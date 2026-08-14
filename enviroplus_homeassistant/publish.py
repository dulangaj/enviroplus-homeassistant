# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from ctypes import ArgumentError
import logging
import paho.mqtt.client as mqtt
import json, dataclasses
from .helpers import del_none

logging.basicConfig(level=logging.DEBUG)

class EnhancedJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if dataclasses.is_dataclass(o):
            return del_none(dataclasses.asdict(o).copy())
        return super().default(o)

class MqttPublisher:
    def __init__(self, client_id, host, port, username, password, use_tls, on_connect = None):
        if not callable(on_connect):
            raise ArgumentError("on_connect callback should be a function.")
        self.on_connect = on_connect

        self.connection_error = None
        self._subscriptions = {}
        self.client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
        self.client.on_connect = self.__on_connect
        self.client.username_pw_set(username, password)
        logger = logging.getLogger(__name__)
        self.client.enable_logger(logger)
        # tls_set() must be called *before* connect().  It configures the SSL
        # context using the system's default CA certificates, which is correct
        # for brokers that present a publicly-trusted certificate.
        if use_tls:
            self.client.tls_set()
        self.client.connect(host, port)
        self.client.loop_start()

    def __on_connect(self, client, userdata, flags, rc):
        errors = {
            1: "incorrect MQTT protocol version",
            2: "invalid MQTT client identifier",
            3: "server unavailable",
            4: "bad username or password",
            5: "connection refused"
        }

        if rc > 0:
            self.connection_error = errors.get(rc, "unknown error")
        else:
            if self.on_connect:
                self.on_connect(self, userdata, flags)
            # Subscriptions do not survive a reconnect, so every topic must be
            # re-subscribed here whenever the client (re)connects.
            for topic in self._subscriptions:
                self.client.subscribe(topic, qos=1)

    def __wrap_callback(self, callback):
        def handler(client, userdata, message):
            try:
                payload = message.payload.decode("utf-8", errors="replace")
                callback(payload)
            except Exception:
                logging.getLogger(__name__).exception(
                    "Unhandled exception in MQTT subscription callback for topic %s",
                    message.topic,
                )
        return handler

    def subscribe(self, topic, callback):
        self._subscriptions[topic] = callback
        self.client.message_callback_add(topic, self.__wrap_callback(callback))
        if self.client.is_connected():
            # QoS 1, matching publish(): the broker acknowledges the
            # subscription request, avoiding a silently dropped trigger on
            # unreliable Wi-Fi links.
            self.client.subscribe(topic, qos=1)

    def publish(self, topic, value, retain=False):
        # QoS 1 (at-least-once delivery) ensures the broker acknowledges every
        # message.  This prevents silent data loss on unreliable Wi-Fi links
        # without the overhead of QoS 2's exactly-once handshake.
        self.client.publish(topic, str(value), retain=retain, qos=1)

    def publish_json(self, topic, obj, retain=False):
        self.publish(topic, json.dumps(obj,cls=EnhancedJSONEncoder),retain=retain)

    def destroy(self):
        self.client.disconnect()
        self.client.loop_stop()
