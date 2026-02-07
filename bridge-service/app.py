# Standard library imports
import json
import logging
import os
import re
import threading
import time

# Third-party imports
from flask import Flask, Response, jsonify, request
import serial
from serial import SerialException

# Command definitions
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

# Configuration (env overrides supported)
APP_HOST = os.getenv("BRIDGE_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("BRIDGE_PORT", "5000"))

SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "115200"))
RECONNECT_INTERVAL = float(os.getenv("RECONNECT_INTERVAL", "2.0"))
HOLD_INTERVAL = float(os.getenv("HOLD_INTERVAL", "0.12"))
QUERY_INTERVAL = float(os.getenv("QUERY_INTERVAL", "5.0"))
QUERY_ON_CONNECT = os.getenv("QUERY_ON_CONNECT", "1") != "0"
SERIAL_STALE_TIMEOUT = float(os.getenv("SERIAL_STALE_TIMEOUT", "30.0"))
SERIAL_WATCHDOG_INTERVAL = float(os.getenv("SERIAL_WATCHDOG_INTERVAL", "2.0"))
COMMAND_STYLE = os.getenv("COMMAND_STYLE", "auto").lower()
DEFAULT_COMMAND_STYLE = os.getenv("DEFAULT_COMMAND_STYLE", "short").lower()
COMMAND_ZONE = os.getenv("COMMAND_ZONE", "Z1")

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# Command mode detection and selection
class CommandMode:
    """Track and auto-detect whether the device expects short or zone commands."""
    def __init__(self, mode, default_style, zone):
        """Initialize command mode configuration and detection state."""
        self._mode = mode if mode in ("short", "zone", "auto") else "auto"
        self._default_style = default_style if default_style in ("short", "zone") else "short"
        self._zone = zone
        self._lock = threading.Lock()
        self._detected = None
        self._last_invalid_time = 0.0

    def zone(self):
        """Return the configured zone identifier."""
        return self._zone

    def style(self):
        """Return the effective command style based on config/detection."""
        with self._lock:
            if self._mode != "auto":
                return self._mode
            return self._detected or self._default_style

    def is_auto(self):
        """Return True when auto-detection mode is enabled."""
        return self._mode == "auto"

    def is_detected(self):
        """Return True if a command style has been detected."""
        with self._lock:
            return self._detected is not None

    def detect_from_parts(self, parts):
        """Infer command style from a parsed response line."""
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
        """Mark that an invalid-command error was observed."""
        with self._lock:
            self._last_invalid_time = time.time()

    def invalid_after(self, ts):
        """Return True if an invalid-command error occurred after a timestamp."""
        with self._lock:
            return self._last_invalid_time > ts

    def note_fallback(self, style):
        """Record a fallback style selected after invalid-command detection."""
        with self._lock:
            if self._mode == "auto":
                self._detected = style
                logging.info("Switching to %s-form command mode after invalid command.", style)


command_mode = CommandMode(COMMAND_STYLE, DEFAULT_COMMAND_STYLE, COMMAND_ZONE)


# Serial connection manager (I/O loop + reconnects + health info)
class SerialManager:
    """Manage the serial connection, I/O loop, and health tracking."""
    def __init__(self, port, baud, reconnect_interval, line_handler=None, on_connect=None):
        """Start the serial I/O thread and configure handlers."""
        self._port = port
        self._baud = baud
        self._reconnect_interval = reconnect_interval
        self._lock = threading.Lock()
        self._serial = None
        self._last_rx_time = 0.0
        self._last_connect_time = 0.0
        self._last_error_time = 0.0
        self._last_error = None
        self._stop_event = threading.Event()
        self._line_handler = line_handler
        self._on_connect = on_connect
        self._thread = threading.Thread(target=self._io_loop, daemon=True)
        self._thread.start()

    def _io_loop(self):
        """Main serial read loop with reconnect and error recovery."""
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
                    self._record_error("Serial read failed: %s" % exc)
                    self._close()
                    self._stop_event.wait(self._reconnect_interval)
                    continue
                except Exception as exc:
                    logging.exception("Serial read unexpected error: %s", exc)
                    self._record_error("Serial read unexpected error: %s" % exc)
                    self._close()
                    self._stop_event.wait(self._reconnect_interval)
                    continue

                if line:
                    self._dispatch_line(line)
            except Exception as exc:
                logging.exception("Serial loop error: %s", exc)
                self._record_error("Serial loop error: %s" % exc)
                    self._close()
                    self._stop_event.wait(self._reconnect_interval)

    def _is_connected(self):
        """Return True if the serial port is open."""
        with self._lock:
            return self._serial is not None and self._serial.is_open

    def _get_serial(self):
        """Return the current serial object (may be None)."""
        with self._lock:
            return self._serial

    def _dispatch_line(self, line):
        """Dispatch a raw line to the configured line handler."""
        with self._lock:
            self._last_rx_time = time.time()
        if not self._line_handler:
            return
        try:
            self._line_handler(line)
        except Exception as exc:
            logging.warning("Serial line handler failed: %s", exc)

    def _connect(self):
        """Open the serial port and invoke on_connect when successful."""
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
            self._record_error("Serial connect failed: %s" % exc)
            return False

        with self._lock:
            self._serial = ser
            self._last_connect_time = time.time()
        logging.info("Serial connected on %s", self._port)

        if self._on_connect:
            try:
                self._on_connect()
            except Exception as exc:
                logging.warning("Serial on_connect failed: %s", exc)
        return True

    def write(self, command):
        """Write a command to the serial port."""
        data = (command + "\r\n").encode("ascii", errors="ignore")
        with self._lock:
            ser = self._serial
        if ser is None or not ser.is_open:
            self._record_error("Serial not connected")
            raise SerialException("Serial not connected")
        try:
            ser.write(data)
            ser.flush()
        except SerialException:
            self._record_error("Serial write failed")
            self._close()
            raise

    def _close(self):
        """Close the serial port if open."""
        with self._lock:
            ser = self._serial
            self._serial = None
        if ser is not None:
            try:
                ser.close()
            except SerialException:
                pass

    def stop(self):
        """Stop the serial I/O thread and close the port."""
        self._stop_event.set()
        self._close()

    def _record_error(self, message):
        """Record the latest serial error for health reporting."""
        with self._lock:
            self._last_error_time = time.time()
            self._last_error = message

    def force_reconnect(self, reason):
        """Force a reconnect cycle, recording the reason."""
        self._record_error("Forced reconnect: %s" % reason)
        self._close()

    def health_snapshot(self):
        """Return a dict of current serial health metrics."""
        with self._lock:
            connected = self._serial is not None and self._serial.is_open
            return {
                "connected": connected,
                "port": self._port,
                "baud": self._baud,
                "last_rx_time": self._last_rx_time,
                "last_connect_time": self._last_connect_time,
                "last_error_time": self._last_error_time,
                "last_error": self._last_error,
            }


# Serial watchdog to force reconnect when the line is stale
class SerialWatchdog:
    """Monitor serial activity and trigger reconnect on stale link."""
    def __init__(self, manager, stale_timeout, interval):
        """Start the watchdog thread with configured thresholds."""
        self._manager = manager
        self._stale_timeout = stale_timeout
        self._interval = interval
        self._stop_event = threading.Event()
        self._thread = None
        if self._stale_timeout > 0:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def _loop(self):
        """Periodically check for stale serial activity."""
        while not self._stop_event.is_set():
            snapshot = self._manager.health_snapshot()
            if snapshot["connected"] and self._stale_timeout > 0:
                last_rx = snapshot["last_rx_time"]
                if last_rx > 0:
                    age = time.time() - last_rx
                    if age > self._stale_timeout:
                        logging.warning("Serial stale for %.1fs, forcing reconnect.", age)
                        self._manager.force_reconnect("stale for %.1fs" % age)
            self._stop_event.wait(self._interval)

    def stop(self):
        """Stop the watchdog thread."""
        if not self._thread:
            return
        self._stop_event.set()


# Hold controller for press-and-hold volume changes
class HoldController:
    """Handle press-and-hold volume changes with repeated commands."""
    def __init__(self, send_func, get_level_func, set_level_func, interval):
        """Configure hold behavior and start state."""
        self._send = send_func
        self._get_level = get_level_func
        self._set_level = set_level_func
        self._interval = interval
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = None
        self._direction = None

    def start(self, direction):
        """Start or switch a hold loop in the given direction."""
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
        """Issue repeated volume steps until stopped."""
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
        """Stop any active hold loop."""
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        """Stop the hold loop without releasing the outer lock."""
        if not self._stop_event:
            return
        self._stop_event.set()
        thread = self._thread
        self._thread = None
        self._stop_event = None
        if thread:
            thread.join(timeout=0.3)


# Periodic status query poller
class QueryPoller:
    """Periodically send QUERY commands to refresh device state."""
    def __init__(self, send_func, interval, send_immediately=True):
        """Start the polling thread when interval is positive."""
        self._send = send_func
        self._interval = interval
        self._send_immediately = send_immediately
        self._stop_event = threading.Event()
        self._thread = None
        if self._interval > 0:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def _loop(self):
        """Issue periodic QUERY commands."""
        initial_delay = 0.5 if self._send_immediately else self._interval
        self._stop_event.wait(initial_delay)
        while not self._stop_event.is_set():
            try:
                self._send(QUERY)
            except Exception as exc:
                logging.debug("Query send failed: %s", exc)
            self._stop_event.wait(self._interval)

    def stop(self):
        """Stop the poller thread."""
        if not self._thread:
            return
        self._stop_event.set()


# In-memory state cache for device status
class StateCache:
    """Thread-safe cache of last known device state."""
    def __init__(self):
        """Initialize cached state defaults."""
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
        """Update cached volume (clamped)."""
        with self._lock:
            clamped = max(0, min(50, int(level)))
            self._volume = clamped
            self._updated_at = time.time()

    def get_volume(self):
        """Return cached volume."""
        with self._lock:
            return int(self._volume)

    def set_mute(self, muted):
        """Update cached mute state."""
        with self._lock:
            self._mute = bool(muted)
            self._updated_at = time.time()

    def get_mute(self):
        """Return cached mute state."""
        with self._lock:
            return bool(self._mute)

    def set_power(self, is_on):
        """Update cached power state."""
        with self._lock:
            self._power = bool(is_on)
            self._updated_at = time.time()

    def get_power(self):
        """Return cached power state."""
        with self._lock:
            return self._power

    def set_input(self, value):
        """Update cached input value."""
        with self._lock:
            self._input = int(value)
            self._updated_at = time.time()

    def get_input(self):
        """Return cached input value."""
        with self._lock:
            return self._input

    def set_model(self, value):
        """Update cached model."""
        with self._lock:
            self._model = value
            self._updated_at = time.time()

    def get_model(self):
        """Return cached model."""
        with self._lock:
            return self._model

    def set_serial_number(self, value):
        """Update cached serial number."""
        with self._lock:
            self._serial_number = value
            self._updated_at = time.time()

    def get_serial_number(self):
        """Return cached serial number."""
        with self._lock:
            return self._serial_number

    def set_firmware(self, value):
        """Update cached firmware version."""
        with self._lock:
            self._firmware = value
            self._updated_at = time.time()

    def get_firmware(self):
        """Return cached firmware version."""
        with self._lock:
            return self._firmware

    def snapshot(self):
        """Return a snapshot of the cached state."""
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

# Buffer of recent serial messages for diagnostics
class LineBuffer:
    """Bounded list of recent serial messages for diagnostics."""
    def __init__(self, max_lines=200):
        """Initialize the line buffer with a max size."""
        self._lock = threading.Lock()
        self._lines = []
        self._max_lines = max_lines

    def add(self, line):
        """Add a line, trimming to the max buffer size."""
        with self._lock:
            self._lines.append(line)
            if len(self._lines) > self._max_lines:
                self._lines = self._lines[-self._max_lines:]

    def clear(self):
        """Clear all buffered lines."""
        with self._lock:
            self._lines = []

    def snapshot(self):
        """Return a copy of the buffered lines."""
        with self._lock:
            return list(self._lines)

# Command builders (short vs zone)
def build_power_on(style):
    """Build the power-on command for the given style."""
    return POWER_ON_SHORT if style == "short" else POWER_ON_ZONE.format(zone=command_mode.zone())


def build_power_off(style):
    """Build the power-off command for the given style."""
    return POWER_OFF_SHORT if style == "short" else POWER_OFF_ZONE.format(zone=command_mode.zone())


def build_mute_on(style):
    """Build the mute-on command for the given style."""
    return MUTE_ON_SHORT if style == "short" else MUTE_ON_ZONE.format(zone=command_mode.zone())


def build_mute_off(style):
    """Build the mute-off command for the given style."""
    return MUTE_OFF_SHORT if style == "short" else MUTE_OFF_ZONE.format(zone=command_mode.zone())


def build_volume_set(style, level):
    """Build the volume set command with clamped level."""
    clamped = max(0, min(50, int(level)))
    if style == "short":
        return VOLUME_SET_SHORT.format(level=clamped)
    return VOLUME_SET_ZONE.format(zone=command_mode.zone(), level=clamped)


def build_input_set(style, value):
    """Build the input set command for the given style."""
    if style == "short":
        return INPUT_SET_SHORT.format(value=value)
    return INPUT_SET_ZONE.format(zone=command_mode.zone(), value=value)


# Send with auto-detect fallback for short/zone mode
def send_with_fallback(short_cmd, zone_cmd):
    """Send a command with auto-detect fallback for command style."""
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


# Shared singletons
state_cache = StateCache()
line_buffer = LineBuffer()

# Serial line parsing -> update cache + detect mode
def handle_serial_line(raw_line):
    """Parse a raw serial line and update cached state."""
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


# Serial + polling + watchdog infrastructure
serial_manager = SerialManager(
    SERIAL_PORT,
    SERIAL_BAUD,
    RECONNECT_INTERVAL,
    line_handler=handle_serial_line,
)
query_poller = QueryPoller(serial_manager.write, QUERY_INTERVAL, send_immediately=QUERY_ON_CONNECT)
serial_watchdog = SerialWatchdog(serial_manager, SERIAL_STALE_TIMEOUT, SERIAL_WATCHDOG_INTERVAL)

# Flask app + HTTP routes
app = Flask(__name__)

# Helper to probe device help/firmware output
def query_help_lines(timeout=1.0):
    """Request help or query output and return captured lines."""
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
    """Simple liveness check for the HTTP service."""
    return jsonify(ok=True)


# Health and diagnostics
@app.route("/health", methods=["GET"])
def health():
    """Return serial health metrics and watchdog settings."""
    snapshot = serial_manager.health_snapshot()
    now = time.time()
    last_rx_time = snapshot["last_rx_time"]
    last_connect_time = snapshot["last_connect_time"]
    last_error_time = snapshot["last_error_time"]
    return jsonify(
        ok=True,
        serial_connected=snapshot["connected"],
        serial_port=snapshot["port"],
        serial_baud=snapshot["baud"],
        last_rx_time=last_rx_time or None,
        last_rx_age_s=(now - last_rx_time) if last_rx_time else None,
        last_connect_time=last_connect_time or None,
        last_connect_age_s=(now - last_connect_time) if last_connect_time else None,
        last_error_time=last_error_time or None,
        last_error_age_s=(now - last_error_time) if last_error_time else None,
        last_error=snapshot["last_error"],
        watchdog_timeout_s=SERIAL_STALE_TIMEOUT,
        watchdog_interval_s=SERIAL_WATCHDOG_INTERVAL,
        query_interval_s=QUERY_INTERVAL,
    )


# Power endpoints
@app.route("/power/on", methods=["POST"])
def power_on():
    """Turn power on (with command style auto-detect)."""
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
    """Turn power off."""
    try:
        short_cmd = build_power_off("short")
        zone_cmd = build_power_off("zone")
        send_with_fallback(short_cmd, zone_cmd)
    except SerialException as exc:
        return jsonify(error=str(exc)), 503
    state_cache.set_power(False)
    return Response(status=204)


# Mute endpoints
@app.route("/mute/on", methods=["POST"])
def mute_on():
    """Enable mute."""
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
    """Disable mute."""
    try:
        short_cmd = build_mute_off("short")
        zone_cmd = build_mute_off("zone")
        send_with_fallback(short_cmd, zone_cmd)
    except SerialException as exc:
        return jsonify(error=str(exc)), 503
    state_cache.set_mute(False)
    return Response(status=204)


# Read-only state endpoints
@app.route("/power", methods=["GET"])
def power_get():
    """Return cached power state."""
    return jsonify(on=state_cache.get_power())


@app.route("/mute", methods=["GET"])
def mute_get():
    """Return cached mute state."""
    return jsonify(muted=state_cache.get_mute())


# Volume endpoints
@app.route("/volume/set", methods=["POST"])
def volume_set():
    """Set volume with bounds and max-delta enforcement."""
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
    """Return cached volume level."""
    return jsonify(level=state_cache.get_volume())


@app.route("/volume/lvl", methods=["GET"])
def volume_lvl():
    """Return cached volume level as plain text."""
    return Response(str(state_cache.get_volume()), mimetype="text/plain")

# Input endpoints
@app.route("/input/set", methods=["POST"])
def input_set():
    """Set active input."""
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
    """Return cached input value."""
    return jsonify(value=state_cache.get_input())

# Device help/firmware endpoints
@app.route("/help", methods=["GET"])
def help_get():
    """Return help output lines from the device."""
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
    """Return firmware version (cached or parsed from help output)."""
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


# Aggregate state endpoint
@app.route("/state", methods=["GET"])
def state_get():
    """Return the full cached state snapshot."""
    return jsonify(state_cache.snapshot())


# Hold endpoints (press-and-hold volume changes)
hold_controller = HoldController(
    serial_manager.write,
    state_cache.get_volume,
    state_cache.set_volume,
    HOLD_INTERVAL,
)


@app.route("/hold/start", methods=["POST"])
def hold_start():
    """Start a hold loop to step volume up or down."""
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
    """Stop any active hold loop."""
    hold_controller.stop()
    return Response(status=204)


# Root endpoint
@app.route("/", methods=["GET"])
def root():
    """Return a minimal service identity payload."""
    return Response(
        json.dumps({"ok": True, "service": "ma352-bridge"}),
        mimetype="application/json",
    )


# Local dev entrypoint
if __name__ == "__main__":
    app.run(host=APP_HOST, port=APP_PORT)
