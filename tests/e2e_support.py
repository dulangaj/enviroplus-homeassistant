# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Shared fixtures for the end-to-end test suite.

The daemon under test runs as a real subprocess (``python -m
enviroplus_homeassistant ...``), talking to a real mosquitto broker started
for the test class. Tests drive and observe it entirely through MQTT via
``Collector``, never by reaching into the process.
"""

import collections
import functools
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

import paho.mqtt.client as mqtt

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_THIS_DIR)
FAKES_DIR = os.path.join(_THIS_DIR, "fakes")

MOSQUITTO_BIN = shutil.which("mosquitto") or (
    "/opt/homebrew/sbin/mosquitto" if os.path.exists("/opt/homebrew/sbin/mosquitto") else None
)


def _mosquitto_available():
    return MOSQUITTO_BIN is not None


def _can_run_daemon(python_path):
    """A candidate interpreter is usable only if it can import ``atmos``,
    which acquire.py imports at module level (and which needs Python < 3.11:
    it uses inspect.getargspec, removed in 3.11)."""
    try:
        result = subprocess.run(
            [python_path, "-c", "import atmos"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@functools.lru_cache(maxsize=1)
def _resolve_python():
    """Find a Python interpreter suitable for running the daemon subprocess.

    Checks $E2E_PYTHON first, then a couple of likely interpreter names, then
    falls back to the interpreter running the tests themselves (works if the
    whole suite is already run under Python 3.9/3.10).
    """
    override = os.environ.get("E2E_PYTHON")
    candidates = [override] if override else []
    candidates += [shutil.which("python3.9"), shutil.which("python3.10"), sys.executable]
    for candidate in candidates:
        if candidate and _can_run_daemon(candidate):
            return candidate
    return None


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


Message = collections.namedtuple("Message", "topic payload retain ts")


class MosquittoBroker:
    """Starts a real mosquitto broker bound to loopback on a free port.

    Mosquitto 2.x refuses anonymous connections and won't bind at all without
    an explicit listener by default, so the generated config spells both out.
    """

    def __init__(self):
        self.host = "127.0.0.1"
        self.port = _find_free_port()
        self._tmpdir = tempfile.mkdtemp(prefix="enviroplus-e2e-broker-")
        conf_path = os.path.join(self._tmpdir, "mosquitto.conf")
        with open(conf_path, "w") as f:
            f.write(f"listener {self.port} {self.host}\nallow_anonymous true\npersistence false\nlog_type error\n")

        self._log_path = os.path.join(self._tmpdir, "mosquitto.log")
        self._log_file = open(self._log_path, "w")
        self.process = subprocess.Popen(
            [MOSQUITTO_BIN, "-c", conf_path],
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
        )
        try:
            self._wait_ready()
        except Exception:
            self.stop()
            raise

    def _wait_ready(self, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"mosquitto exited early (rc={self.process.returncode}); log: {self._log_path}"
                )
            try:
                with socket.create_connection((self.host, self.port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError(f"mosquitto did not become ready within {timeout}s; log: {self._log_path}")

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self._log_file.close()
        shutil.rmtree(self._tmpdir, ignore_errors=True)


class Daemon:
    """Runs `python -m enviroplus_homeassistant ...` as a real subprocess."""

    def __init__(self, python, host, port, args, env=None):
        self._tmpdir = tempfile.mkdtemp(prefix="enviroplus-e2e-daemon-")
        self._log_path = os.path.join(self._tmpdir, "daemon.log")
        self._log_file = open(self._log_path, "w")

        full_env = os.environ.copy()
        full_env["PYTHONPATH"] = os.pathsep.join([FAKES_DIR, REPO_ROOT])
        if env:
            full_env.update(env)

        cmd = [python, "-m", "enviroplus_homeassistant", "-h", host, "-p", str(port), *args]
        self.process = subprocess.Popen(
            cmd, cwd=REPO_ROOT, env=full_env, stdout=self._log_file, stderr=subprocess.STDOUT
        )
        self.log_text = None  # populated by stop()

    def stop(self, timeout=10):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=timeout)
        self._log_file.close()
        try:
            with open(self._log_path) as f:
                self.log_text = f.read()
        except OSError:
            self.log_text = ""
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    @property
    def returncode(self):
        return self.process.returncode


class Collector:
    """Subscribes to `homeassistant/#` (or a narrower topic) and records
    every message's topic, payload, retain flag, and arrival time."""

    def __init__(self, host, port, topic="homeassistant/#"):
        self._topic = topic
        self._lock = threading.Lock()
        self._messages = []
        self._connected = threading.Event()

        self._client = mqtt.Client(client_id=f"e2e-collector-{uuid.uuid4().hex[:8]}", protocol=mqtt.MQTTv311)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.connect(host, port)
        self._client.loop_start()

    def _on_connect(self, client, userdata, flags, rc):
        client.subscribe(self._topic, qos=1)
        self._connected.set()

    def _on_message(self, client, userdata, msg):
        with self._lock:
            self._messages.append(
                Message(msg.topic, msg.payload.decode("utf-8", errors="replace"), bool(msg.retain), time.time())
            )

    def wait_connected(self, timeout=5):
        if not self._connected.wait(timeout):
            raise AssertionError("collector did not connect to the broker in time")

    def publish(self, topic, payload, retain=False, qos=1):
        self._client.publish(topic, payload, retain=retain, qos=qos)

    def messages(self):
        with self._lock:
            return list(self._messages)

    def clear(self):
        with self._lock:
            self._messages.clear()

    def wait_for(self, predicate, timeout=10, poll_interval=0.05):
        """Return the first collected message matching predicate. Re-scans
        the whole buffer on each poll, so a message that arrived before this
        call still counts."""
        deadline = time.time() + timeout
        while True:
            for message in self.messages():
                if predicate(message):
                    return message
            if time.time() >= deadline:
                raise AssertionError(f"no message matched the predicate within {timeout}s")
            time.sleep(poll_interval)

    def wait_until(self, condition, timeout=10, poll_interval=0.05):
        """Like wait_for, but `condition` receives the whole message list at
        once, for conditions spanning multiple messages."""
        deadline = time.time() + timeout
        while True:
            result = condition(self.messages())
            if result:
                return result
            if time.time() >= deadline:
                raise AssertionError(f"condition not satisfied within {timeout}s")
            time.sleep(poll_interval)

    def close(self):
        self._client.disconnect()
        self._client.loop_stop()


def retained_snapshot(host, port, topic="#", settle=2.0):
    """Return what the broker has in its *retained store* for `topic`.

    A client that is already subscribed receives live messages with retain=0
    even when the publisher set the flag (MQTT 3.1.1 3.3.1.3); the flag is only
    set on delivery from the retained store to a new subscription.  So retention
    can only be observed by connecting fresh, which is what this does.  Use it
    for assertions about *retention*; use Collector for liveness and cadence.
    """
    snapshot = Collector(host, port, topic=topic)
    try:
        snapshot.wait_connected()
        # No completion signal exists for a retained replay, so settle briefly.
        time.sleep(settle)
        return [m for m in snapshot.messages() if m.retain]
    finally:
        snapshot.close()


E2E_SKIP_REASON = None
if not _mosquitto_available():
    E2E_SKIP_REASON = "mosquitto is not installed (checked PATH and /opt/homebrew/sbin/mosquitto)"
elif _resolve_python() is None:
    E2E_SKIP_REASON = (
        "no usable Python interpreter found (need Python <3.11 with 'atmos' importable); "
        "set E2E_PYTHON to one"
    )


@unittest.skipUnless(E2E_SKIP_REASON is None, E2E_SKIP_REASON or "")
class E2ETestCase(unittest.TestCase):
    """Base class for E2E tests: one real mosquitto broker per test class,
    real daemon subprocess(es) per test, driven/observed only through MQTT."""

    @classmethod
    def setUpClass(cls):
        cls.broker = MosquittoBroker()

    @classmethod
    def tearDownClass(cls):
        cls.broker.stop()

    def setUp(self):
        self._daemons = []
        self._collectors = []
        self._profile_paths = []

    def tearDown(self):
        for daemon in self._daemons:
            daemon.stop()
        for collector in self._collectors:
            collector.close()
        for path in self._profile_paths:
            try:
                os.remove(path)
            except OSError:
                pass

    def start_collector(self, topic="#"):
        # "#" (not "homeassistant/#"): tests isolate daemons from each other
        # with distinct --prefix values (see start_daemon), not just the
        # default "homeassistant" prefix, so the collector must not assume it.
        collector = Collector(self.broker.host, self.broker.port, topic=topic)
        collector.wait_connected()
        self._collectors.append(collector)
        return collector

    def retained(self, topic="#", settle=2.0):
        return retained_snapshot(self.broker.host, self.broker.port, topic=topic, settle=settle)

    def write_profile(self, profile):
        fd, path = tempfile.mkstemp(prefix="enviroplus-e2e-profile-", suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(profile, f)
        self._profile_paths.append(path)
        return path

    def start_daemon(self, args=(), profile=None, env=None, prefix=None):
        """Start the daemon subprocess. `prefix`, if given, is passed as
        --prefix: it is the *only* CLI knob that changes discovery/state
        topics (--client-id sets just the MQTT session id, not entity
        naming -- main.py never forwards it to HassDiscovery), so tests use
        distinct prefixes to isolate daemons sharing a per-class broker.
        """
        python = _resolve_python()
        full_env = dict(env or {})
        if profile is not None:
            full_env["ENVIROPLUS_FAKE_PROFILE"] = self.write_profile(profile)

        full_args = list(args)
        if prefix is not None:
            full_args = ["--prefix", prefix, *full_args]

        daemon = Daemon(python, self.broker.host, self.broker.port, full_args, env=full_env)
        self._daemons.append(daemon)
        return daemon
