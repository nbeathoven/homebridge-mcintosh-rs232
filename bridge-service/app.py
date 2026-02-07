import json
import logging
import os
import re
import threading
import time

from flask import Flask, Response, jsonify, request
import serial
from serial import SerialException

from commands import (
    HELP,
    INPUT_SET_SHORT,
    INPUT_SET_ZONE,
    MUTE_OFF_SHORT,
    MUTE_OFF_ZONE,
    MUTE_ON_SHORT,
    MUTE_ON_ZONE,
    POWER_OFF_SHORT,
    POWER_OFF_ZONE,
    POWER_ON_SHORT,
    POWER_ON_ZONE,
    QUERY,
    VOLUME_SET_SHORT,
    VOLUME_SET_ZONE,
)

APP_HOST = os.getenv("BRIDGE_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("BRIDGE_PORT", "5000"))

SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "115200"))
RECONNECT_INTERVAL = float(os.getenv("RECONNECT_INTERVAL", "2.0"))
HOLD_INTERVAL = float(os.getenv("HOLD_INTERVAL", "0.12"))
QUERY_INTERVAL = float(os.getenv("QUERY_INTERVAL", "5.0"))
QUERY_ON_CONNECT = os.getenv("QUERY_ON_CONNECT", "1") != "0"
COMMAND_STYLE = os.getenv("COMMAND_STYLE", "auto").lower()
DEFAULT_COMMAND_STYLE = os.getenv("DEFAULT_COMMAND_STYLE", "short").lower()
COMMAND_ZONE = os.getenv("COMMAND_ZONE", "Z1")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class CommandMode:
    def __init__(self, mode, default_style, zone):
        self._mode = mode if mode in ("short", "zone", "auto") else "auto"
        self._default_style = default_style if default_style in ("short", "zone") else "short"
        self._zone = zone
        self._lock = threading.Lock()
        self._detected = None
        self._last_invalid_time = 0.0

    def zone(self):
        return self._zone

    def style(self):
        with self._lock:
            if self._mode != "auto":
                return self._mode
            return self._detected or self._default_style

    def is_auto(self):
        return self._mode == "auto"

    def is_detected(self):
        with self._lock:
            return self._detected is not None

    def detect_from_parts(self, parts):
        if not parts:
            return
        with self._lock:
            if self._mode != "auto" or self._detected is not None:
                return
            cmd = parts[0].upper()
            if cmd in ("PWR", "VOL"):
                self._detected = "short"
                logging.info("Detected short-form command mode from device replies.")
                return
            if cmd in ("PON", "POF", "VST"):
                self._detected = "zone"
                logging.info("Detected zone-form command mode from device replies.")
                return
            if len(parts) >= 2 and parts[1].upper().startswith("Z"):
                self._detected = "zone"
                logging.info("Detected zone-form command mode from device replies.")

    def mark_invalid(self):
        with self._lock:
            self._last_invalid_time = time.time()

    def invalid_after(self, ts):
        with self._lock:
            return self._last_invalid_time > ts

    def note_fallback(self, style):
        with self._lock:
            if self._mode == "auto":
                self._detected = style
                logging.info("Switching to %s-form command mode after invalid command.", style)


command_mode = CommandMode(COMMAND_STYLE, DEFAULT_COMMAND_STYLE, COMMAND_ZONE)


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
            try:
                if not self._is_connected():
                    if self._connect():
                        continue
                    self._stop_event.wait(self._reconnect_interval)
                    continue

                ser = self._get_serial()
                if ser is None:
                    self._stop_event.wait(0.2)
                    continue

                try:
                    line = ser.readline()
                except SerialException as exc:
                    logging.warning("Serial read failed: %s", exc)
                    self._close()
                    self._stop_event.wait(self._reconnect_interval)
                    continue
                except Exception as exc:
                    logging.exception("Serial read unexpected error: %s", exc)
                    self._close()
                    self._stop_event.wait(self._reconnect_interval)
                    continue

                if line:
                    self._dispatch_line(line)
            except Exception as exc:
                logging.exception("Serial loop error: %s", exc)
                self._close()
                self._stop_event.wait(self._reconnect_interval)

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
    def __init__(self, send_func, get_level_func, set_level_func, interval):
        self._send = send_func
        self._get_level = get_level_func
        self._set_level = set_level_func
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
        step = 1 if self._direction == "up" else -1
        while not self._stop_event.is_set():
            try:
                current = self._get_level()
                next_level = max(0, min(50, current + step))
                short_cmd = build_volume_set("short", next_level)
                zone_cmd = build_volume_set("zone", next_level)
                send_with_fallback(short_cmd, zone_cmd)
                self._set_level(next_level)
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
        self._input = None
        self._model = None
        self._serial_number = None
        self._firmware = None
        self._updated_at = 0.0

    def set_volume(self, level):
        with self._lock:
            clamped = max(0, min(50, int(level)))
            self._volume = clamped
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

    def set_input(self, value):
        with self._lock:
            self._input = int(value)
            self._updated_at = time.time()

    def get_input(self):
        with self._lock:
            return self._input

    def set_model(self, value):
        with self._lock:
            self._model = value
            self._updated_at = time.time()

    def get_model(self):
        with self._lock:
            return self._model

    def set_serial_number(self, value):
        with self._lock:
            self._serial_number = value
            self._updated_at = time.time()

    def get_serial_number(self):
        with self._lock:
            return self._serial_number

    def set_firmware(self, value):
        with self._lock:
            self._firmware = value
            self._updated_at = time.time()

    def get_firmware(self):
        with self._lock:
            return self._firmware

    def snapshot(self):
        with self._lock:
            return {
                "volume": int(self._volume),
                "mute": bool(self._mute),
                "power": self._power,
                "input": self._input,
                "model": self._model,
                "serial_number": self._serial_number,
                "firmware": self._firmware,
                "updated_at": self._updated_at,
            }

class LineBuffer:
    def __init__(self, max_lines=200):
        self._lock = threading.Lock()
        self._lines = []
        self._max_lines = max_lines

    def add(self, line):
        with self._lock:
            self._lines.append(line)
            if len(self._lines) > self._max_lines:
                self._lines = self._lines[-self._max_lines:]

    def clear(self):
        with self._lock:
            self._lines = []

    def snapshot(self):
        with self._lock:
            return list(self._lines)

def build_power_on(style):
    return POWER_ON_SHORT if style == "short" else POWER_ON_ZONE.format(zone=command_mode.zone())


def build_power_off(style):
    return POWER_OFF_SHORT if style == "short" else POWER_OFF_ZONE.format(zone=command_mode.zone())


def build_mute_on(style):
    return MUTE_ON_SHORT if style == "short" else MUTE_ON_ZONE.format(zone=command_mode.zone())


def build_mute_off(style):
    return MUTE_OFF_SHORT if style == "short" else MUTE_OFF_ZONE.format(zone=command_mode.zone())


def build_volume_set(style, level):
    clamped = max(0, min(50, int(level)))
    if style == "short":
        return VOLUME_SET_SHORT.format(level=clamped)
    return VOLUME_SET_ZONE.format(zone=command_mode.zone(), level=clamped)


def build_input_set(style, value):
    if style == "short":
        return INPUT_SET_SHORT.format(value=value)
    return INPUT_SET_ZONE.format(zone=command_mode.zone(), value=value)


def send_with_fallback(short_cmd, zone_cmd):
    style = command_mode.style()
    cmd = short_cmd if style == "short" else zone_cmd
    send_time = time.time()
    serial_manager.write(cmd)
    if command_mode.is_auto() and not command_mode.is_detected():
        time.sleep(0.15)
        if command_mode.invalid_after(send_time):
            fallback_style = "zone" if style == "short" else "short"
            fallback_cmd = zone_cmd if fallback_style == "zone" else short_cmd
            serial_manager.write(fallback_cmd)
            command_mode.note_fallback(fallback_style)


state_cache = StateCache()
line_buffer = LineBuffer()

def handle_serial_line(raw_line):
    try:
        text = raw_line.decode("ascii", errors="ignore").strip()
    except Exception:
        return
    if not text:
        return
    cleaned = text.replace("\x00", "").strip()
    if not cleaned:
        return
    messages = re.findall(r"\([^)]*\)", cleaned)
    if not messages:
        logging.debug("Serial line ignored: %s", cleaned)
        return

    for message in messages:
        line_buffer.add(message)
        body = message[1:-1].strip()
        if not body:
            continue

        upper_body = body.upper()
        if "ERROR" in upper_body and "INVALID COMMAND" in upper_body:
            command_mode.mark_invalid()
            logging.warning("Device error: %s", message)
            continue
        if upper_body.startswith("SERIAL NUMBER"):
            parts = body.split(":", 1)
            if len(parts) == 2:
                state_cache.set_serial_number(parts[1].strip())
            continue

        if upper_body.startswith("FW VERSION"):
            parts = body.split(":", 1)
            if len(parts) == 2:
                state_cache.set_firmware(parts[1].strip())
            continue

        if body == "MA352":
            state_cache.set_model(body)
            continue

        parts = body.split()
        cmd = parts[0].upper()
        command_mode.detect_from_parts(parts)

        if cmd == "VST" and len(parts) >= 3:
            try:
                level = int(parts[2])
            except ValueError:
                logging.debug("Invalid volume status: %s", text)
                continue
            level = max(0, min(50, level))
            state_cache.set_volume(level)
            continue

        if cmd == "VOL" and len(parts) >= 2:
            try:
                level = int(parts[1])
            except ValueError:
                logging.debug("Invalid volume status: %s", text)
                continue
            level = max(0, min(50, level))
            state_cache.set_volume(level)
            continue

        if cmd == "MUT" and len(parts) >= 2:
            value = parts[-1]
            if value in ("0", "1"):
                state_cache.set_mute(value == "1")
            else:
                logging.debug("Invalid mute status: %s", text)
            continue

        if cmd == "PON":
            state_cache.set_power(True)
            continue

        if cmd == "POF":
            state_cache.set_power(False)
            continue

        if cmd == "PWR" and len(parts) >= 2:
            value = parts[-1]
            if value in ("0", "1"):
                state_cache.set_power(value == "1")
            else:
                logging.debug("Invalid power status: %s", text)
            continue

        if cmd == "INP" and len(parts) >= 2:
            try:
                value = int(parts[-1])
            except ValueError:
                logging.debug("Invalid input status: %s", text)
                continue
            if 1 <= value <= 9:
                state_cache.set_input(value)
            else:
                logging.debug("Input out of range: %s", text)
            continue

        if cmd == "ERR":
            logging.warning("Device error: %s", text)
            continue


serial_manager = SerialManager(
    SERIAL_PORT,
    SERIAL_BAUD,
    RECONNECT_INTERVAL,
    line_handler=handle_serial_line,
)
query_poller = QueryPoller(serial_manager.write, QUERY_INTERVAL, send_immediately=QUERY_ON_CONNECT)

app = Flask(__name__)

def query_help_lines(timeout=1.0):
    line_buffer.clear()
    serial_manager.write(HELP)
    time.sleep(timeout)
    lines = line_buffer.snapshot()
    if lines:
        upper_lines = [line.upper() for line in lines]
        if any("INVALID COMMAND" in line for line in upper_lines):
            lines = []
    if not lines:
        line_buffer.clear()
        serial_manager.write(QUERY)
        time.sleep(timeout)
        lines = line_buffer.snapshot()
    return lines


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify(ok=True)


@app.route("/power/on", methods=["POST"])
def power_on():
    try:
        short_cmd = build_power_on("short")
        zone_cmd = build_power_on("zone")
        send_with_fallback(short_cmd, zone_cmd)
        time.sleep(0.1)
        style = command_mode.style()
        serial_manager.write(short_cmd if style == "short" else zone_cmd)
    except SerialException as exc:
        return jsonify(error=str(exc)), 503
    state_cache.set_power(True)
    return Response(status=204)


@app.route("/power/off", methods=["POST"])
def power_off():
    try:
        short_cmd = build_power_off("short")
        zone_cmd = build_power_off("zone")
        send_with_fallback(short_cmd, zone_cmd)
    except SerialException as exc:
        return jsonify(error=str(exc)), 503
    state_cache.set_power(False)
    return Response(status=204)


@app.route("/mute/on", methods=["POST"])
def mute_on():
    try:
        short_cmd = build_mute_on("short")
        zone_cmd = build_mute_on("zone")
        send_with_fallback(short_cmd, zone_cmd)
    except SerialException as exc:
        return jsonify(error=str(exc)), 503
    state_cache.set_mute(True)
    return Response(status=204)


@app.route("/mute/off", methods=["POST"])
def mute_off():
    try:
        short_cmd = build_mute_off("short")
        zone_cmd = build_mute_off("zone")
        send_with_fallback(short_cmd, zone_cmd)
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
        return jsonify(error="level must be int 0..50"), 400
    if not (0 <= level_int <= 50):
        return jsonify(error="level must be int 0..50"), 400
    current = state_cache.get_volume()
    if abs(level_int - current) > 10:
        return jsonify(error="level change must be <= 10"), 400
    try:
        short_cmd = build_volume_set("short", level_int)
        zone_cmd = build_volume_set("zone", level_int)
        send_with_fallback(short_cmd, zone_cmd)
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

@app.route("/input/set", methods=["POST"])
def input_set():
    value = request.args.get("value")
    if value is None:
        data = request.get_json(silent=True) or {}
        value = data.get("value")
    try:
        value_int = int(value)
    except (TypeError, ValueError):
        return jsonify(error="value must be int 1..9"), 400
    if not (1 <= value_int <= 9):
        return jsonify(error="value must be int 1..9"), 400
    try:
        short_cmd = build_input_set("short", value_int)
        zone_cmd = build_input_set("zone", value_int)
        send_with_fallback(short_cmd, zone_cmd)
    except SerialException as exc:
        return jsonify(error=str(exc)), 503
    state_cache.set_input(value_int)
    return jsonify(value=value_int)


@app.route("/input", methods=["GET"])
def input_get():
    return jsonify(value=state_cache.get_input())

@app.route("/help", methods=["GET"])
def help_get():
    try:
        timeout = request.args.get("timeout")
        if timeout is None:
            timeout_val = 1.0
        else:
            timeout_val = max(0.2, min(5.0, float(timeout)))
        lines = query_help_lines(timeout_val)
    except SerialException as exc:
        return jsonify(error=str(exc)), 503
    except ValueError:
        return jsonify(error="timeout must be a number"), 400
    return jsonify(lines=lines)


@app.route("/firmware", methods=["GET"])
def firmware_get():
    try:
        timeout = request.args.get("timeout")
        if timeout is None:
            timeout_val = 1.0
        else:
            timeout_val = max(0.2, min(5.0, float(timeout)))
        lines = query_help_lines(timeout_val)
    except SerialException as exc:
        return jsonify(error=str(exc)), 503
    except ValueError:
        return jsonify(error="timeout must be a number"), 400
    cached = state_cache.get_firmware()
    if cached:
        return jsonify(version=cached)
    for line in lines:
        if "FW Version" in line:
            cleaned = line.strip("()")
            parts = cleaned.split("FW Version:", 1)
            if len(parts) == 2:
                return jsonify(version=parts[1].strip())
    return jsonify(version=None, lines=lines)


@app.route("/state", methods=["GET"])
def state_get():
    return jsonify(state_cache.snapshot())


hold_controller = HoldController(
    serial_manager.write,
    state_cache.get_volume,
    state_cache.set_volume,
    HOLD_INTERVAL,
)


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
