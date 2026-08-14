# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import os
import signal
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

# acquire.py imports Raspberry Pi hardware libraries that are not installed on
# a dev machine. Stub them out before anything in this test file imports
# enviroplus_homeassistant.acquire or enviroplus_homeassistant.__main__.
for _name in ("bme280", "pms5003", "enviroplus", "enviroplus.gas", "enviroplus.noise", "ltr559"):
    sys.modules.setdefault(_name, MagicMock())

from enviroplus_homeassistant import acquire
from enviroplus_homeassistant import __main__ as main_module


class CpuTemperatureTests(unittest.TestCase):
    """acquire.CPU_TEMPERATURE_PATH is a module-level constant precisely so it
    can be repointed here, instead of at the hardcoded, Linux-only
    /sys/class/thermal/thermal_zone0/temp path."""

    def test_get_cpu_temperature_parses_millidegrees(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("48123")
            path = f.name

        try:
            with patch.object(acquire, "CPU_TEMPERATURE_PATH", path):
                sensor = acquire.EnviroPlus(use_pms5003=False, num_samples=5, use_cpu_comp=False)
                self.assertEqual(sensor.get_cpu_temperature(), 48.123)
        finally:
            os.remove(path)


class SigtermShutdownTests(unittest.TestCase):
    """Delivering a real OS SIGTERM to the current process from inside a test
    and reliably synchronizing with it is unreliable (Python only runs signal
    handlers between bytecode instructions on the main thread). Instead this
    captures the handler that main() registers via signal.signal and invokes
    it directly, then verifies its effect: main()'s loop -- parked in
    trigger.wait() for a 10s sample period / 300s publish interval -- notices
    the stop flag and unwinds through `finally: publisher.destroy()` almost
    immediately, rather than after the full wait. This tests the handler's
    effect, not a real delivered signal.
    """

    def test_sigterm_handler_stops_and_wakes_the_loop(self):
        captured_handlers = {}

        def fake_signal(signum, handler):
            captured_handlers[signum] = handler

        mock_publisher = MagicMock()
        mock_publisher.connection_error = None

        errors = []

        def run_main():
            try:
                main_module.main()
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        with patch("signal.signal", side_effect=fake_signal), \
             patch.object(main_module, "MqttPublisher", return_value=mock_publisher), \
             patch.object(main_module, "HassDiscovery", return_value=MagicMock()), \
             patch.object(main_module, "EnviroPlus"), \
             patch.object(sys, "argv", ["prog", "--host", "localhost", "--delay", "0"]):

            thread = threading.Thread(target=run_main, daemon=True)
            thread.start()

            waiter = threading.Event()
            for _ in range(200):  # up to ~2s
                if signal.SIGTERM in captured_handlers:
                    break
                waiter.wait(0.01)
            self.assertIn(
                signal.SIGTERM, captured_handlers,
                "main() never registered a SIGTERM handler",
            )

            # Simulate signal delivery by invoking the captured handler directly.
            captured_handlers[signal.SIGTERM](signal.SIGTERM, None)

            thread.join(timeout=3)
            self.assertFalse(
                thread.is_alive(),
                "main() did not exit promptly after the SIGTERM handler ran; "
                "the loop should be woken via `trigger`, not wait out the "
                "sample period",
            )
            self.assertEqual(errors, [])
            self.assertTrue(mock_publisher.destroy.called, "publisher.destroy() should run on shutdown")


if __name__ == "__main__":
    unittest.main()
