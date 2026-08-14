# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import argparse, math, time, sys, logging, threading, queue, signal

from enviroplus_homeassistant.models import SensorPayload

logging.basicConfig(level=logging.INFO)

from .publish import MqttPublisher
from .discovery import HassDiscovery
from .acquire import EnviroPlus
from .gas import apply_gas_indices, build_gas_baselines

def parse_args():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("-h", "--host", required=True, help="the MQTT host to connect to")
    ap.add_argument("-p", "--port", type=int, default=1883, help="the port on the MQTT host to connect to")
    ap.add_argument("-U", "--username", default=None, help="the MQTT username to connect with")
    ap.add_argument("-P", "--password", default=None, help="the password to connect with")
    ap.add_argument("--delete-sensors", action="store_true", help="Delete sensors in home assistant")
    ap.add_argument("--print-sensors", action="store_true", help="Print sensors and do nothing else")
    ap.add_argument("--prefix", default="homeassistant", help="the topic prefix to use when publishing readings, i.e. 'homeassistant'")
    ap.add_argument("--client-id", default="", help="the MQTT client identifier to use when connecting")
    ap.add_argument("--interval", type=int, default=300, help="the duration in seconds between publishes")
    ap.add_argument("--fast-interval", type=int, default=60, help="the duration in seconds between publishes while Fast Sampling is switched on in Home Assistant")
    ap.add_argument("--fast-duration", type=int, default=1800, help="the duration in seconds after which Fast Sampling reverts to the normal interval; set to 0 to stay on until switched off")
    ap.add_argument("--sample-period", type=int, default=10, help="the duration in seconds between sensor samples")
    ap.add_argument("--delay", type=int, default=15, help="the duration in seconds to allow the sensors to stabilise before starting to publish readings")
    ap.add_argument("--use-pms5003", action="store_true", help="if set, PM readings will be taken from the PMS5003 sensor")
    ap.add_argument("--use-noise", action="store_true", help="if set, sound level readings will be taken from the MEMS microphone")
    ap.add_argument("--use-cpu-comp", action="store_true", help="Use the CPU temp compensation.")
    ap.add_argument("--no-retain-config", dest='retain_config', action="store_false", help="Do not set RETAIN flag on config messages.")
    ap.add_argument("--retain-state", action="store_true", help="Set RETAIN flag on state messages.")
    ap.add_argument("--cpu-comp-factor", type=float, default=2.25, help="The factor to use for the CPU temp compensation. Decrease this number to adjust the temperature down, and increase to adjust up.")
    ap.add_argument("--use-tls", action="store_true", help="Enable TLS for the MQTT connection.")
    ap.add_argument("--gas-oxidising-baseline", type=float, default=None, help="Optional clean-air baseline for the oxidising channel in kOhm. Enables a NO2/oxidising relative index.")
    ap.add_argument("--gas-reducing-baseline", type=float, default=None, help="Optional clean-air baseline for the reducing channel in kOhm. Enables a CO/reducing relative index.")
    ap.add_argument("--gas-nh3-baseline", type=float, default=None, help="Optional clean-air baseline for the NH3 channel in kOhm. Enables an NH3 relative index.")
    ap.add_argument("--help", action="help", help="print this help message and exit")
    return vars(ap.parse_args())


def main():
    args = parse_args()
    sample_period = max(1, args["sample_period"])
    try:
        gas_baselines = build_gas_baselines(
            oxidising=args["gas_oxidising_baseline"],
            reducing=args["gas_reducing_baseline"],
            nh3=args["gas_nh3_baseline"],
        )
    except ValueError as exc:
        sys.exit(str(exc))

    logging.info("Starting with arguments: %s", args)

    # Set expire_after to 2× the publish interval so Home Assistant marks the
    # sensor as unavailable if two consecutive publishes are missed.
    expire_after = args["interval"] * 2

    discovery = HassDiscovery(
        use_pms5003=args["use_pms5003"],
        use_noise=args["use_noise"],
        prefix=args["prefix"],
        retain=args["retain_config"],
        expire_after=expire_after,
        gas_baselines=gas_baselines,
    )

    if args['print_sensors']:
        for sensor in discovery.sensors:
            print("Key: ",sensor)
            print("Payload: ",discovery.sensors[sensor])
            print("Config Topic:",discovery.sensors[sensor].get_config_topic(discovery.client_id,discovery.prefix))
        print("Manual publish command topic:",discovery.command_topic)
        print("Fast Sampling command topic:",discovery.fast_command_topic)
        exit()

    # Initialise the publisher
    publisher = MqttPublisher(
        client_id=args["client_id"],
        host=args["host"],
        port=args["port"],
        username=args["username"],
        password=args["password"],
        use_tls=args["use_tls"],
        on_connect=discovery.publish if not args['delete_sensors'] else discovery.publish_delete
    )

    if args['delete_sensors']:
        time.sleep(args["delay"])
        exit()

    # Pressing the "Publish Now" button in Home Assistant sets this event, which
    # both wakes the sampling loop out of its sleep and requests a publish.
    trigger = threading.Event()
    publisher.subscribe(discovery.command_topic, lambda payload: trigger.set())

    # systemctl stop sends SIGTERM, which Python does not unwind by default.
    # Without a handler the process is killed mid-loop and the `finally` below
    # never runs, so the broker never gets a clean MQTT DISCONNECT. Set the
    # stop flag and reuse `trigger` to wake the loop immediately rather than
    # waiting out the rest of the sample period.
    stop = threading.Event()

    def handle_sigterm(signum, frame):
        stop.set()
        trigger.set()

    signal.signal(signal.SIGTERM, handle_sigterm)

    # Subscription callbacks run on paho's network thread, so a Fast Sampling
    # command is only queued here and applied by the loop.  Resizing the sample
    # window off-thread could tear a concurrent aggregation.
    fast_requests = queue.SimpleQueue()

    def on_fast_command(payload):
        fast_requests.put(payload.strip().upper() == "ON")
        trigger.set()

    publisher.subscribe(discovery.fast_command_topic, on_fast_command)

    def publish_fast_state(enabled):
        # Retained so Home Assistant recovers the switch position after a
        # restart of either end, rather than showing it as unknown.
        publisher.publish(discovery.fast_state_topic, "ON" if enabled else "OFF", retain=True)

    def sample_count(interval):
        return max(1, math.ceil(interval / sample_period))

    normal_interval = args["interval"]
    fast_interval = max(1, args["fast_interval"])
    current_interval = normal_interval
    fast_deadline = None
    publish_fast_state(False)

    acquire = EnviroPlus(
        use_pms5003=args["use_pms5003"],
        use_noise=args["use_noise"],
        num_samples=sample_count(normal_interval),
        use_cpu_comp=args["use_cpu_comp"],
        cpu_comp_factor=args["cpu_comp_factor"]
    )

    def set_interval(interval):
        # The sample window has to track the interval: publishing more often
        # while still aggregating a long history would emit overlapping
        # readings rather than a finer-grained view.
        nonlocal current_interval, next_publish_time
        if interval == current_interval:
            return
        current_interval = interval
        acquire.resize_window(sample_count(interval))
        next_publish_time = time.time() + interval
        logging.info("Publish interval is now %ss", interval)

    # Take readings without publishing them for the specified delay period,
    # to allow the sensors time to warm up and stabilise
    publish_start_time = time.time() + args["delay"]
    while time.time() < publish_start_time and not stop.is_set():
        acquire.update()
        trigger.wait(min(sample_period, max(publish_start_time - time.time(), 0)))
        trigger.clear()

    # Start taking readings and publishing them at the specified interval
    next_sample_time = time.time()
    next_publish_time = time.time() + current_interval

    try:
        while not stop.is_set():
            if publisher.connection_error is not None:
                sys.exit(f"Connecting to the MQTT server failed: {publisher.connection_error}")

            while not fast_requests.empty():
                enabled = fast_requests.get()
                set_interval(fast_interval if enabled else normal_interval)
                fast_deadline = time.time() + args["fast_duration"] if enabled and args["fast_duration"] > 0 else None
                publish_fast_state(enabled)

            if fast_deadline is not None and time.time() >= fast_deadline:
                fast_deadline = None
                set_interval(normal_interval)
                publish_fast_state(False)
                logging.info("Fast Sampling expired after %ss", args["fast_duration"])

            should_publish = time.time() >= next_publish_time
            if should_publish:
                next_publish_time += current_interval
            elif trigger.is_set():
                should_publish = True
                # Re-base the schedule so the next automatic publish is a full
                # interval after the manual one rather than moments later.
                next_publish_time = time.time() + current_interval
            trigger.clear()

            acquire.update()

            if should_publish:
                aggregated = EnviroPlus.aggregate_samples(acquire.samples)
                aggregated = apply_gas_indices(aggregated, gas_baselines)
                for sensor_name, sensor_value in aggregated.items():
                    if sensor_name not in discovery.sensors:
                        continue
                    value = SensorPayload(value=sensor_value)
                    topic = discovery.sensors[sensor_name].state_topic
                    publisher.publish_json(topic, value, retain=args['retain_state'])

            next_sample_time += sample_period
            sleep_duration = max(next_sample_time - time.time(), 0)
            # Bail out here rather than in trigger.wait() below: a SIGTERM
            # arriving between the trigger.clear() above and this point would
            # otherwise be swallowed, forcing a wait of the full sleep_duration.
            if stop.is_set():
                break
            # Event.wait() returns as soon as the button is pressed, so a manual
            # publish is not held up until the end of the sample period.
            trigger.wait(sleep_duration)

    finally:
        publisher.destroy()


if __name__ == "__main__":
    main()
