# Copyright (C) 2026 Vladislav Shadiuk xvladx@protonmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""Andromeda output parsing and native settings invocation (no phone needed)."""
import unittest
from unittest.mock import patch

from tweak_flx1s.system import android


class AndroidTest(unittest.TestCase):
    def test_app_list(self):
        output = ("packageName: stale.package\nName: Zed\npackageName: org.example.Zed\n"
                  "Name: Alpha\npackageName: org.example.Alpha\n"
                  "Name: Bad\npackageName: ../../bad\n")
        self.assertEqual(android.parse_apps(output),
                         [("org.example.Alpha", "Alpha"), ("org.example.Zed", "Zed")])

    @patch.object(android.subprocess, "run")
    def test_native_settings(self, run):
        android.open_battery_settings("org.example.App")
        self.assertEqual(run.call_args.args[0], ["andromeda", "app", "intent",
                         "android.settings.VIEW_ADVANCED_POWER_USAGE_DETAIL",
                         "package:org.example.App"])
        run.reset_mock()
        with self.assertRaises(ValueError):
            android.open_battery_settings("../bad")
        run.assert_not_called()
