import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import app as bridge_app


BRIDGE_SERVICE_DIR = Path(__file__).resolve().parents[1]


class ResolveAppHostTests(unittest.TestCase):
    def test_resolve_app_host_defaults_to_localhost(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(bridge_app, "_ipv4_for_interface", return_value=None):
                self.assertEqual(bridge_app.resolve_app_host(), "127.0.0.1")

    def test_resolve_app_host_prefers_explicit_bridge_host(self):
        env = {"BRIDGE_HOST": "0.0.0.0", "BRIDGE_INTERFACE": "eth0"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(bridge_app, "_ipv4_for_interface", return_value="192.168.5.163"):
                self.assertEqual(bridge_app.resolve_app_host(), "0.0.0.0")

    def test_resolve_app_host_uses_interface_ipv4_when_host_unset(self):
        env = {"BRIDGE_INTERFACE": "eth0"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(bridge_app, "_ipv4_for_interface", return_value="192.168.5.163"):
                self.assertEqual(bridge_app.resolve_app_host(), "192.168.5.163")


class InstallerAndUnitContractTests(unittest.TestCase):
    def test_installer_defaults_to_local_only_bind(self):
        script = (BRIDGE_SERVICE_DIR / "rpi-install.sh").read_text()

        self.assertIn("# BRIDGE_HOST=0.0.0.0", script)
        self.assertIn("# BRIDGE_INTERFACE=eth0", script)
        self.assertNotRegex(script, r"(?m)^BRIDGE_HOST=0\.0\.0\.0$")

    def test_systemd_unit_waits_for_network_online(self):
        unit = (BRIDGE_SERVICE_DIR / "systemd" / "ma352-bridge.service").read_text()

        self.assertIn("Wants=network-online.target", unit)
        self.assertIn("After=network-online.target", unit)


class ProcmonExampleContractTests(unittest.TestCase):
    def test_procmon_example_matches_canonical_monitor_contract(self):
        example_path = BRIDGE_SERVICE_DIR / "procmon" / "ma352-monitor.example.json"
        monitor = json.loads(example_path.read_text())

        self.assertEqual(monitor["id"], "ma352")
        self.assertEqual(monitor["target"]["transport"], "local")
        self.assertEqual(monitor["metadata"]["service_name"], "ma352-bridge")

        checks = {check["id"]: check for check in monitor["checks"]}
        self.assertEqual(checks["ma352-service-active"]["type"], "systemd_service")
        self.assertEqual(checks["ma352-service-active"]["service"], "ma352-bridge")
        self.assertEqual(checks["ma352-health"]["type"], "http_json")
        self.assertEqual(checks["ma352-health"]["url"], "http://127.0.0.1:5000/health")
        self.assertEqual(checks["ma352-health"]["timeout"], "3s")
        self.assertTrue(checks["ma352-health"]["require_ok"])
        self.assertTrue(checks["ma352-health"]["require_serial_connected"])

        recovery_types = [step["type"] for step in monitor["recovery"]]
        self.assertEqual(recovery_types, ["restart_systemd_service", "sleep", "recheck"])


if __name__ == "__main__":
    unittest.main()
