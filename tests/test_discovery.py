# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import unittest

from enviroplus_homeassistant.models import (
    DiscoveryButtonConfig,
    DiscoveryDeviceConfig,
    DiscoverySensorConfig,
    DiscoverySwitchConfig,
)

CLIENT_ID = "enviroplus"
PREFIX = "homeassistant"


class DiscoverySensorRegressionTests(unittest.TestCase):
    """An existing sensor's topics and payload must not change shape."""

    def setUp(self):
        self.device = DiscoveryDeviceConfig(name="AirQuality")
        self.sensor = DiscoverySensorConfig(
            client_id=CLIENT_ID,
            prefix=PREFIX,
            name="Humidity",
            unit_of_measurement="%",
            device=self.device,
            device_class="humidity",
        )

    def test_config_topic(self):
        self.assertEqual(
            self.sensor.get_config_topic(CLIENT_ID, PREFIX),
            "homeassistant/sensor/enviroplus/humidity/config",
        )

    def test_state_topic(self):
        self.assertEqual(
            self.sensor.state_topic,
            "homeassistant/sensor/enviroplus/humidity/state",
        )

    def test_serialised_fields_unchanged(self):
        expected = {
            "name": "Humidity",
            "device_class": "humidity",
            "unit_of_measurement": "%",
            "value_template": "{{ value_json.value | round(1) }}",
            "expire_after": 15 * 60,
            "force_update": True,
            "state_class": "measurement",
            "object_id": "airquality_humidity",
            "unique_id": "enviroplus_humidity",
            "state_topic": "homeassistant/sensor/enviroplus/humidity/state",
        }
        actual = {k: v for k, v in vars(self.sensor).items() if k != "device"}
        self.assertEqual(actual, expected)


class DiscoveryButtonConfigTests(unittest.TestCase):
    def setUp(self):
        self.device = DiscoveryDeviceConfig(name="AirQuality")
        self.button = DiscoveryButtonConfig(
            client_id=CLIENT_ID,
            prefix=PREFIX,
            name="Publish Now",
            device=self.device,
        )

    def test_config_topic(self):
        self.assertEqual(
            self.button.get_config_topic(CLIENT_ID, PREFIX),
            "homeassistant/button/enviroplus/publish_now/config",
        )

    def test_command_topic(self):
        self.assertEqual(
            self.button.command_topic,
            "homeassistant/button/enviroplus/publish_now/set",
        )

    def test_serialised_json_excludes_sensor_only_keys(self):
        import dataclasses

        payload = dataclasses.asdict(self.button)
        for key in (
            "state_topic",
            "state_class",
            "force_update",
            "expire_after",
            "unit_of_measurement",
            "value_template",
        ):
            self.assertNotIn(key, payload)

        for key in ("name", "unique_id", "object_id", "command_topic", "device"):
            self.assertIn(key, payload)


class DiscoverySwitchConfigTests(unittest.TestCase):
    def setUp(self):
        self.device = DiscoveryDeviceConfig(name="AirQuality")
        self.switch = DiscoverySwitchConfig(
            client_id=CLIENT_ID,
            prefix=PREFIX,
            name="Fast Sampling",
            device=self.device,
        )

    def test_config_topic(self):
        self.assertEqual(
            self.switch.get_config_topic(CLIENT_ID, PREFIX),
            "homeassistant/switch/enviroplus/fast_sampling/config",
        )

    def test_command_topic(self):
        self.assertEqual(
            self.switch.command_topic,
            "homeassistant/switch/enviroplus/fast_sampling/set",
        )

    def test_state_topic(self):
        self.assertEqual(
            self.switch.state_topic,
            "homeassistant/switch/enviroplus/fast_sampling/state",
        )

    def test_optimistic_is_false(self):
        self.assertIn("optimistic", vars(self.switch))
        self.assertIs(self.switch.optimistic, False)

    def test_serialised_json_excludes_sensor_only_keys(self):
        import dataclasses

        payload = dataclasses.asdict(self.switch)
        for key in (
            "state_class",
            "force_update",
            "expire_after",
            "unit_of_measurement",
            "device_class",
            "value_template",
        ):
            self.assertNotIn(key, payload)

        for key in (
            "name",
            "unique_id",
            "object_id",
            "command_topic",
            "state_topic",
            "device",
            "optimistic",
        ):
            self.assertIn(key, payload)


class HassDiscoverySwitchTests(unittest.TestCase):
    def setUp(self):
        from enviroplus_homeassistant.discovery import HassDiscovery

        self.discovery = HassDiscovery(client_id=CLIENT_ID, prefix=PREFIX)

    def test_switch_not_in_sensors(self):
        self.assertNotIn("fast_sampling", self.discovery.sensors)
        for sensor in self.discovery.sensors.values():
            self.assertNotIsInstance(sensor, DiscoverySwitchConfig)

    def test_public_topic_attributes(self):
        self.assertEqual(
            self.discovery.fast_command_topic,
            "homeassistant/switch/enviroplus/fast_sampling/set",
        )
        self.assertEqual(
            self.discovery.fast_state_topic,
            "homeassistant/switch/enviroplus/fast_sampling/state",
        )


if __name__ == "__main__":
    unittest.main()
