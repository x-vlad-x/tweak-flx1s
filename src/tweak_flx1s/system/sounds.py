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
import shutil
from loguru import logger
from tweak_flx1s.utils import run_command
from tweak_flx1s.const import HOME_DIR

class SoundManager:
    """Manages custom sound themes."""

    def __init__(self):
        self.SOUND_DIR = os.path.join(HOME_DIR, ".local/share/sounds/__custom")
        self.APP_SHARE_DIR = "/usr/share/tweak-flx1s"
        if not os.path.exists(os.path.join(self.APP_SHARE_DIR, "sounds")):

             self.APP_SHARE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../data/share"))

    def is_custom_sounds_installed(self):
        """Checks if the custom sound folder exists and is populated."""
        return os.path.exists(os.path.join(self.SOUND_DIR, "index.theme"))

    def is_custom_theme_active(self):
        """Checks if the custom sound theme is currently active in gsettings."""
        try:
            out = run_command("gsettings get org.gnome.desktop.sound theme-name", check=False)
            if not out:
                return False
            return "__custom" in out.strip("'") and self.is_custom_sounds_installed()
        except Exception:
            return False

    def install_custom_sounds(self):
        """Copies custom sounds to local share."""
        try:
            src = os.path.join(self.APP_SHARE_DIR, "sounds", "__custom")
            if not os.path.exists(src):
                logger.error(f"Source sounds dir not found at {src}")
                return False

            if os.path.exists(self.SOUND_DIR):
                shutil.rmtree(self.SOUND_DIR)

            shutil.copytree(src, self.SOUND_DIR)
            logger.info(f"Installed custom sounds to {self.SOUND_DIR}")
            return True
        except Exception as e:
            logger.error(f"Failed to install custom sounds: {e}")
            return False

    def enable_custom_theme(self):
        """Enables the custom sound theme."""
        if not self.is_custom_sounds_installed():
            if not self.install_custom_sounds():
                return False

        run_command("gsettings set org.gnome.desktop.sound theme-name '__custom'", check=False)
        return True

    def disable_custom_theme(self):
        """Reverts to default sound theme."""
        run_command("gsettings set org.gnome.desktop.sound theme-name 'default'", check=False)
        return True
