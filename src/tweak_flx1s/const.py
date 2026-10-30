# Copyright (C) 2026 alaraajavamma aki@urheiluaki.fi
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
import os

APP_ID = "io.FuriOS.Tweak-FLX1s"
APP_NAME = "Tweak-FLX1s"
VERSION = "0.1.0"

HOME_DIR = os.path.expanduser("~")
CONFIG_DIR = os.path.join(HOME_DIR, ".config", "tweak-flx1s")
CACHE_DIR = os.path.join(HOME_DIR, ".cache", "tweak-flx1s")

SERVICE_ALARM = "tweak-flx1s-alarm.service"
SERVICE_GESTURES = "tweak-flx1s-gestures.service"
SERVICE_DATAMONITOR = "tweak-flx1s-datamonitor.service"
SERVICE_OFFLINE_PAUSE = "tweak-flx1s-offline-pause.service"

ICON_APP = "io.FuriOS.Tweak-FLX1s"
