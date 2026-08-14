# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Fake LTR559 light/proximity sensor. See README.md.

Satisfies both import forms used by acquire.py: ``from ltr559 import
LTR559`` followed by instantiation, and the ``import ltr559`` fallback
(module-level functions delegate to a default instance).
"""

from _profile import next_value


class LTR559:
    def get_lux(self):
        return next_value("ltr559", "lux")


_default = LTR559()
get_lux = _default.get_lux
