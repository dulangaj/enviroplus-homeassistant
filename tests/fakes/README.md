# Fake hardware modules

Real modules that shadow the Enviro+ hardware libraries (`bme280`, `ltr559`,
`pms5003`, `enviroplus.gas`, `enviroplus.noise`), so `enviroplus_homeassistant`
can run unmodified on machines without the sensors. Put this directory first
on `PYTHONPATH` and normal import resolution does the rest — no
`sys.modules` patching, no production code changes.

```
PYTHONPATH=tests/fakes:. python -m enviroplus_homeassistant ...
```

## Configuring readings

Readings are controlled by a JSON file referenced by the
`ENVIROPLUS_FAKE_PROFILE` env var. If unset, fixed defaults are used.

Schema — top-level keys are sensor sections, each mapping reading names to a
spec:

```json
{
  "bme280": {
    "temperature": {"mode": "constant", "value": 21.5},
    "humidity": {"mode": "counter", "start": 40.0, "step": 0.5}
  },
  "gas": {
    "oxidising": {"mode": "constant", "value": 50000.0}
  }
}
```

Sections and their reading names:

| Section    | Readings                    | Units                     |
|------------|------------------------------|---------------------------|
| `bme280`   | `temperature`, `pressure`, `humidity` | degC, hPa, %RH  |
| `ltr559`   | `lux`                         | lux                       |
| `gas`      | `oxidising`, `reducing`, `nh3` | ohms (acquire.py converts to kOhm) |
| `pms5003`  | `pm1`, `pm25`, `pm10`          | ug/m3                     |
| `noise`    | `low`, `mid`, `high`, `amp`   | arbitrary amplitude       |

Any section/reading omitted from the profile falls back to the built-in
default. Two modes are supported per reading:

- `{"mode": "constant", "value": N}` — always returns `N`.
- `{"mode": "counter", "start": N, "step": S}` — returns `N`, then `N+S`,
  `N+2S`, ... on each successive read. Useful for identifying exactly which
  samples landed in an aggregation window without relying on timing. Being
  evenly spaced, a mean and a median computed over any contiguous run of a
  counter are always numerically identical, so it cannot show a difference
  between mean- and median-aggregated sensors.
- `{"mode": "sequence", "values": [...]}` — cycles through the list,
  wrapping around. A window length that is a multiple of the list length
  contains the same multiset of values regardless of phase, so the window's
  mean and median are deterministic even though its exact alignment against
  wall-clock samples isn't — useful for asserting *which* aggregation a
  sensor uses.
