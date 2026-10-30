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
"""Access Andromeda's app list and native battery policy screen."""
import re
import subprocess


PACKAGE_RE = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+\Z")


def parse_apps(output):
    apps = {}
    name = None
    for line in output.splitlines():
        if line.startswith("Name: "):
            name = line[len("Name: "):].strip()
        elif line.startswith("packageName: "):
            package = line[len("packageName: "):].strip()
            if name and PACKAGE_RE.fullmatch(package):
                apps[package] = name
            name = None
    return sorted(apps.items(), key=lambda item: item[1].casefold())


def list_apps():
    result = subprocess.run(["andromeda", "app", "list"], capture_output=True,
                            text=True, timeout=60, check=True)
    apps = parse_apps(result.stdout)
    if not apps:
        raise RuntimeError("No Android apps found. Check that Android is running.")
    return apps


def open_battery_settings(package):
    if not PACKAGE_RE.fullmatch(package):
        raise ValueError("Invalid Android package name")
    subprocess.run(["andromeda", "app", "intent",
                    "android.settings.VIEW_ADVANCED_POWER_USAGE_DETAIL",
                    "package:" + package], capture_output=True, text=True,
                   timeout=30, check=True)
