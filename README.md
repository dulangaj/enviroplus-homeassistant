# enviroplus-homeassistant

Publishes Pimoroni Enviro+ sensor readings to Home Assistant via MQTT Discovery.

## Prerequisites

```bash
sudo apt install libatlas3-base
```

## Install

```bash
CFLAGS="-fcommon" poetry install
```

`CFLAGS="-fcommon"` is required to build `rpi.gpio` under GCC 10 (Bullseye).

## Run

```bash
poetry run python -m enviroplus_homeassistant -h <mqtt-host> [options]
```

## Options

| Flag | Default | Description |
|---|---|---|
| `-h`, `--host` | *(required)* | MQTT broker hostname or IP |
| `-p`, `--port` | `1883` | MQTT broker port |
| `-U`, `--username` | | MQTT username |
| `-P`, `--password` | | MQTT password |
| `--use-tls` | | Enable TLS for the MQTT connection |
| `--prefix` | `homeassistant` | MQTT topic prefix |
| `--client-id` | | MQTT client identifier |
| `--interval` | `300` | Seconds between publishes |
| `--sample-period` | `10` | Seconds between sensor samples |
| `--delay` | `15` | Warm-up seconds before the first publish |
| `--use-pms5003` | | Enable PMS5003 particulate sensor (PM1/2.5/10) |
| `--use-noise` | | Enable MEMS microphone (see [Noise sensor](#noise-sensor)) |
| `--use-cpu-comp` | | Apply CPU temperature compensation to the temperature reading |
| `--cpu-comp-factor` | `2.25` | CPU compensation factor; decrease to read lower, increase to read higher |
| `--gas-oxidising-baseline` | | Clean-air oxidising baseline in `kOhm`; enables the relative `NO2` index |
| `--gas-reducing-baseline` | | Clean-air reducing baseline in `kOhm`; enables the relative `CO` index |
| `--gas-nh3-baseline` | | Clean-air `NH3` baseline in `kOhm`; enables the relative `NH3` index |
| `--no-retain-config` | | Do not set RETAIN on discovery config messages |
| `--retain-state` | | Set RETAIN on state messages |
| `--delete-sensors` | | Publish empty discovery payloads to remove sensors from Home Assistant, then exit |
| `--print-sensors` | | Print all sensor keys and discovery topics, then exit |

## systemd service

Copy `enviroplus-ha.service.example` to `enviroplus-ha.service`, fill in your values, then symlink it into systemd:

```bash
cp enviroplus-ha.service.example enviroplus-ha.service
# edit enviroplus-ha.service with your user, mqtt host, and any options
sudo ln -s $(pwd)/enviroplus-ha.service /etc/systemd/system/enviroplus-ha.service
sudo systemctl daemon-reload
sudo systemctl enable enviroplus-ha.service
sudo systemctl start enviroplus-ha.service
```

---

## Gas channels

The Enviro+ gas sensor reports three metal-oxide resistance channels, not directly calibrated gas concentrations:

- `gas_oxidising`: oxidising channel resistance in `kOhm` that mainly tracks `NO2`-like gases
- `gas_reducing`: reducing channel resistance in `kOhm` that responds to `CO` and other reducing gases
- `gas_nh3`: `NH3` channel resistance in `kOhm`

These raw resistance values are the most honest default output. Lower resistance generally means more gas for the reducing and `NH3` channels, while higher resistance generally means more gas for the oxidising channel.

If you have captured a stable clean-air baseline for one or more gas channels, the app can also publish unitless relative indices. The formulas are chosen so that a larger value always means "more gas than baseline":

- `NO2 / oxidising index = Rs / R0`
- `CO / reducing index = R0 / Rs`
- `NH3 index = R0 / Rs`

These derived values are trend indicators only. They are not trustworthy `ppm` measurements without per-device calibration, gas-specific response curves, and temperature/humidity compensation.

### Capturing a baseline

Run the service without baselines for several weeks, then query your Home Assistant database for the median resistance in clean-air conditions. Use the upper percentile (p80–p90) for reducing and NH3 channels (high resistance = clean air), and the lower percentile (p10–p50) for the oxidising channel (low resistance = clean air).

```sql
SELECT
  sm.statistic_id,
  ROUND(AVG(s.mean), 2) AS mean_kohm,
  ROUND(MIN(s.min), 2)  AS min_kohm,
  ROUND(MAX(s.max), 2)  AS max_kohm
FROM statistics s
JOIN statistics_meta sm ON s.metadata_id = sm.id
WHERE sm.statistic_id IN (
    'sensor.airquality_gas_nh3',
    'sensor.airquality_gas_oxidising',
    'sensor.airquality_gas_reducing'
  )
  AND s.created_ts > unixepoch('now', '-30 days')
GROUP BY sm.statistic_id;
```

## PMS5003 particulate sensor

Enable with `--use-pms5003` and set the `PMS5003_DEVICE` environment variable to the serial port the sensor is connected to (default `/dev/serial0`). See `enviroplus-ha.service.example` for how to set this in the service file.

### Serial UART stability on Pi 3

The Pi 3 has two UARTs. By default the full UART (`ttyAMA0`) is assigned to the Bluetooth radio, and the GPIO serial pins are given the mini UART (`ttyS0`). The mini UART is rate-limited and less reliable, which causes PMS5003 read timeouts.

The fix is to disable Bluetooth so the full UART is freed up for the GPIO pins:

```bash
# Disable the Bluetooth UART overlay so ttyAMA0 is available on the GPIO pins
echo 'dtoverlay=disable-bt' | sudo tee -a /boot/firmware/config.txt

# Disable the Bluetooth modem service that initialises the BT hardware at boot
sudo systemctl disable hciuart

sudo reboot
```

After reboot, verify `/dev/serial0` now points to the full UART:

```bash
ls -la /dev/serial0   # should read -> ttyAMA0
```

> Note: this permanently disables Bluetooth on the Pi. For a dedicated sensor node that is the cleanest solution. If you need Bluetooth, see the Raspberry Pi documentation for alternative UART configurations.

## Noise sensor

The Enviro+ carries an ADAU7002 I2S MEMS microphone. Enable it with `--use-noise` after completing this one-time Pi setup:

```bash
# 1. Enable the I2S overlay
echo 'dtoverlay=adau7002' | sudo tee -a /boot/firmware/config.txt

# 2. Reboot
sudo reboot

# 3. Verify the device is visible
arecord -l
```

Four sensors are added: Noise Level, Noise Low Frequency, Noise Mid Frequency, and Noise High Frequency. Values are relative FFT amplitudes, not calibrated dB SPL.

## Debugging

```bash
# Check whether the service is running and see its last few log lines
sudo systemctl status enviroplus-ha.service

# Stream live logs (Ctrl+C to stop)
sudo journalctl -u enviroplus-ha.service -f

# Show all logs since the last boot
sudo journalctl -u enviroplus-ha.service -b

# Show the last 100 lines
sudo journalctl -u enviroplus-ha.service -n 100

# Restart after changing the service file or code
sudo systemctl daemon-reload && sudo systemctl restart enviroplus-ha.service

# Verify the MQTT broker is reachable and receiving messages
mosquitto_sub -h <mqtt-host> -p 1883 -t "homeassistant/#" -v

# Check which Python and venv are being used
sudo systemctl cat enviroplus-ha.service
```
