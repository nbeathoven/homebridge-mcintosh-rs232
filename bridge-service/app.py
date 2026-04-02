# Standard library imports
import json
import logging
import os
import queue
import re
import socket
import struct
import threading
import time

try:
    import fcntl  # Unix/Linux only (BRIDGE_INTERFACE lookup)
except ImportError:
    fcntl = None

# Third-party imports
from flask import Flask, Response, jsonify, request
import serial
from serial import SerialException

# Command definitions
from commands import (
    HELP,
    HELP_ZONE,
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
    QUERY_ZONE,
    VOLUME_SET_SHORT,
    VOLUME_SET_ZONE,
)

# Configuration (env overrides supported)
def _ipv4_for_interface(ifname):
    """Return IPv4 for a Linux interface name (e.g. eth0), or None on failure."""
    if not ifname or fcntl is None:
        return None
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        ifreq = struct.pack("256s", ifname[:15].encode("utf-8"))
        res = fcntl.ioctl(s.fileno(), 0x8915, ifreq)  # SIOCGIFADDR
        return socket.inet_ntoa(res[20:24])
    except OSError:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass


def resolve_app_host():
    """
    Bind order:
      1) BRIDGE_HOST if set (explicit IP/host)
      2) BRIDGE_INTERFACE IPv4 if set (e.g. eth0 -> 192.168.1.10)
      3) Default to localhost (secure by default)
    """
    host = os.getenv("BRIDGE_HOST", "").strip()
    if host:
        return host
    iface = os.getenv("BRIDGE_INTERFACE", "").strip()
    ip = _ipv4_for_interface(iface) if iface else None
    if ip:
        return ip
    return "127.0.0.1"


APP_HOST = resolve_app_host()
APP_PORT = int(os.getenv("BRIDGE_PORT", "5000"))
APP_VERSION = os.getenv("BRIDGE_VERSION", "1.0.10")
SERVICE_NAME = "ma352-bridge"

SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "115200"))
RECONNECT_INTERVAL = float(os.getenv("RECONNECT_INTERVAL", "2.0"))
HOLD_INTERVAL = float(os.getenv("HOLD_INTERVAL", "0.12"))
QUERY_INTERVAL = float(os.getenv("QUERY_INTERVAL", "5.0"))
QUERY_ON_CONNECT = os.getenv("QUERY_ON_CONNECT", "1") != "0"
STATUS_ON_CONNECT = os.getenv("STATUS_ON_CONNECT", "1") != "0"
STATUS_QUERY_TIMEOUT = float(os.getenv("STATUS_QUERY_TIMEOUT", "1.0"))
STATUS_QUERY_DELAY = float(os.getenv("STATUS_QUERY_DELAY", "5.0"))
SERIAL_WRITE_TIMEOUT = float(os.getenv("SERIAL_WRITE_TIMEOUT", "2.0"))
SERIAL_STALE_TIMEOUT = float(os.getenv("SERIAL_STALE_TIMEOUT", "30.0"))
SERIAL_WATCHDOG_INTERVAL = float(os.getenv("SERIAL_WATCHDOG_INTERVAL", "2.0"))
VOLUME_RAMP_STEP = max(1, min(5, int(os.getenv("VOLUME_RAMP_STEP", "5"))))
VOLUME_RAMP_DELAY = float(os.getenv("VOLUME_RAMP_DELAY", "1.0"))
OUTBOUND_LOG_MAX = max(10, int(os.getenv("OUTBOUND_LOG_MAX", "200")))
INVALID_CMD_LOOKBACK = float(os.getenv("INVALID_CMD_LOOKBACK", "2.0"))
STARTUP_VOLUME_ENABLED = os.getenv("STARTUP_VOLUME_ENABLED", "1") != "0"
STARTUP_VOLUME = int(os.getenv("STARTUP_VOLUME", "15"))
SAFETY_ENABLED = os.getenv("SAFETY_ENABLED", "1") != "0"
SAFE_UNMUTE_MAX = int(os.getenv("SAFE_UNMUTE_MAX", "30"))
SAFE_UNMUTE_FALLBACK = int(os.getenv("SAFE_UNMUTE_FALLBACK", "20"))
COMMAND_STYLE = os.getenv("COMMAND_STYLE", "auto").lower()
DEFAULT_COMMAND_STYLE = os.getenv("DEFAULT_COMMAND_STYLE", "short").lower()
COMMAND_ZONE = os.getenv("COMMAND_ZONE", "Z1")

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.info("ma352-bridge version %s starting", APP_VERSION)


runtime_lock = threading.Lock()
runtime_started = False


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
        self._last_invalid_cmd = None
        self._probe_active = False
        self._probe_completed = False

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

    def begin_probe(self):
        """Enable probe mode for auto-detection (only once)."""
        with self._lock:
            if self._mode != "auto":
                return False
            if self._probe_completed:
                return False
            self._probe_active = True
            return True

    def end_probe(self):
        """Disable probe mode and finalize auto-detection."""
        with self._lock:
            self._probe_active = False
            self._probe_completed = True

    def allow_fallback(self):
        """Return True if fallback should be attempted."""
        with self._lock:
            return self._mode == "auto" and self._probe_active

    def needs_probe(self):
        """Return True if auto-detection probe should run."""
        with self._lock:
            return self._mode == "auto" and not self._probe_completed

    def detect_from_parts(self, parts):
        """Infer command style from a parsed response line."""
        if not parts:
            return
        with self._lock:
            if self._mode != "auto" or not self._probe_active or self._detected is not None:
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

    def mark_invalid(self, cmd=None, ts=None):
        """Mark that an invalid-command error was observed."""
        with self._lock:
            self._last_invalid_time = ts if ts is not None else time.time()
            self._last_invalid_cmd = cmd

    def invalid_after(self, ts, cmd=None):
        """Return True if an invalid-command error occurred after a timestamp."""
        with self._lock:
            if self._last_invalid_time <= ts:
                return False
            if cmd is None:
                return True
            return self._last_invalid_cmd == cmd

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
        self._write_queue = queue.Queue()
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
        self._writer_thread = threading.Thread(target=self._write_loop, daemon=True)
        self._writer_thread.start()

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

    def _write_loop(self):
        """Single-threaded writer to serialize outbound commands."""
        while True:
            if self._stop_event.is_set():
                try:
                    item = self._write_queue.get_nowait()
                except queue.Empty:
                    break
            else:
                try:
                    item = self._write_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
            if item is None:
                break
            command, done = item
            if self._stop_event.is_set():
                done["error"] = SerialException("Serial manager stopped")
                done["event"].set()
                continue
            error = None
            try:
                data = (command + "\r\n").encode("ascii", errors="ignore")
                with self._lock:
                    ser = self._serial
                if ser is None or not ser.is_open:
                    raise SerialException("Serial not connected")
                ser.write(data)
                ser.flush()
                record_outbound(command)
            except SerialException as exc:
                self._record_error("Serial write failed: %s" % exc)
                self._close()
                error = exc
            except Exception as exc:
                self._record_error("Serial write unexpected error: %s" % exc)
                self._close()
                error = exc
            finally:
                done["error"] = error
                done["event"].set()

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
        if self._stop_event.is_set():
            raise SerialException("Serial manager stopped")
        done = {"event": threading.Event(), "error": None}
        self._write_queue.put((command, done))
        if not done["event"].wait(timeout=SERIAL_WRITE_TIMEOUT):
            self.force_reconnect(f"write timeout after {SERIAL_WRITE_TIMEOUT}s")
            raise SerialException(f"Serial write timeout after {SERIAL_WRITE_TIMEOUT}s")
        err = done["error"]
        if err:
            if isinstance(err, SerialException):
                raise err
            raise SerialException(str(err))

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
        self._write_queue.put(None)
        if self._writer_thread and self._writer_thread.is_alive():
            self._writer_thread.join(timeout=0.5)

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
                else:
                    last_connect = snapshot["last_connect_time"]
                    if last_connect > 0:
                        age = time.time() - last_connect
                        if age > self._stale_timeout:
                            logging.warning(
                                "Serial received no data for %.1fs after connect, forcing reconnect.",
                                age,
                            )
                            self._manager.force_reconnect("no rx after connect for %.1fs" % age)
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


# Volume ramp controller for gradual increases
class VolumeRampController:
    """Queue volume increases in stepped increments with a delay."""
    def __init__(self, get_level_func, set_level_func, step, delay):
        """Configure ramp behavior."""
        self._get_level = get_level_func
        self._set_level = set_level_func
        self._step = max(1, min(5, int(step)))
        self._delay = max(0.2, float(delay))
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = None
        self._target = None
        self._deferred = False

    def request(self, target, defer=False):
        """Request a volume change to a target level (optionally deferred)."""
        join_thread = None
        deferred = False
        with self._lock:
            self._target = int(target)
            self._deferred = bool(defer)
            if self._deferred:
                join_thread = self._stop_locked()
                deferred = True
            else:
                if self._thread and self._thread.is_alive():
                    return
                self._stop_event = threading.Event()
                self._thread = threading.Thread(target=self._loop, daemon=True)
                self._thread.start()
        if join_thread:
            join_thread.join(timeout=0.3)
        if deferred:
            return

    def resume(self):
        """Resume a deferred ramp if a target is present."""
        with self._lock:
            target = self._target
            if target is None:
                return
            if self._thread and self._thread.is_alive():
                return
            self._deferred = False

        current = self._get_level()
        if target <= current:
            try:
                short_cmd = build_volume_set("short", target)
                zone_cmd = build_volume_set("zone", target)
                send_with_fallback(short_cmd, zone_cmd)
                self._set_level(target)
            except Exception as exc:
                logging.warning("Volume resume failed: %s", exc)
            with self._lock:
                if self._target == target:
                    self._target = None
            return

        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event = threading.Event()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def _loop(self):
        """Ramp volume upward in steps until the target is reached."""
        with self._lock:
            stop_event = self._stop_event
        if stop_event is None:
            return
        while not stop_event.is_set():
            with self._lock:
                target = self._target
                deferred = self._deferred
            if target is None:
                break
            if deferred:
                break

            current = self._get_level()
            if target <= current:
                break

            next_level = min(target, current + self._step)
            try:
                short_cmd = build_volume_set("short", next_level)
                zone_cmd = build_volume_set("zone", next_level)
                send_with_fallback(short_cmd, zone_cmd)
                self._set_level(next_level)
            except Exception as exc:
                logging.warning("Volume ramp failed: %s", exc)
                break

            if next_level >= target:
                break
            stop_event.wait(self._delay)

        with self._lock:
            if self._deferred and self._target is not None:
                return
            self._target = None
            self._deferred = False

    def stop(self):
        """Stop any active ramp."""
        join_thread = None
        with self._lock:
            join_thread = self._stop_locked()
        if join_thread:
            join_thread.join(timeout=0.3)

    def pause(self):
        """Pause an active ramp but keep the target for later."""
        join_thread = None
        with self._lock:
            if self._target is None:
                return
            self._deferred = True
            join_thread = self._stop_locked()
        if join_thread:
            join_thread.join(timeout=0.3)

    def has_target(self):
        """Return True if a target is queued."""
        with self._lock:
            return self._target is not None

    def get_target(self):
        """Return the queued target volume, if any."""
        with self._lock:
            return self._target

    def clear(self):
        """Stop any ramp and clear the queued target."""
        join_thread = None
        with self._lock:
            self._target = None
            self._deferred = False
            join_thread = self._stop_locked()
        if join_thread:
            join_thread.join(timeout=0.3)

    def _stop_locked(self):
        if not self._stop_event:
            return None
        self._stop_event.set()
        thread = self._thread
        self._thread = None
        self._stop_event = None
        return thread

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
                self._send()
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
        self._seq = 0

    def add(self, line):
        """Add a line, trimming to the max buffer size."""
        with self._lock:
            self._seq += 1
            self._lines.append((self._seq, line))
            if len(self._lines) > self._max_lines:
                self._lines = self._lines[-self._max_lines:]

    def clear(self):
        """Clear all buffered lines."""
        with self._lock:
            self._lines = []

    def checkpoint(self):
        """Return the latest sequence number for incremental snapshots."""
        with self._lock:
            return self._seq

    def snapshot_since(self, seq):
        """Return buffered lines added after the given sequence."""
        with self._lock:
            return [line for entry_seq, line in self._lines if entry_seq > seq]

    def snapshot(self):
        """Return a copy of the buffered lines."""
        with self._lock:
            return [line for _, line in self._lines]


class LineCollector:
    """Collect serial messages for a single request."""
    def __init__(self, predicate=None, max_lines=200):
        self._predicate = predicate
        self._max_lines = max_lines
        self._lines = []
        self._lock = threading.Lock()
        self._event = threading.Event()

    def on_line(self, line):
        if self._event.is_set():
            return
        with self._lock:
            if self._event.is_set():
                return
            self._lines.append(line)
            if self._predicate and self._predicate(line):
                self._event.set()
                return
            if self._max_lines and len(self._lines) >= self._max_lines:
                self._event.set()

    def wait(self, timeout):
        self._event.wait(timeout)
        with self._lock:
            return list(self._lines)


class LineCollectorRegistry:
    """Registry for active line collectors."""
    def __init__(self):
        self._lock = threading.Lock()
        self._collectors = []

    def add(self, collector):
        with self._lock:
            self._collectors.append(collector)

    def remove(self, collector):
        with self._lock:
            if collector in self._collectors:
                self._collectors.remove(collector)

    def dispatch(self, line):
        with self._lock:
            collectors = list(self._collectors)
        for collector in collectors:
            collector.on_line(line)

# Recent outbound command log for correlation with device errors
class OutboundLog:
    """Bounded list of outbound serial commands with timestamps."""
    def __init__(self, max_entries=200):
        """Initialize the outbound log with a max size."""
        self._lock = threading.Lock()
        self._entries = []
        self._max_entries = max_entries

    def add(self, command):
        """Record an outbound command with a timestamp."""
        entry = {"ts": time.time(), "cmd": command}
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._max_entries:
                self._entries = self._entries[-self._max_entries:]

    def recent_since(self, cutoff_ts):
        """Return entries recorded at or after the cutoff timestamp."""
        with self._lock:
            return [entry for entry in self._entries if entry["ts"] >= cutoff_ts]

    def last(self):
        """Return the most recent entry or None."""
        with self._lock:
            if not self._entries:
                return None
            return self._entries[-1]


def record_outbound(command):
    """Record an outbound command for later correlation."""
    outbound_log.add(command)


def format_outbound_entries(entries):
    """Format outbound entries for log output."""
    formatted = []
    for entry in entries:
        ts = time.strftime("%H:%M:%S", time.localtime(entry["ts"]))
        formatted.append(f"{entry['cmd']} @ {ts}")
    return ", ".join(formatted)

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


def build_help(style):
    """Build the help command for the given style."""
    return HELP if style == "short" else HELP_ZONE.format(zone=command_mode.zone())


def build_query(style):
    """Build the query command for the given style."""
    return QUERY if style == "short" else QUERY_ZONE.format(zone=command_mode.zone())


# Send with auto-detect fallback for short/zone mode
def send_with_fallback(short_cmd, zone_cmd):
    """Send a command with auto-detect fallback for command style."""
    manager = get_serial_manager()
    style = command_mode.style()
    cmd = short_cmd if style == "short" else zone_cmd
    send_time = time.time()
    manager.write(cmd)
    if command_mode.allow_fallback() and not command_mode.is_detected():
        time.sleep(0.15)
        if command_mode.invalid_after(send_time, cmd=cmd):
            fallback_style = "zone" if style == "short" else "short"
            fallback_cmd = zone_cmd if fallback_style == "zone" else short_cmd
            manager.write(fallback_cmd)
            command_mode.note_fallback(fallback_style)


# Shared singletons
state_cache = StateCache()
line_buffer = LineBuffer()
line_collectors = LineCollectorRegistry()
outbound_log = OutboundLog(OUTBOUND_LOG_MAX)

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
        messages = [cleaned]

    for message in messages:
        line_buffer.add(message)
        line_collectors.dispatch(message)
        if message.startswith("(") and message.endswith(")"):
            body = message[1:-1].strip()
        else:
            body = message.strip()
        if not body:
            continue

        upper_body = body.upper()
        if "ERROR" in upper_body and "INVALID COMMAND" in upper_body:
            now = time.time()
            last = outbound_log.last()
            if last:
                command_mode.mark_invalid(cmd=last["cmd"], ts=now)
            else:
                command_mode.mark_invalid(ts=now)
            cutoff = now - INVALID_CMD_LOOKBACK
            recent = outbound_log.recent_since(cutoff)
            if recent:
                logging.warning("Device error: %s; recent outbound: %s", message, format_outbound_entries(recent))
            else:
                if last:
                    logging.warning("Device error: %s; last outbound: %s", message, format_outbound_entries([last]))
                else:
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


# Startup volume control (applies once per process)
startup_volume_applied = False
startup_volume_lock = threading.Lock()

def _parse_volume_from_lines(lines):
    """Extract the last reported volume from serial messages."""
    volume = None
    for line in lines:
        if not line:
            continue
        message = line.strip()
        if message.startswith("(") and message.endswith(")"):
            body = message[1:-1].strip()
        else:
            body = message
        if not body:
            continue
        parts = body.split()
        if not parts:
            continue
        cmd = parts[0].upper()
        if cmd not in ("VOL", "VST"):
            continue
        candidate = parts[-1]
        try:
            volume = int(candidate)
        except ValueError:
            continue
    if volume is None:
        return None
    return max(0, min(50, volume))

def _detect_mode_from_message(message):
    """Try to detect command mode from a single message string."""
    if not message:
        return
    text = message.strip()
    if text.startswith("(") and text.endswith(")"):
        body = text[1:-1].strip()
    else:
        body = text
    if not body:
        return
    parts = body.split()
    if not parts:
        return
    command_mode.detect_from_parts(parts)

def _probe_device_volume(timeout=0.8):
    """Query the device and return the reported volume, if any."""
    collector = LineCollector()
    line_collectors.add(collector)
    try:
        short_cmd = build_query("short")
        zone_cmd = build_query("zone")
        send_with_fallback(short_cmd, zone_cmd)
        lines = collector.wait(timeout)
    finally:
        line_collectors.remove(collector)
    return _parse_volume_from_lines(lines)

def _startup_volume_worker():
    """Apply startup volume only if it does not increase device volume."""
    level = max(0, min(50, int(STARTUP_VOLUME)))
    if not SAFETY_ENABLED:
        try:
            short_cmd = build_volume_set("short", level)
            zone_cmd = build_volume_set("zone", level)
            send_with_fallback(short_cmd, zone_cmd)
            state_cache.set_volume(level)
            logging.info("Startup volume set to %s", level)
        except Exception as exc:
            logging.warning("Startup volume set failed: %s", exc)
        return

    try:
        device_volume = _probe_device_volume()
    except Exception as exc:
        logging.warning("Startup volume check failed: %s", exc)
        return
    if device_volume is None:
        logging.warning("Startup volume check returned no volume; leaving unchanged.")
        return
    if device_volume < level:
        logging.info(
            "Startup volume not applied; device volume %s below startup %s.",
            device_volume,
            level,
        )
        return
    if device_volume == level:
        logging.info("Startup volume already at %s; no change needed.", level)
        return
    try:
        short_cmd = build_volume_set("short", level)
        zone_cmd = build_volume_set("zone", level)
        send_with_fallback(short_cmd, zone_cmd)
        state_cache.set_volume(level)
        logging.info("Startup volume set to %s (device was %s).", level, device_volume)
    except Exception as exc:
        logging.warning("Startup volume set failed: %s", exc)

def _refresh_status_on_connect(timeout=1.0, delay=0.0):
    """Query device status to populate cached state, then log a state snapshot."""
    if delay > 0:
        time.sleep(delay)

    collector = LineCollector()
    line_collectors.add(collector)
    try:
        short_cmd = build_query("short")
        zone_cmd = build_query("zone")
        send_with_fallback(short_cmd, zone_cmd)
        # Wait for device to answer (or timeout)
        collector.wait(timeout)
    finally:
        line_collectors.remove(collector)

    # Give the serial parser a tiny moment to process any trailing lines
    time.sleep(0.05)

    _log_state_snapshot(prefix="Startup state")


def _log_state_snapshot(prefix="Startup state"):
    snap = state_cache.snapshot()
    # Flatten to match your curl output style
    payload = {
        "firmware": snap.get("firmware"),
        "input": snap.get("input"),
        "model": snap.get("model"),
        "mute": snap.get("mute"),
        "power": snap.get("power"),
        "serial_number": snap.get("serial_number"),
        "updated_at": snap.get("updated_at"),
        "volume": snap.get("volume"),
    }
    logging.info("%s: %s", prefix, json.dumps(payload, separators=(",", ":")))

def _command_mode_probe(timeout=1.0):
    """Probe the device to auto-detect short vs zone command style."""
    if not command_mode.begin_probe():
        return
    try:
        def predicate(line):
            _detect_mode_from_message(line)
            return command_mode.is_detected()

        for cmd in (QUERY, HELP):
            collector = LineCollector(predicate=predicate)
            line_collectors.add(collector)
            try:
                if cmd == QUERY:
                    short_cmd = build_query("short")
                    zone_cmd = build_query("zone")
                else:
                    short_cmd = build_help("short")
                    zone_cmd = build_help("zone")
                send_with_fallback(short_cmd, zone_cmd)
                collector.wait(timeout)
            finally:
                line_collectors.remove(collector)
            if command_mode.is_detected():
                break
        if not command_mode.is_detected():
            logging.warning("Command mode auto-detect probe did not determine style; using default.")
    finally:
        command_mode.end_probe()

def handle_serial_connect():
    """Apply the configured startup volume on first connect."""
    global startup_volume_applied
    needs_probe = command_mode.needs_probe()
    needs_startup = STARTUP_VOLUME_ENABLED
    needs_status = STATUS_ON_CONNECT
    if not needs_probe and not needs_startup and not needs_status:
        return

    def _on_connect_tasks():
        global startup_volume_applied
        if needs_probe:
            _command_mode_probe()
        if needs_status:
            _refresh_status_on_connect(STATUS_QUERY_TIMEOUT, STATUS_QUERY_DELAY)
        if not needs_startup:
            return
        with startup_volume_lock:
            if startup_volume_applied:
                return
            startup_volume_applied = True
        _startup_volume_worker()

    threading.Thread(target=_on_connect_tasks, daemon=True).start()


# Runtime-managed singletons (initialized in init_runtime)
serial_manager = None
query_poller = None
serial_watchdog = None
hold_controller = None
volume_ramp_controller = None


def init_runtime():
    """Initialize serial manager and background threads once."""
    global serial_manager, query_poller, serial_watchdog, hold_controller, volume_ramp_controller, runtime_started
    with runtime_lock:
        if runtime_started:
            return
        serial_manager = SerialManager(
            SERIAL_PORT,
            SERIAL_BAUD,
            RECONNECT_INTERVAL,
            line_handler=handle_serial_line,
            on_connect=handle_serial_connect,
        )
        def _poll_query():
            short_cmd = build_query("short")
            zone_cmd = build_query("zone")
            send_with_fallback(short_cmd, zone_cmd)

        query_poller = QueryPoller(
            _poll_query,
            QUERY_INTERVAL,
            send_immediately=QUERY_ON_CONNECT,
        )
        serial_watchdog = SerialWatchdog(serial_manager, SERIAL_STALE_TIMEOUT, SERIAL_WATCHDOG_INTERVAL)
        hold_controller = HoldController(
            serial_manager.write,
            state_cache.get_volume,
            state_cache.set_volume,
            HOLD_INTERVAL,
        )
        volume_ramp_controller = VolumeRampController(
            state_cache.get_volume,
            state_cache.set_volume,
            VOLUME_RAMP_STEP,
            VOLUME_RAMP_DELAY,
        )
        runtime_started = True


def get_serial_manager():
    """Return the active serial manager or raise if not initialized."""
    if serial_manager is None:
        raise SerialException("Serial runtime not initialized")
    return serial_manager


def get_hold_controller():
    """Return the hold controller or raise if not initialized."""
    if hold_controller is None:
        raise SerialException("Serial runtime not initialized")
    return hold_controller


def get_volume_ramp_controller():
    """Return the volume ramp controller or raise if not initialized."""
    if volume_ramp_controller is None:
        raise SerialException("Serial runtime not initialized")
    return volume_ramp_controller


def _age_or_none(timestamp, now):
    """Return elapsed seconds for a timestamp or None when unavailable."""
    if not timestamp:
        return None
    return now - timestamp


def _health_payload(snapshot=None, alive=True, ready=False, last_error=None):
    """Build a stable, machine-readable health response payload."""
    snapshot = snapshot or {}
    now = time.time()
    serial_connected = bool(snapshot.get("connected", False))
    last_rx_time = snapshot.get("last_rx_time") or None
    last_connect_time = snapshot.get("last_connect_time") or None
    last_error_time = snapshot.get("last_error_time") or None
    payload_last_error = last_error if last_error is not None else snapshot.get("last_error")
    if not ready and not payload_last_error:
        payload_last_error = "Serial transport not connected"
    return {
        "ok": bool(alive and ready),
        "alive": bool(alive),
        "ready": bool(ready),
        "service": SERVICE_NAME,
        "version": APP_VERSION,
        "serial_connected": serial_connected,
        "serial_port": snapshot.get("port", SERIAL_PORT),
        "serial_baud": snapshot.get("baud", SERIAL_BAUD),
        "last_error": payload_last_error,
        "last_rx_time": last_rx_time,
        "last_rx_age_s": _age_or_none(last_rx_time, now),
        "last_connect_time": last_connect_time,
        "last_connect_age_s": _age_or_none(last_connect_time, now),
        "last_error_time": last_error_time,
        "last_error_age_s": _age_or_none(last_error_time, now),
        "watchdog_timeout_s": SERIAL_STALE_TIMEOUT,
        "watchdog_interval_s": SERIAL_WATCHDOG_INTERVAL,
        "query_interval_s": QUERY_INTERVAL,
        "bind_host": APP_HOST,
        "listen_port": APP_PORT,
    }


# Flask app + HTTP routes
app = Flask(__name__)

# Helper to probe device help/firmware output
def query_help_lines(timeout=1.0):
    """Request help or query output and return captured lines."""
    collector = LineCollector()
    line_collectors.add(collector)
    try:
        short_cmd = build_help("short")
        zone_cmd = build_help("zone")
        send_with_fallback(short_cmd, zone_cmd)
        lines = collector.wait(timeout)
    finally:
        line_collectors.remove(collector)
    if lines:
        upper_lines = [line.upper() for line in lines]
        if any("INVALID COMMAND" in line for line in upper_lines):
            lines = []
    if not lines:
        collector = LineCollector()
        line_collectors.add(collector)
        try:
            short_cmd = build_query("short")
            zone_cmd = build_query("zone")
            send_with_fallback(short_cmd, zone_cmd)
            lines = collector.wait(timeout)
        finally:
            line_collectors.remove(collector)
    return lines


@app.route("/ping", methods=["GET"])
def ping():
    """Simple liveness check for the HTTP service."""
    return jsonify(alive=True)


# Health and diagnostics
@app.route("/health", methods=["GET"])
def health():
    """Return serial health metrics and watchdog settings."""
    try:
        manager = get_serial_manager()
    except SerialException as exc:
        return jsonify(_health_payload(alive=True, ready=False, last_error=str(exc))), 503
    snapshot = manager.health_snapshot()
    if not snapshot.get("connected", False):
        last_error = snapshot.get("last_error") or "Serial transport not connected"
        return jsonify(_health_payload(snapshot, alive=True, ready=False, last_error=last_error)), 503
    return jsonify(_health_payload(snapshot, alive=True, ready=True))


# Power endpoints
@app.route("/power/on", methods=["POST"])
def power_on():
    """Turn power on (with command style auto-detect)."""
    try:
        short_cmd = build_power_on("short")
        zone_cmd = build_power_on("zone")
        send_with_fallback(short_cmd, zone_cmd)
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
        ramp = get_volume_ramp_controller()
        hold = get_hold_controller()
        ramp.pause()
        hold.stop()
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
        ramp = get_volume_ramp_controller()
        if SAFETY_ENABLED:
            last_volume_cmd = ramp.get_target()
            if last_volume_cmd is None:
                last_volume_cmd = state_cache.get_volume()
            if last_volume_cmd > SAFE_UNMUTE_MAX:
                safe_volume = max(0, min(50, SAFE_UNMUTE_FALLBACK))
                ramp.clear()
                short_vol = build_volume_set("short", safe_volume)
                zone_vol = build_volume_set("zone", safe_volume)
                send_with_fallback(short_vol, zone_vol)
                state_cache.set_volume(safe_volume)
        short_cmd = build_mute_off("short")
        zone_cmd = build_mute_off("zone")
        send_with_fallback(short_cmd, zone_cmd)
    except SerialException as exc:
        return jsonify(error=str(exc)), 503
    state_cache.set_mute(False)
    if SAFETY_ENABLED and ramp.has_target():
        ramp.resume()
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
    """Set volume with bounds; large increases are queued."""
    try:
        ramp = get_volume_ramp_controller()
    except SerialException as exc:
        return jsonify(error=str(exc)), 503
    level = request.args.get("level")
    if level is None:
        data = request.get_json(silent=True) or {}
        level = data.get("level")
    try:
        level_int = int(level)
    except (TypeError, ValueError):
        return jsonify(error="level must be int 0..50", max=50), 400
    if not (0 <= level_int <= 50):
        return jsonify(error="level must be int 0..50", max=50, requested=level_int), 400
    current = state_cache.get_volume()
    if SAFETY_ENABLED:
        if state_cache.get_mute():
            if level_int != current:
                ramp.request(level_int, defer=True)
                return jsonify(level=level_int, queued=True, deferred=True)
            return jsonify(level=level_int, deferred=True)
        if level_int > current and (level_int - current) > VOLUME_RAMP_STEP:
            ramp.request(level_int)
            return jsonify(level=level_int, queued=True)
        ramp.clear()
    else:
        ramp.clear()
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


@app.route("/hold/start", methods=["POST"])
def hold_start():
    """Start a hold loop to step volume up or down."""
    data = request.get_json(silent=True) or {}
    direction = data.get("dir")
    if direction not in ("up", "down"):
        return jsonify(error="dir must be 'up' or 'down'"), 400
    try:
        manager = get_serial_manager()
        if not manager.health_snapshot().get("connected"):
            return jsonify(error="Serial not connected"), 503
        hold = get_hold_controller()
        hold.start(direction)
    except SerialException as exc:
        return jsonify(error=str(exc)), 503
    return Response(status=204)


@app.route("/hold/stop", methods=["POST"])
def hold_stop():
    """Stop any active hold loop."""
    try:
        hold = get_hold_controller()
        hold.stop()
        return Response(status=204)
    except SerialException as exc:
        return jsonify(error=str(exc)), 503


# Root endpoint
@app.route("/", methods=["GET"])
def root():
    """Return a minimal service identity payload."""
    return Response(
        json.dumps({"ok": True, "service": SERVICE_NAME, "version": APP_VERSION}),
        mimetype="application/json",
    )


def create_app():
    """Return the Flask application (without starting background threads)."""
    return app


def main():
    """Start runtime threads and run the HTTP server."""
    init_runtime()
    app.run(host=APP_HOST, port=APP_PORT)


# Local dev entrypoint
if __name__ == "__main__":
    main()
