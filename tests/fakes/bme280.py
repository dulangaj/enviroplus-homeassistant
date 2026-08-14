# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Fake BME280 temperature/pressure/humidity sensor. See README.md."""

from _profile import next_value


class BME280:
    def get_temperature(self):
        return next_value("bme280", "temperature")

    def get_pressure(self):
        return next_value("bme280", "pressure")

    def get_humidity(self):
        return next_value("bme280", "humidity")
