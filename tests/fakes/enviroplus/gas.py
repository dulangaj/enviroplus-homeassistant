# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Fake MICS6814 gas sensor. See README.md.

Readings are in ohms, matching the real ``enviroplus.gas`` module;
acquire.py divides by 1e3 to report kOhm.
"""

from _profile import next_value


class _GasReading:
    def __init__(self, oxidising, reducing, nh3):
        self.oxidising = oxidising
        self.reducing = reducing
        self.nh3 = nh3


def read_all():
    return _GasReading(
        next_value("gas", "oxidising"),
        next_value("gas", "reducing"),
        next_value("gas", "nh3"),
    )
