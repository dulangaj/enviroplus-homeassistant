# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Fake PMS5003 particulate sensor. See README.md.

``read()`` is called in a tight loop by a background thread in acquire.py,
so it sleeps briefly to mimic the real sensor's ~1s cadence instead of
spinning hot.
"""

import time

from _profile import next_value

_READ_INTERVAL_SECONDS = 1


class _PmReading:
    def __init__(self, pm1, pm25, pm10):
        self._by_size = {1.0: pm1, 2.5: pm25, None: pm10}

    def pm_ug_per_m3(self, size, atmospheric_environment=True):
        return self._by_size[size]


class PMS5003:
    def __init__(self, device=None):
        self.device = device

    def read(self):
        time.sleep(_READ_INTERVAL_SECONDS)
        return _PmReading(
            next_value("pms5003", "pm1"),
            next_value("pms5003", "pm25"),
            next_value("pms5003", "pm10"),
        )

    def reset(self):
        pass
