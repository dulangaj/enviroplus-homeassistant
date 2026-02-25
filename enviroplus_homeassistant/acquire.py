# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import collections, threading, traceback, os, time, statistics

try:
    # Transitional fix for breaking change in LTR559
    from ltr559 import LTR559
    ltr559 = LTR559()
except ImportError:
    import ltr559

from bme280 import BME280
from pms5003 import PMS5003, ReadTimeoutError as PmsReadTimeoutError
from enviroplus import gas
from atmos import calculate

# Sensors whose readings are characteristically noisy / spiky (e.g. optical
# particle counters, electrochemical cells).  A median aggregation is more
# robust for these than a mean.
_MEDIAN_SENSORS = {"pm1", "pm25", "pm10", "gas_oxidising", "gas_reducing", "gas_nh3"}


class EnviroPlus:
    def __init__(self, use_pms5003, num_samples, use_cpu_comp: bool = True, cpu_num_samples: int = 5, cpu_comp_factor: float = 2.25, sample_period: float = 0):
        self.bme280 = BME280()
        # How long (seconds) the PMS5003 thread should idle between reads.
        # Keeping this aligned with the main-loop sample period prevents the
        # thread from reading the sensor far faster than data is consumed and
        # spinning the CPU unnecessarily (which generates heat and shortens
        # Raspberry Pi component life).  Two seconds of margin is kept for
        # the sensor's ~1 s frame output interval.
        self._pms_idle = max(0.0, sample_period - 2.0)

        self.samples = collections.deque(maxlen=num_samples)
        self.use_cpu_comp = use_cpu_comp
        if self.use_cpu_comp:
            self.cpu_comp_factor = cpu_comp_factor
            # EMA state for CPU temperature.  alpha is derived from cpu_num_samples
            # using the standard conversion α = 2 / (N + 1) so that the EMA has
            # roughly the same "memory" as an N-point simple moving average while
            # being more responsive to recent changes.
            self._cpu_ema_alpha = 2.0 / (cpu_num_samples + 1)
            self._cpu_ema = None  # seeded on the first sample
        else:
            self.cpu_comp_factor = None
            self._cpu_ema = None
            self._cpu_ema_alpha = None

        self._pms_lock = threading.Lock()
        self._latest_pms_readings = {}

        if use_pms5003:
            self.pm_thread = threading.Thread(target=self.__read_pms_continuously)
            self.pm_thread.daemon = True
            self.pm_thread.start()

    # ------------------------------------------------------------------
    # PMS5003 continuous-polling thread
    # ------------------------------------------------------------------

    def __read_pms_continuously(self):
        """Continuously reads from the PMS5003 sensor and stores the most recent
        values in ``self._latest_pms_readings`` as they become available.

        If the sensor is not polled continuously then readings are buffered on
        the PMS5003, and over time a significant delay is introduced between
        changes in PM levels and the corresponding change in reported levels."""

        configured_device = os.getenv("PMS5003_DEVICE")
        candidate_devices = (
            [configured_device]
            if configured_device
            else ["/dev/serial0", "/dev/ttyAMA0", "/dev/ttyS0"]
        )
        pms = None
        while True:
            if pms is None:
                for device in candidate_devices:
                    try:
                        pms = PMS5003(device=device)
                        print(f"PMS5003 connected on {device}")
                        break
                    except Exception:
                        print(f"Failed to initialise PMS5003 on {device}")
                        traceback.print_exc()

                if pms is None:
                    print("Unable to initialise PMS5003 on any candidate device. Retrying in 5 seconds.")
                    time.sleep(5)
                    continue

            try:
                pm_data = pms.read()
                new_readings = {
                    "pm1": pm_data.pm_ug_per_m3(1.0, atmospheric_environment=True),
                    "pm25": pm_data.pm_ug_per_m3(2.5, atmospheric_environment=True),
                    "pm10": pm_data.pm_ug_per_m3(None, atmospheric_environment=True),
                }
                with self._pms_lock:
                    self._latest_pms_readings = new_readings
                # Idle until the next sample is due.  Without this sleep the
                # thread reads a new frame every ~1 s and discards most of
                # them, burning CPU and producing heat for no benefit.
                if self._pms_idle > 0:
                    time.sleep(self._pms_idle)
            except PmsReadTimeoutError:
                # ReadTimeoutError is a normal, expected condition: the PMS5003
                # operates in a periodic duty cycle and will not respond while
                # its fan/laser are spun down.  A brief pause before the next
                # read attempt is all that is needed; resetting the sensor is
                # unnecessary and can itself cause further timeouts.
                time.sleep(1)
            except Exception:
                print("Failed to read from PMS5003. Resetting sensor.")
                traceback.print_exc()
                try:
                    pms.reset()
                except Exception:
                    print("Failed to reset PMS5003. Reinitialising.")
                    traceback.print_exc()
                    pms = None
                # Brief pause before retrying so a persistent hardware fault
                # does not spin the thread at 100 % CPU.
                time.sleep(1)

    # ------------------------------------------------------------------
    # Sensor reading
    # ------------------------------------------------------------------

    def take_readings(self):
        gas_data = gas.read_all()
        readings = {
            #"proximity": ltr559.get_proximity(),
            "illuminance": ltr559.get_lux(),
            "temperature": self.bme280.get_temperature(),
            "pressure": self.bme280.get_pressure(),
            "humidity": self.bme280.get_humidity(),
            "gas_oxidising": gas_data.oxidising / 1e3,  # kOhm
            "gas_reducing": gas_data.reducing / 1e3,   # kOhm
            "gas_nh3": gas_data.nh3 / 1e3,             # kOhm
        }

        if self.use_cpu_comp:
            readings = self.compensate_readings(readings)

        with self._pms_lock:
            readings.update(self._latest_pms_readings)

        return readings

    def compensate_readings(self, readings):
        cpu_temp = self.get_cpu_temperature()

        # Update the Exponential Moving Average for CPU temperature.  EMA
        # responds more quickly to sustained changes than a simple rolling
        # mean while still smoothing transient spikes.
        if self._cpu_ema is None:
            self._cpu_ema = cpu_temp  # seed with the first measurement
        else:
            self._cpu_ema = self._cpu_ema_alpha * cpu_temp + (1 - self._cpu_ema_alpha) * self._cpu_ema

        t_precomp = readings["temperature"]  # RH source temp

        readings["temperature"] = readings["temperature"] - ((self._cpu_ema - readings["temperature"]) / self.cpu_comp_factor)

        ah = calculate('AH', RH=readings["humidity"], p=readings['pressure'], p_unit="hPa", T=t_precomp, T_unit="degC")
        # Using the virtual temperature is close enough
        rh_comp = calculate('RH', AH=ah, p=readings['pressure'], p_unit="hPa", Tv=readings["temperature"], Tv_unit="degC")
        readings["humidity"] = rh_comp

        return readings

    def update(self):
        self.samples.append(self.take_readings())

    def get_cpu_temperature(self):
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp = f.read()
            temp = int(temp) / 1000.0
        return temp

    # ------------------------------------------------------------------
    # Statistical aggregation
    # ------------------------------------------------------------------

    @staticmethod
    def _iqr_filtered_mean(values: list) -> float:
        """Return the mean of *values* after removing Tukey-fence outliers.

        Uses the standard 1.5 × IQR rule.  Falls back to the plain mean when
        there are fewer than 4 values (not enough data to estimate quartiles
        reliably).
        """
        if len(values) < 4:
            return statistics.mean(values)

        q1, _, q3 = statistics.quantiles(values, n=4)
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        filtered = [v for v in values if lower <= v <= upper]
        # Guard against the (unlikely) case where all values are outliers
        return statistics.mean(filtered) if filtered else statistics.mean(values)

    @staticmethod
    def aggregate_samples(samples) -> dict:
        """Aggregate a collection of sensor sample dicts into a single dict.

        Aggregation strategy per sensor type:
        - **Noisy / spiky sensors** (PM1/2.5/10, gas readings): **median** –
          robust against the transient spikes that optical particle counters
          and electrochemical gas sensors produce.
        - **Smooth sensors** (temperature, humidity, pressure, illuminance):
          **IQR-filtered mean** – removes statistical outliers caused by
          sensor glitches before averaging, which is more accurate than a
          plain mean while preserving the expected precision of these sensors.
        """
        sensor_values: dict[str, list] = {}
        for sample in samples:
            for sensor_name, sensor_value in sample.items():
                sensor_values.setdefault(sensor_name, []).append(sensor_value)

        result = {}
        for sensor_name, values in sensor_values.items():
            if not values:
                continue
            if sensor_name in _MEDIAN_SENSORS:
                result[sensor_name] = statistics.median(values)
            else:
                result[sensor_name] = EnviroPlus._iqr_filtered_mean(values)

        return result
