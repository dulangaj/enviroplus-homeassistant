# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""End-to-end tests: a real daemon subprocess talking to a real mosquitto
broker, driven and observed entirely through MQTT. See tests/e2e_support.py
for the fixtures and tests/fakes/README.md for the fake-hardware profile
schema used to control sensor readings.
"""

import json
import time

from enviroplus_homeassistant.discovery import HassDiscovery
from enviroplus_homeassistant.gas import build_gas_baselines
from enviroplus_homeassistant.publish import EnhancedJSONEncoder

from tests.e2e_support import E2ETestCase

TINY_ARGS = ["--interval", "2", "--delay", "1", "--sample-period", "1", "--fast-duration", "3"]


def _expected_payload(entity):
    """Round-trip an entity through the same encoder publish.py uses, so the
    expectation is derived from production code rather than hand-copied."""
    return json.loads(json.dumps(entity, cls=EnhancedJSONEncoder))


def _all_entities(discovery):
    return list(discovery.sensors.values()) + [discovery.button, discovery.fast_sampling_switch]


class DiscoveryTests(E2ETestCase):
    """Area 1: discovery config messages for every sensor + button + switch."""

    def test_discovery_config_messages_match_all_entities(self):
        collector = self.start_collector()
        self.start_daemon(args=TINY_ARGS)  # no --client-id: exercises the real getserial() fallback

        # Derived from the daemon's own client_id convention (HassDiscovery
        # computes it deterministically from getserial(), which falls back to
        # the same "000000000ERROR" on this non-Pi host in both processes)
        # rather than a hardcoded CPU serial.
        expected = HassDiscovery(prefix="homeassistant", expire_after=2 * 2)
        expected_by_topic = {
            entity.get_config_topic(expected.client_id, expected.prefix): _expected_payload(entity)
            for entity in _all_entities(expected)
        }
        self.assertEqual(len(expected_by_topic), 12, "expected 10 sensors + button + switch")

        # Liveness first (these arrive with retain=0 -- see retained_snapshot),
        # then assert retention separately against the broker's retained store.
        collector.wait_until(
            lambda messages: set(expected_by_topic) <= {m.topic for m in messages},
            timeout=20,
        )

        retained = {m.topic: m for m in self.retained()}
        missing = set(expected_by_topic) - set(retained)
        self.assertFalse(missing, f"config topics not retained by the broker: {sorted(missing)}")
        for topic, payload in expected_by_topic.items():
            self.assertEqual(json.loads(retained[topic].payload), payload, topic)

    def test_a_sensor_config_has_the_shape_ha_consumes(self):
        collector = self.start_collector()
        self.start_daemon(args=TINY_ARGS, prefix="e2e-shape")

        expected = HassDiscovery(prefix="e2e-shape")
        topic = expected.sensors["humidity"].get_config_topic(expected.client_id, expected.prefix)
        msg = collector.wait_for(lambda m: m.topic == topic, timeout=20)
        payload = json.loads(msg.payload)

        self.assertIn("device", payload)
        self.assertIn("identifiers", payload["device"])
        # unique_id/topics key off HassDiscovery's own getserial()-derived
        # client_id, not --prefix or --client-id (see start_daemon's docstring).
        self.assertEqual(payload["unique_id"], f"{expected.client_id}_humidity")
        self.assertEqual(payload["state_topic"], expected.sensors["humidity"].state_topic)
        self.assertEqual(payload["unit_of_measurement"], "%")


class SteadyStatePublishingTests(E2ETestCase):
    """Area 2: sensor state messages with plausible values from the fakes."""

    def test_state_messages_carry_fake_readings(self):
        collector = self.start_collector()
        expected = HassDiscovery(prefix="e2e-steady")
        # --use-pms5003 matches the real deployment and is required for the PM
        # sensors: without it the polling thread never starts and pm* readings
        # are simply absent from every publish.
        self.start_daemon(args=TINY_ARGS + ["--use-pms5003"], prefix="e2e-steady")

        checks = {
            "temperature": 21.5,
            "humidity": 45.0,
            "pressure": 1013.25,
            "illuminance": 300.0,
            "gas_oxidising": 50.0,  # 50000 ohm -> 50 kOhm
            "pm25": 8.0,
        }
        for sensor_name, expected_value in checks.items():
            topic = expected.sensors[sensor_name].state_topic
            msg = collector.wait_for(lambda m, t=topic: m.topic == t, timeout=15)
            value = json.loads(msg.payload)["value"]
            self.assertAlmostEqual(value, expected_value, delta=max(0.5, abs(expected_value) * 0.05))


class ButtonTests(E2ETestCase):
    """Area 3: "Publish Now" button wakes the loop for an immediate publish."""

    def test_button_press_triggers_prompt_publish(self):
        collector = self.start_collector()
        expected = HassDiscovery(prefix="e2e-button")
        # A 300s interval means nothing would publish on its own inside this
        # test's lifetime, so any state message proves the button caused it.
        self.start_daemon(
            args=["--interval", "300", "--delay", "1", "--sample-period", "1", "--fast-duration", "3"],
            prefix="e2e-button",
        )
        humidity_topic = expected.sensors["humidity"].state_topic

        # A press during the warm-up delay is harmlessly swallowed (it only
        # wakes that loop's sleep early, no publish happens there), so retry
        # until the main loop is up rather than guessing the delay length.
        deadline = time.time() + 10
        msg = None
        press_time = None
        while msg is None and time.time() < deadline:
            press_time = time.time()
            collector.publish(expected.command_topic, "PRESS")
            try:
                msg = collector.wait_for(lambda m: m.topic == humidity_topic, timeout=1.5)
            except AssertionError:
                pass

        self.assertIsNotNone(msg, "button press never produced a state publish within 10s")
        elapsed = msg.ts - press_time
        self.assertLess(elapsed, 2.0, f"button press to publish took {elapsed:.2f}s, expected ~1s")


class FastSamplingTests(E2ETestCase):
    """Area 4: Fast Sampling switch -- tighter cadence while on, auto-revert
    after --fast-duration, and immediate manual-off revert."""

    def test_fast_sampling_tightens_cadence_then_auto_reverts(self):
        collector = self.start_collector()
        expected = HassDiscovery(prefix="e2e-fast-auto")
        self.start_daemon(
            # --fast-duration must comfortably exceed a few --fast-interval
            # publishes: too tight and, under load, the last sample lands after
            # auto-revert and is timed against the normal interval instead.
            args=["--interval", "5", "--fast-interval", "1", "--fast-duration", "6",
                  "--delay", "1", "--sample-period", "1"],
            prefix="e2e-fast-auto",
        )
        humidity_topic = expected.sensors["humidity"].state_topic

        collector.wait_for(
            lambda m: m.topic == expected.fast_state_topic and m.payload == "OFF",
            timeout=25,
        )

        t_on = time.time()
        collector.publish(expected.fast_command_topic, "ON")
        collector.wait_for(
            lambda m: m.topic == expected.fast_state_topic and m.payload == "ON" and m.ts >= t_on,
            timeout=5,
        )

        # Wait for auto-revert first, then measure only the publishes that fell
        # strictly inside the fast window -- a sample taken after the revert is
        # paced by --interval and would be timed against the wrong cadence.
        t_off = collector.wait_for(
            lambda m: m.topic == expected.fast_state_topic and m.payload == "OFF" and m.ts > t_on,
            timeout=20,
        ).ts

        fast_msgs = [m for m in collector.messages() if m.topic == humidity_topic and t_on <= m.ts <= t_off]
        self.assertGreaterEqual(len(fast_msgs), 3, f"expected several publishes inside the fast window, got {len(fast_msgs)}")
        for a, b in zip(fast_msgs, fast_msgs[1:]):
            delta = b.ts - a.ts
            self.assertLess(delta, 2.5, f"fast-sampling cadence was {delta:.2f}s, expected close to --fast-interval=1s")

        def two_relaxed_messages(messages):
            hits = [m for m in messages if m.topic == humidity_topic and m.ts > t_off]
            return hits if len(hits) >= 2 else None

        relaxed_msgs = collector.wait_until(two_relaxed_messages, timeout=15)
        relaxed_delta = relaxed_msgs[1].ts - relaxed_msgs[0].ts
        self.assertGreater(
            relaxed_delta, 2.5, f"post-revert cadence was {relaxed_delta:.2f}s, expected close to --interval=5s"
        )

    def test_manual_off_reverts_immediately(self):
        collector = self.start_collector()
        expected = HassDiscovery(prefix="e2e-fast-manual")
        # fast-duration is long enough that it cannot fire before the manual
        # OFF below, isolating the manual-revert path from the auto-revert.
        self.start_daemon(
            args=["--interval", "5", "--fast-interval", "1", "--fast-duration", "60",
                  "--delay", "1", "--sample-period", "1"],
            prefix="e2e-fast-manual",
        )

        collector.wait_for(
            lambda m: m.topic == expected.fast_state_topic and m.payload == "OFF",
            timeout=25,
        )

        collector.publish(expected.fast_command_topic, "ON")
        collector.wait_for(
            lambda m: m.topic == expected.fast_state_topic and m.payload == "ON",
            timeout=5,
        )

        t_off_command = time.time()
        collector.publish(expected.fast_command_topic, "OFF")
        off_msg = collector.wait_for(
            lambda m: (
                m.topic == expected.fast_state_topic and m.payload == "OFF"
                and m.ts >= t_off_command
            ),
            timeout=3,
        )
        elapsed = off_msg.ts - t_off_command
        self.assertLess(elapsed, 2.0, f"manual OFF took {elapsed:.2f}s to take effect, expected near-immediate")

        # The switch state must also be retained, so Home Assistant recovers the
        # position on reconnect instead of showing the entity as unknown.
        retained = {m.topic: m.payload for m in self.retained()}
        self.assertEqual(retained.get(expected.fast_state_topic), "OFF")


class AggregationTests(E2ETestCase):
    """Area 5: gas/PM aggregate via median, temperature/humidity/pressure/
    illuminance aggregate via IQR-filtered mean -- through the real stack."""

    def test_median_vs_iqr_mean_through_the_stack(self):
        collector = self.start_collector()
        expected = HassDiscovery(prefix="e2e-agg")

        # 'sequence' cycles [a, a, a, b]. Window length (sample_count =
        # interval / sample_period = 4) equals the pattern's period, so
        # *any* 4 consecutive ticks are some rotation of the same multiset --
        # deterministic regardless of exactly which ticks land in the window,
        # which real wall-clock timing does not let us pin down exactly.
        # For this shape: median = a, IQR-filtered mean = (3a + b) / 4
        # (verified: at n=4 the single high value doesn't clear the Tukey
        # fence, so it isn't filtered out).
        profile = {
            "gas": {
                "oxidising": {"mode": "sequence", "values": [10000, 10000, 10000, 40000]},  # ohms -> 10/10/10/40 kOhm
                "reducing": {"mode": "sequence", "values": [20000, 20000, 20000, 50000]},  # -> 20/20/20/50 kOhm
            },
            "bme280": {
                "temperature": {"mode": "sequence", "values": [10, 10, 10, 40]},
                "humidity": {"mode": "sequence", "values": [20, 20, 20, 50]},
            },
        }
        self.start_daemon(
            args=["--interval", "4", "--delay", "1", "--sample-period", "1", "--fast-duration", "3"],
            profile=profile,
            prefix="e2e-agg",
        )

        def value_of(sensor_name):
            topic = expected.sensors[sensor_name].state_topic
            msg = collector.wait_for(lambda m, t=topic: m.topic == t, timeout=15)
            return json.loads(msg.payload)["value"]

        self.assertAlmostEqual(value_of("gas_oxidising"), 10.0, places=2, msg="median sensor")
        self.assertAlmostEqual(value_of("temperature"), 17.5, places=2, msg="IQR-mean sensor: (3*10+40)/4")
        self.assertAlmostEqual(value_of("gas_reducing"), 20.0, places=2, msg="median sensor")
        self.assertAlmostEqual(value_of("humidity"), 27.5, places=2, msg="IQR-mean sensor: (3*20+50)/4")


class GasIndexTests(E2ETestCase):
    """Area 6: gas baseline args enable index sensors that compute correctly."""

    def test_gas_indices_appear_and_compute(self):
        collector = self.start_collector()
        baselines = build_gas_baselines(oxidising=10.0, reducing=20.0, nh3=40.0)
        expected = HassDiscovery(prefix="e2e-gasidx", gas_baselines=baselines)

        profile = {
            "gas": {
                "oxidising": {"mode": "constant", "value": 12000.0},  # 12 kOhm / 10 baseline = 1.2
                "reducing": {"mode": "constant", "value": 10000.0},  # 20 baseline / 10 kOhm = 2.0
                "nh3": {"mode": "constant", "value": 20000.0},  # 40 baseline / 20 kOhm = 2.0
            }
        }
        self.start_daemon(
            args=TINY_ARGS + [
                "--gas-oxidising-baseline", "10", "--gas-reducing-baseline", "20", "--gas-nh3-baseline", "40",
            ],
            profile=profile,
            prefix="e2e-gasidx",
        )

        self.assertIn("gas_no2_index", expected.sensors)
        self.assertIn("gas_co_index", expected.sensors)
        self.assertIn("gas_nh3_index", expected.sensors)

        config_topic = expected.sensors["gas_no2_index"].get_config_topic(expected.client_id, expected.prefix)
        collector.wait_for(lambda m: m.topic == config_topic, timeout=25)

        def value_of(sensor_name):
            topic = expected.sensors[sensor_name].state_topic
            msg = collector.wait_for(lambda m, t=topic: m.topic == t, timeout=15)
            return json.loads(msg.payload)["value"]

        self.assertAlmostEqual(value_of("gas_no2_index"), 1.2, places=3)
        self.assertAlmostEqual(value_of("gas_co_index"), 2.0, places=3)
        self.assertAlmostEqual(value_of("gas_nh3_index"), 2.0, places=3)


class DeleteSensorsTests(E2ETestCase):
    """Area 7: --delete-sensors clears every entity from the retained store."""

    def test_delete_sensors_clears_the_retained_configs(self):
        collector = self.start_collector()
        expected = HassDiscovery(prefix="e2e-delete")
        expected_topics = {
            entity.get_config_topic(expected.client_id, expected.prefix) for entity in _all_entities(expected)
        }

        # Publish the configs first, otherwise there is nothing to delete and
        # the test would pass against a broker that never held them.
        publisher = self.start_daemon(args=TINY_ARGS, prefix="e2e-delete")
        collector.wait_until(lambda messages: expected_topics <= {m.topic for m in messages}, timeout=25)
        self.assertLessEqual(expected_topics, {m.topic for m in self.retained()}, "setup: configs should be retained")
        publisher.stop()

        daemon = self.start_daemon(args=["--delete-sensors", "--delay", "1"], prefix="e2e-delete")
        daemon.process.wait(timeout=25)
        self.assertEqual(daemon.process.returncode, 0)

        # An empty retained payload deletes the retained message, so the
        # entities must now be absent from a fresh subscriber's replay.
        still_retained = expected_topics & {m.topic for m in self.retained()}
        self.assertFalse(still_retained, f"still in the retained store after deletion: {sorted(still_retained)}")


class ShutdownTests(E2ETestCase):
    """Area 8: a real SIGTERM (via Popen.terminate()) exits promptly and cleanly."""

    def test_sigterm_exits_promptly_with_clean_status(self):
        collector = self.start_collector()
        expected = HassDiscovery(prefix="e2e-shutdown")
        daemon = self.start_daemon(args=TINY_ARGS, prefix="e2e-shutdown")

        config_topic = expected.sensors["humidity"].get_config_topic(expected.client_id, expected.prefix)
        collector.wait_for(lambda m: m.topic == config_topic, timeout=25)

        t0 = time.time()
        daemon.process.terminate()  # SIGTERM
        daemon.process.wait(timeout=10)
        elapsed = time.time() - t0
        daemon.stop()  # already exited; just closes the log and captures it for the assertion message

        self.assertLess(elapsed, 5.0, f"SIGTERM shutdown took {elapsed:.2f}s")
        self.assertEqual(daemon.returncode, 0, daemon.log_text)


if __name__ == "__main__":
    import unittest

    unittest.main()
