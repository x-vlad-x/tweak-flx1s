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

class KeyboardManager:
    """Manages keyboard layouts and OSK selection."""

    def __init__(self):
        self.SQUEEKBOARD_DIR = os.path.join(HOME_DIR, ".local/share/squeekboard")
        self.APP_SHARE_DIR = "/usr/share/tweak-flx1s"
        if not os.path.exists(self.APP_SHARE_DIR):
             self.APP_SHARE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../data/share"))

    def check_squeekboard_installed(self):
        """Checks if squeekboard package is installed."""
        try:
            logger.info("Checking if squeekboard is installed...")
            res = run_command("dpkg -l squeekboard", check=False)
            if res and "ii  squeekboard" in res:
                 logger.info("Squeekboard found via dpkg.")
                 return True
            if shutil.which("squeekboard"):
                 logger.info("Squeekboard found via binary path.")
                 return True
            logger.warning("Squeekboard not found.")
            return False
        except Exception as e:
            logger.error(f"Failed to check squeekboard installation: {e}")
            return False

    def get_install_cmd(self):
        """Returns command to install squeekboard.

        A helper carries this rather than apt alone, because squeekboard
        wants a gnome-desktop library whose only package caps
        gnome-desktop3-data below what FuriOS ships. The helper installs
        the plain way first and only rebuilds the library when apt says
        it cannot.
        """
        return "tweak-flx1s-install-squeekboard"

    def get_remove_cmd(self):
        """Returns command to remove squeekboard and reset alternative."""
        return "apt remove -y squeekboard; update-alternatives --auto Phosh-OSK"

    def get_current_keyboard(self):
        """Returns the currently selected keyboard alternative."""
        try:
            logger.info("Fetching current keyboard...")
            out = run_command("update-alternatives --query Phosh-OSK", check=False)
            if not out:
                 logger.warning("No output from update-alternatives query.")
                 return "unknown"

            for line in out.splitlines():
                if line.startswith("Value:"):
                    path = line.split(":", 1)[1].strip()
                    logger.info(f"Current keyboard path: {path}")
                    if "squeekboard" in path: return "squeekboard"
                    if "stub" in path: return "phosh-osk-stub"
                    if "stevia" in path: return "phosh-osk-stevia"
                    return path
            return "unknown"
        except Exception as e:
            logger.error(f"Failed to get current keyboard: {e}")
            return "unknown"

    def get_available_keyboards(self):
        """
        Returns a list of available keyboard alternatives.
        """
        options = []
        try:
            logger.info("Querying available keyboards via Phosh-OSK...")
            out = run_command("update-alternatives --query Phosh-OSK", check=False)

            if out:
                blocks = out.strip().split("\n\n")
                for block in blocks:
                    lines = block.splitlines()
                    path = None
                    priority = None

                    for line in lines:
                        if line.startswith("Alternative:"):
                            path = line.split(":", 1)[1].strip()

                    if path:
                        name = "Unknown"
                        if "squeekboard" in path: name = "Squeekboard"
                        elif "stub" in path: name = "Phosh OSK (Stub)"
                        elif "stevia" in path: name = "Phosh OSK (Stevia)"
                        else: name = path

                        logger.info(f"Found alternative: {name} ({path})")
                        options.append({"name": name, "path": path})

            if not options:
                 logger.warning("No alternatives found via query.")

        except Exception as e:
            logger.error(f"Failed to list keyboards: {e}")

        return options

    def set_keyboard(self, path):
        """Returns the command to set the keyboard."""
        logger.info(f"Preparing command to set keyboard to {path}")
        return f"update-alternatives --set Phosh-OSK {path}"

    def configure_osk_service(self, keyboard_path):
        """Creates or removes the Squeekboard user service override based on the selected keyboard."""
        user_systemd_dir = os.path.join(HOME_DIR, ".config/systemd/user")
        service_file = os.path.join(user_systemd_dir, "mobi.phosh.OSK.service")

        try:
            if "squeekboard" in keyboard_path:
                logger.info("Configuring Squeekboard user service override.")
                os.makedirs(user_systemd_dir, exist_ok=True)
                service_content = """[Unit]
Description=Squeekboard On Screen Keyboard (User Override)
PartOf=mobi.phosh.OSK.target
After=mobi.phosh.OSK.target

[Service]
Type=simple
ExecStart=/usr/bin/squeekboard
Restart=on-failure
RestartSec=1
"""
                with open(service_file, 'w') as f:
                    f.write(service_content)
                logger.info(f"Created {service_file}")
            else:
                if os.path.exists(service_file):
                    logger.info("Removing Squeekboard user service override as a different keyboard is selected.")
                    os.remove(service_file)
        except Exception as e:
            logger.error(f"Failed to configure OSK service: {e}")

    def install_finnish_layout(self):
        """Copies the custom Finnish layout to ~/.local/share/squeekboard."""
        try:
            logger.info("Installing Finnish layout...")
            src = os.path.join(self.APP_SHARE_DIR, "squeekboard")
            if not os.path.exists(src):
                logger.error(f"Source squeekboard dir not found at {src}")
                return False

            if os.path.exists(self.SQUEEKBOARD_DIR):
                logger.info(f"Removing existing directory: {self.SQUEEKBOARD_DIR}")
                shutil.rmtree(self.SQUEEKBOARD_DIR)

            shutil.copytree(src, self.SQUEEKBOARD_DIR)
            logger.info(f"Installed Finnish layout to {self.SQUEEKBOARD_DIR}")
            return True
        except Exception as e:
            logger.error(f"Failed to install Finnish layout: {e}")
            return False

    def is_finnish_layout_installed(self):
        """Checks if Finnish layout is installed."""
        try:
            check_path = os.path.join(self.SQUEEKBOARD_DIR, "keyboards", "fi.yaml")
            exists = os.path.exists(check_path)
            return exists
        except Exception as e:
            logger.error(f"Failed to check Finnish layout: {e}")
            return False

    def remove_finnish_layout(self):
        """Removes the custom layout folder."""
        try:
            logger.info("Removing Finnish layout...")
            if os.path.exists(self.SQUEEKBOARD_DIR):
                shutil.rmtree(self.SQUEEKBOARD_DIR)
                logger.info("Removed Finnish layout directory.")
            return True
        except Exception as e:
             logger.error(f"Failed to remove Finnish layout: {e}")
             return False
