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

## Noise sensor

The Enviro+ carries an ADAU7002 I2S MEMS microphone. Enable it with `--use-noise` after completing this one-time Pi setup:

```bash
# 1. Enable the I2S overlay
echo 'dtoverlay=adau7002' | sudo tee -a /boot/config.txt

# 2. Reboot
sudo reboot

# 3. Verify the device is visible
arecord -l
```

Four sensors are added: Noise Level, Noise Low Frequency, Noise Mid Frequency, and Noise High Frequency. Values are relative FFT amplitudes, not calibrated dB SPL.

## systemd service

Create `/etc/systemd/system/enviroplus-ha.service`:

```ini
[Unit]
Description=Enviro+ -> MQTT Home Assistant Discovery
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
ExecStart=/bin/bash -lc 'cd /home/pi/enviroplus-homeassistant && source .venv/bin/activate && python -m enviroplus_homeassistant -h <mqtt-host> -p 1883 --client-id enviroplus --interval 300 --delay 60'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

If you have a PMS5003 particulate sensor, add the `Environment` line and `--use-pms5003` flag:

```ini
[Unit]
Description=Enviro+ -> MQTT Home Assistant Discovery
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
Environment=PMS5003_DEVICE=/dev/serial0
ExecStart=/bin/bash -lc 'cd /home/pi/enviroplus-homeassistant && source .venv/bin/activate && python -m enviroplus_homeassistant -h <mqtt-host> -p 1883 --client-id enviroplus --use-pms5003 --interval 300 --delay 60'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable enviroplus-ha.service
sudo systemctl start enviroplus-ha.service
```
