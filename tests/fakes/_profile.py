# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Shared configuration for the fake hardware modules in this directory.

See README.md for the JSON profile schema.
"""

import json, os, threading

_DEFAULTS = {
    "bme280": {
        "temperature": {"mode": "constant", "value": 21.5},
        "pressure": {"mode": "constant", "value": 1013.25},
        "humidity": {"mode": "constant", "value": 45.0},
    },
    "ltr559": {
        "lux": {"mode": "constant", "value": 300.0},
    },
    "gas": {
        "oxidising": {"mode": "constant", "value": 50000.0},
        "reducing": {"mode": "constant", "value": 450000.0},
        "nh3": {"mode": "constant", "value": 150000.0},
    },
    "pms5003": {
        "pm1": {"mode": "constant", "value": 5.0},
        "pm25": {"mode": "constant", "value": 8.0},
        "pm10": {"mode": "constant", "value": 12.0},
    },
    "noise": {
        "low": {"mode": "constant", "value": 0.1},
        "mid": {"mode": "constant", "value": 0.15},
        "high": {"mode": "constant", "value": 0.2},
        "amp": {"mode": "constant", "value": 0.3},
    },
}

_lock = threading.Lock()
_profile = None
_counters = {}


def _load_profile():
    global _profile
    if _profile is not None:
        return _profile

    merged = {section: dict(values) for section, values in _DEFAULTS.items()}
    path = os.environ.get("ENVIROPLUS_FAKE_PROFILE")
    if path:
        with open(path) as f:
            overrides = json.load(f)
        for section, values in overrides.items():
            merged.setdefault(section, {}).update(values)
    _profile = merged
    return merged


def next_value(section: str, key: str) -> float:
    """Return the next reading for ``section.key`` per the active profile.

    ``constant`` mode always returns ``value``. ``counter`` mode returns a
    running total that starts at ``start`` and increments by ``step`` on
    every call, so successive reads are individually distinguishable.
    """
    profile = _load_profile()
    spec = profile.get(section, {}).get(key) or _DEFAULTS[section][key]
    mode = spec.get("mode", "constant")

    if mode == "constant":
        return spec["value"]

    if mode == "counter":
        counter_key = (section, key)
        with _lock:
            current = _counters.get(counter_key, spec.get("start", 0.0))
            _counters[counter_key] = current + spec.get("step", 1.0)
            return current

    if mode == "sequence":
        # Cycles through an explicit list of values. Unlike ``counter`` (which
        # is always evenly spaced, so a mean and a median computed over any
        # contiguous run of it are mathematically identical) this can express
        # a skewed distribution, which is what distinguishes a median
        # aggregation from a mean aggregation in a test.
        values = spec["values"]
        counter_key = (section, key)
        with _lock:
            index = _counters.get(counter_key, 0)
            _counters[counter_key] = index + 1
            return values[index % len(values)]

    raise ValueError(f"Unknown profile mode {mode!r} for {section}.{key}")
