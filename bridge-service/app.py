import json
import logging
import os
import threading
import time

from flask import Flask, Response, jsonify, request
import serial
from serial import SerialException

from commands import (
    MUTE_OFF,
    MUTE_ON,
    POWER_OFF,
    POWER_ON,
    QUERY,
    VOLUME_DOWN,
    VOLUME_SET,
    VOLUME_UP,
)

APP_HOST = os.getenv("BRIDGE_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("BRIDGE_PORT", "5000"))

SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "115200"))
RECONNECT_INTERVAL = float(os.getenv("RECONNECT_INTERVAL", "2.0"))
HOLD_INTERVAL = float(os.getenv("HOLD_INTERVAL", "0.12"))
QUERY_INTERVAL = float(os.getenv("QUERY_INTERVAL", "5.0"))
QUERY_ON_CONNECT = os.getenv("QUERY_ON_CONNECT", "1") != "0"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class SerialManager:
    def __init__(self, port, baud, reconnect_interval, line_handler=None, on_connect=None):
        self._port = port
        self._baud = baud
        self._reconnect_interval = reconnect_interval
        self._lock = threading.Lock()
        self._serial = None
        self._stop_event = threading.Event()
        self._line_handler = line_handler
        self._on_connect = on_connect
        self._thread = threading.Thread(target=self._io_loop, daemon=True)
        self._thread.start()

    def _io_loop(self):
        while not self._stop_event.is_set():
            if not self._is_connected():
                if self._connect():
                    continue
                time.sleep(self._reconnect_interval)
                continue

            ser = self._get_serial()
            if ser is None:
                time.sleep(0.2)
                continue

            try:
                line = ser.readline()
            except SerialException as exc:
                logging.warning("Serial read failed: %s", exc)
                self._close()
                time.sleep(self._reconnect_interval)
                continue

            if line:
                self._dispatch_line(line)

    def _is_connected(self):
        with self._lock:
            return self._serial is not None and self._serial.is_open

    def _get_serial(self):
        with self._lock:
            return self._serial

    def _dispatch_line(self, line):
        if not self._line_handler:
            return
        try:
            self._line_handler(line)
        except Exception as exc:
            logging.warning("Serial line handler failed: %s", exc)

    def _connect(self):
        try:
            ser = serial.Serial(
                port=self._port,
                baudrate=self._baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1,
                write_timeout=1,
            )
        except SerialException as exc:
            logging.warning("Serial connect failed: %s", exc)
            return False

        with self._lock:
            self._serial = ser
        logging.info("Serial connected on %s", self._port)

        if self._on_connect:
            try:
                self._on_connect()
            except Exception as exc:
                logging.warning("Serial on_connect failed: %s", exc)
        return True

    def write(self, command):
        data = (command + "\r\n").encode("ascii", errors="ignore")
        with self._lock:
            ser = self._serial
        if ser is None or not ser.is_open:
            raise SerialException("Serial not connected")
        try:
            ser.write(data)
            ser.flush()
        except SerialException:
            self._close()
            raise

    def _close(self):
        with self._lock:
            ser = self._serial
            self._serial = None
        if ser is not None:
            try:
                ser.close()
            except SerialException:
                pass

    def stop(self):
        self._stop_event.set()
        self._close()


class HoldController:
    def __init__(self, send_func, interval):
        self._send = send_func
        self._interval = interval
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = None
        self._direction = None

    def start(self, direction):
        if direction not in ("up", "down"):
            raise ValueError("dir must be 'up' or 'down'")
        with self._lock:
            if self._thread and self._thread.is_alive():
                if self._direction == direction:
                    return
                self._stop_locked()
            self._stop_event = threading.Event()
            self._direction = direction
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def _loop(self):
        command = VOLUME_UP if self._direction == "up" else VOLUME_DOWN
        while not self._stop_event.is_set():
            try:
                self._send(command)
            except Exception as exc:
                logging.warning("Hold send failed: %s", exc)
            self._stop_event.wait(self._interval)

    def stop(self):
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        if not self._stop_event:
            return
        self._stop_event.set()
        thread = self._thread
        self._thread = None
        self._stop_event = None
        if thread:
            thread.join(timeout=0.3)


class QueryPoller:
    def __init__(self, send_func, interval, send_immediately=True):
        self._send = send_func
        self._interval = interval
        self._send_immediately = send_immediately
        self._stop_event = threading.Event()
        self._thread = None
        if self._interval > 0:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def _loop(self):
        initial_delay = 0.5 if self._send_immediately else self._interval
        self._stop_event.wait(initial_delay)
        while not self._stop_event.is_set():
            try:
                self._send(QUERY)
            except Exception as exc:
                logging.debug("Query send failed: %s", exc)
            self._stop_event.wait(self._interval)

    def stop(self):
        if not self._thread:
            return
        self._stop_event.set()


class StateCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._volume = 0
        self._mute = False
        self._power = None
        self._updated_at = 0.0

    def set_volume(self, level):
        with self._lock:
            self._volume = int(level)
            self._updated_at = time.time()

    def get_volume(self):
        with self._lock:
            return int(self._volume)

    def set_mute(self, muted):
        with self._lock:
            self._mute = bool(muted)
            self._updated_at = time.time()

    def get_mute(self):
        with self._lock:
            return bool(self._mute)

    def set_power(self, is_on):
        with self._lock:
            self._power = bool(is_on)
            self._updated_at = time.time()

    def get_power(self):
        with self._lock:
            return self._power

    def snapshot(self):
        with self._lock:
            return {
                "volume": int(self._volume),
                "mute": bool(self._mute),
                "power": self._power,
                "updated_at": self._updated_at,
            }


state_cache = StateCache()

def handle_serial_line(raw_line):
    try:
        text = raw_line.decode("ascii", errors="ignore").strip()
    except Exception:
        return
    if not text:
        return
    if not (text.startswith("(") and text.endswith(")")):
        logging.debug("Serial line ignored: %s", text)
        return
    body = text[1:-1].strip()
    if not body:
        return
    parts = body.split()
    cmd = parts[0].upper()

    if cmd == "VST" and len(parts) >= 3:
        try:
            level = int(parts[2])
        except ValueError:
            logging.debug("Invalid volume status: %s", text)
            return
        level = max(0, min(100, level))
        state_cache.set_volume(level)
        return

    if cmd == "MUT" and len(parts) >= 3:
        if parts[2] in ("0", "1"):
            state_cache.set_mute(parts[2] == "1")
        else:
            logging.debug("Invalid mute status: %s", text)
        return

    if cmd == "PON":
        state_cache.set_power(True)
        return

    if cmd == "POF":
        state_cache.set_power(False)
        return

    if cmd == "ERR":
        logging.warning("Device error: %s", text)
        return


serial_manager = SerialManager(
    SERIAL_PORT,
    SERIAL_BAUD,
    RECONNECT_INTERVAL,
    line_handler=handle_serial_line,
)
query_poller = QueryPoller(serial_manager.write, QUERY_INTERVAL, send_immediately=QUERY_ON_CONNECT)

app = Flask(__name__)


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify(ok=True)


@app.route("/power/on", methods=["POST"])
def power_on():
    try:
        serial_manager.write(POWER_ON)
        time.sleep(0.1)
        serial_manager.write(POWER_ON)
    except SerialException as exc:
        return jsonify(error=str(exc)), 503
    state_cache.set_power(True)
    return Response(status=204)


@app.route("/power/off", methods=["POST"])
def power_off():
    try:
        serial_manager.write(POWER_OFF)
    except SerialException as exc:
        return jsonify(error=str(exc)), 503
    state_cache.set_power(False)
    return Response(status=204)


@app.route("/mute/on", methods=["POST"])
def mute_on():
    try:
        serial_manager.write(MUTE_ON)
    except SerialException as exc:
        return jsonify(error=str(exc)), 503
    state_cache.set_mute(True)
    return Response(status=204)


@app.route("/mute/off", methods=["POST"])
def mute_off():
    try:
        serial_manager.write(MUTE_OFF)
    except SerialException as exc:
        return jsonify(error=str(exc)), 503
    state_cache.set_mute(False)
    return Response(status=204)


@app.route("/power", methods=["GET"])
def power_get():
    return jsonify(on=state_cache.get_power())


@app.route("/mute", methods=["GET"])
def mute_get():
    return jsonify(muted=state_cache.get_mute())


@app.route("/volume/set", methods=["POST"])
def volume_set():
    level = request.args.get("level")
    if level is None:
        data = request.get_json(silent=True) or {}
        level = data.get("level")
    try:
        level_int = int(level)
    except (TypeError, ValueError):
        return jsonify(error="level must be int 0..100"), 400
    level_int = max(0, min(100, level_int))
    try:
        serial_manager.write(VOLUME_SET.format(level=level_int))
    except SerialException as exc:
        return jsonify(error=str(exc)), 503
    state_cache.set_volume(level_int)
    return jsonify(level=level_int)


@app.route("/volume", methods=["GET"])
def volume_get():
    return jsonify(level=state_cache.get_volume())


@app.route("/volume/lvl", methods=["GET"])
def volume_lvl():
    return Response(str(state_cache.get_volume()), mimetype="text/plain")


@app.route("/state", methods=["GET"])
def state_get():
    return jsonify(state_cache.snapshot())


hold_controller = HoldController(serial_manager.write, HOLD_INTERVAL)


@app.route("/hold/start", methods=["POST"])
def hold_start():
    data = request.get_json(silent=True) or {}
    direction = data.get("dir")
    if direction not in ("up", "down"):
        return jsonify(error="dir must be 'up' or 'down'"), 400
    try:
        hold_controller.start(direction)
    except SerialException as exc:
        return jsonify(error=str(exc)), 503
    return Response(status=204)


@app.route("/hold/stop", methods=["POST"])
def hold_stop():
    hold_controller.stop()
    return Response(status=204)


@app.route("/", methods=["GET"])
def root():
    return Response(
        json.dumps({"ok": True, "service": "ma352-bridge"}),
        mimetype="application/json",
    )


if __name__ == "__main__":
    app.run(host=APP_HOST, port=APP_PORT)
