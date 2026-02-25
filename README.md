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

> `CFLAGS="-fcommon"` is required to build `rpi.gpio` under GCC 10 (Bullseye).

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
| `--cpu-comp-factor` | `2.25` | CPU compensation factor — decrease to read lower, increase to read higher |
| `--no-retain-config` | | Do not set RETAIN on discovery config messages |
| `--retain-state` | | Set RETAIN on state messages |
| `--delete-sensors` | | Publish empty discovery payloads to remove sensors from Home Assistant, then exit |
| `--print-sensors` | | Print all sensor keys and discovery topics, then exit |

## Noise sensor

The Enviro+ carries an ADAU7002 I2S MEMS microphone. Enable it with `--use-noise` after completing this one-time Pi setup:

```bash
# 1. Enable the I2S overlay
echo 'dtoverlay=adau7002' | sudo tee -a /boot/config.txt

# 2. Reboot
sudo reboot

# 3. Verify the device is visible
arecord -l   # should list an adau7002 capture device
```

Four sensors are added: **Noise Level** (total amplitude), **Noise Low Frequency** (~0–960 Hz), **Noise Mid Frequency** (~960–2880 Hz), **Noise High Frequency** (~2880–8000 Hz). Values are relative FFT amplitudes, not calibrated dB SPL.

## systemd service

Get the Python path from your virtual environment:

```bash
poetry run bash -c 'which python3'
```

Create `/etc/systemd/system/enviroplus-homeassistant.service`:

```ini
[Unit]
Description=Enviro+ MQTT Home Assistant
After=network.target

[Service]
ExecStart=<python_path> -m enviroplus_homeassistant -h <mqtt-host> [options]
WorkingDirectory=/home/pi/enviroplus-homeassistant
StandardOutput=inherit
StandardError=inherit
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable enviroplus-homeassistant.service
sudo systemctl start enviroplus-homeassistant.service
```
