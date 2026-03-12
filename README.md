# enviroplus-homeassistant
Pimoroni Enviro+ MQTT publisher with Home Assistant Discovery written in Python.
Uses [pimoroni/enviroplus-python](https://github.com/pimoroni/enviroplus-python) and the excellent [paho-mqtt](https://www.eclipse.org/paho/index.php?page=clients/python/index.php) client.

Clone this repository to for example your home directory with

```
git clone https://github.com/EraYaN/enviroplus-homeassistant.git
``` 

**Note you might need the package `libatlas3-base` installed with `sudo apt install libatlas3-base` for this to all work at runtime.**
## Installing with pip
```
pip3 install -r requiremnets.txt
```

## Installing with poetry
The `CFLAGS="-fcommon"` is required because of the newer bundled gcc (10) for building `rpi.gpio` which [piwheels](https://piwheels.org) does not build for python 3.9 that ships with bullseye.
```
CFLAGS="-fcommon" poetry install
```

## Gas channels
The Enviro+ gas sensor reports three metal-oxide resistance channels, not directly calibrated gas concentrations:

- `gas_oxidising`: oxidising channel resistance in `kOhm` that mainly tracks `NO2`-like gases
- `gas_reducing`: reducing channel resistance in `kOhm` that responds to `CO` and other reducing gases
- `gas_nh3`: `NH3` channel resistance in `kOhm`

These raw resistance values are the most honest default output. Lower resistance generally means more gas for the reducing and `NH3` channels, while higher resistance generally means more gas for the oxidising channel.

If you have captured a stable clean-air baseline for one or more gas channels, you can also publish unitless relative indices:

```
--gas-oxidising-baseline <kOhm>
--gas-reducing-baseline <kOhm>
--gas-nh3-baseline <kOhm>
```

The published indices use the baseline-normalized formulas below so that a larger value always means "more gas than baseline":

- `NO2 / oxidising index = Rs / R0`
- `CO / reducing index = R0 / Rs`
- `NH3 index = R0 / Rs`

These derived values are trend indicators only. They are not trustworthy `ppm` measurements without per-device calibration, gas-specific response curves, and temperature/humidity compensation.

## SystemD unit
Run `poetry run bash -c 'which python3'` to get the python path in the virtual env. If you are using the system python it is most likely `/usr/bin/python3`
Add a new file `/etc/systemd/system/enviroplus-homeassistant.service` with the following content and replace the `<`*tags*`>`.
```
[Unit]
Description=Enviro+ MQTT Home Assistant
After=network.target

[Service]
ExecStart=<python_path> -m enviroplus_homeassistant <arguments>
WorkingDirectory=/home/pi/enviroplus-homeassistant
StandardOutput=inherit
StandardError=inherit
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
```
and then 
```
sudo systemctl enable enviroplus-homeassistant.service
sudo systemctl start enviroplus-homeassistant.service
```
