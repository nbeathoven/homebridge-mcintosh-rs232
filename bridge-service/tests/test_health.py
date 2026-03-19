import unittest
from unittest.mock import patch

import app as bridge_app
from serial import SerialException


class FakeManager:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def health_snapshot(self):
        return dict(self._snapshot)


class HealthEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = bridge_app.app.test_client()

    def test_health_reports_machine_readable_monitoring_fields(self):
        manager = FakeManager(
            {
                "connected": True,
                "port": "/dev/ttyUSB0",
                "baud": 115200,
                "last_rx_time": 100.0,
                "last_connect_time": 90.0,
                "last_error_time": 80.0,
                "last_error": "older warning",
            }
        )
        with patch.object(bridge_app, "get_serial_manager", return_value=manager):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["service"], "ma352-bridge")
        self.assertEqual(payload["version"], bridge_app.APP_VERSION)
        self.assertEqual(payload["serial_connected"], True)
        self.assertEqual(payload["serial_port"], "/dev/ttyUSB0")
        self.assertEqual(payload["serial_baud"], 115200)
        self.assertEqual(payload["last_error"], "older warning")
        self.assertIn("watchdog_timeout_s", payload)
        self.assertIn("watchdog_interval_s", payload)
        self.assertIn("query_interval_s", payload)

    def test_health_returns_503_when_runtime_is_missing(self):
        with patch.object(
            bridge_app,
            "get_serial_manager",
            side_effect=SerialException("Serial runtime not initialized"),
        ):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["service"], "ma352-bridge")
        self.assertEqual(payload["serial_connected"], False)
        self.assertEqual(payload["serial_port"], bridge_app.SERIAL_PORT)
        self.assertEqual(payload["serial_baud"], bridge_app.SERIAL_BAUD)
        self.assertEqual(payload["last_error"], "Serial runtime not initialized")

    def test_health_returns_503_when_serial_never_opened(self):
        manager = FakeManager(
            {
                "connected": False,
                "port": "/dev/ttyUSB0",
                "baud": 115200,
                "last_rx_time": 0.0,
                "last_connect_time": 0.0,
                "last_error_time": 120.0,
                "last_error": "Serial connect failed: no such file",
            }
        )
        with patch.object(bridge_app, "get_serial_manager", return_value=manager):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["serial_connected"], False)
        self.assertEqual(payload["last_error"], "Serial connect failed: no such file")

    def test_health_returns_200_when_reconnecting_after_prior_connect(self):
        manager = FakeManager(
            {
                "connected": False,
                "port": "/dev/ttyUSB0",
                "baud": 115200,
                "last_rx_time": 110.0,
                "last_connect_time": 100.0,
                "last_error_time": 120.0,
                "last_error": "Forced reconnect: stale for 35.0s",
            }
        )
        with patch.object(bridge_app, "get_serial_manager", return_value=manager):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["serial_connected"], False)
        self.assertEqual(payload["last_error"], "Forced reconnect: stale for 35.0s")


if __name__ == "__main__":
    unittest.main()
