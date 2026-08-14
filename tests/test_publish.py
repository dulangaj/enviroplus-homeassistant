# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import unittest
from unittest.mock import MagicMock, patch

from enviroplus_homeassistant.publish import MqttPublisher


class MqttPublisherSubscribeTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("enviroplus_homeassistant.publish.mqtt.Client")
        self.addCleanup(patcher.stop)
        client_class = patcher.start()
        self.client = MagicMock()
        self.client.is_connected.return_value = False
        client_class.return_value = self.client

        self.publisher = MqttPublisher(
            "client-id", "host", 1883, "user", "pass", False, on_connect=lambda *a: None
        )

    def _simulate_connect(self, rc=0):
        self.client.on_connect(self.client, None, {}, rc)

    def test_subscribe_before_connect_subscribes_once_connected(self):
        self.publisher.subscribe("trigger/topic", lambda payload: None)

        self.client.subscribe.assert_not_called()

        self._simulate_connect()

        self.client.subscribe.assert_called_once_with("trigger/topic", qos=1)

    def test_subscribe_after_connect_subscribes_immediately(self):
        self.client.is_connected.return_value = True

        self.publisher.subscribe("trigger/topic", lambda payload: None)

        self.client.subscribe.assert_called_once_with("trigger/topic", qos=1)

    def test_reconnect_resubscribes_all_topics(self):
        self.publisher.subscribe("trigger/topic", lambda payload: None)

        self._simulate_connect()
        self._simulate_connect()

        self.assertEqual(
            self.client.subscribe.call_args_list,
            [
                unittest.mock.call("trigger/topic", qos=1),
                unittest.mock.call("trigger/topic", qos=1),
            ],
        )

    def test_callback_exception_is_contained(self):
        def failing_callback(payload):
            raise RuntimeError("boom")

        self.publisher.subscribe("trigger/topic", failing_callback)

        handler = self.client.message_callback_add.call_args.args[1]
        message = MagicMock(topic="trigger/topic", payload=b"ON")

        try:
            handler(self.client, None, message)
        except RuntimeError:
            self.fail("exception from callback should not propagate")

    def test_callback_receives_decoded_payload(self):
        received = []
        self.publisher.subscribe("trigger/topic", received.append)

        handler = self.client.message_callback_add.call_args.args[1]
        message = MagicMock(topic="trigger/topic", payload=b"ON")
        handler(self.client, None, message)

        self.assertEqual(received, ["ON"])

    def test_non_utf8_payload_does_not_raise(self):
        received = []
        self.publisher.subscribe("trigger/topic", received.append)

        handler = self.client.message_callback_add.call_args.args[1]
        message = MagicMock(topic="trigger/topic", payload=b"\xff\xfe")

        try:
            handler(self.client, None, message)
        except UnicodeDecodeError:
            self.fail("malformed payload bytes should not propagate")

        self.assertEqual(len(received), 1)


if __name__ == "__main__":
    unittest.main()
