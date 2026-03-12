from __future__ import annotations

from typing import Mapping


_GAS_BASELINE_ARGUMENTS = {
    "gas_oxidising": "oxidising",
    "gas_reducing": "reducing",
    "gas_nh3": "nh3",
}

_GAS_INDEX_TRANSFORMS = {
    "gas_no2_index": ("gas_oxidising", lambda reading, baseline: reading / baseline),
    "gas_co_index": ("gas_reducing", lambda reading, baseline: baseline / reading),
    "gas_nh3_index": ("gas_nh3", lambda reading, baseline: baseline / reading),
}


def build_gas_baselines(oxidising: float = None, reducing: float = None, nh3: float = None) -> dict[str, float]:
    candidate_values = {
        "gas_oxidising": oxidising,
        "gas_reducing": reducing,
        "gas_nh3": nh3,
    }
    baselines = {}
    for sensor_name, value in candidate_values.items():
        if value is None:
            continue
        if value <= 0:
            arg_name = _GAS_BASELINE_ARGUMENTS[sensor_name]
            raise ValueError(f"gas baseline '{arg_name}' must be greater than zero")
        baselines[sensor_name] = value
    return baselines


def apply_gas_indices(readings: dict[str, float], gas_baselines: Mapping[str, float] | None) -> dict[str, float]:
    if not gas_baselines:
        return readings

    enriched_readings = dict(readings)
    for sensor_name, (source_sensor, transform) in _GAS_INDEX_TRANSFORMS.items():
        baseline = gas_baselines.get(source_sensor)
        reading = readings.get(source_sensor)
        if baseline is None or reading is None or reading <= 0:
            continue
        enriched_readings[sensor_name] = transform(reading, baseline)

    return enriched_readings
