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
import filecmp
from loguru import logger
from tweak_flx1s.const import HOME_DIR

class WofiManager:
    """Manages Wofi configuration."""

    def __init__(self):
        self.WOFI_CONFIG_DIR = os.path.join(HOME_DIR, ".config/wofi")
        self.APP_CONFIG_DIR = "/usr/share/tweak-flx1s/configs/wofi"
        if not os.path.exists(self.APP_CONFIG_DIR):
             self.APP_CONFIG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../data/configs/wofi"))

    def generate_dynamic_style(self):
        """Generates style.css dynamically based on system theme and accent color."""
        bg_color = "#1e1e1e"
        fg_color = "#ffffff"
        accent_color = "#3584e4"
        entry_bg_color = "#2d2d2d"

        is_dark = True
        try:
            import subprocess
            res = subprocess.check_output(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"], text=True).strip()
            if "prefer-light" in res:
                is_dark = False
            else:
                theme = subprocess.check_output(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"], text=True).strip().lower()
                if "dark" not in theme:
                    is_dark = False
        except Exception:
            pass

        if not is_dark:
            bg_color = "#f6f6f6"
            fg_color = "#000000"
            entry_bg_color = "#e8e8e8"

        try:
            import subprocess
            acc = subprocess.check_output(["gsettings", "get", "org.gnome.desktop.interface", "accent-color"], text=True).strip().strip("'")
            accent_map = {
                "blue": "#3584e4",
                "teal": "#21c7b8",
                "green": "#2ec27e",
                "yellow": "#f5c211",
                "orange": "#e66100",
                "red": "#e01b24",
                "pink": "#dc8add",
                "purple": "#9141ac",
                "slate": "#6f8396"
            }
            if acc in accent_map:
                accent_color = accent_map[acc]
        except Exception:
            pass

        css = f"""window {{
    background-color: transparent;
}}

#outer {{
    background-color: {bg_color};
    border: 1px solid {entry_bg_color};
    border-radius: 24px;
    padding: 16px;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
}}

#scroll {{
    background-color: transparent;
    border: none;
}}

#inner {{
    background-color: transparent;
}}

#entry {{
    background-color: {entry_bg_color};
    color: {fg_color};
    border: none;
    border-radius: 9999px;
    margin-bottom: 8px;
    font-size: 14px;
    font-weight: 500;
    padding: 12px 24px;
    transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1);
}}

#entry:selected {{
    background-color: {accent_color};
    color: #ffffff;
    border: none;
    border-radius: 9999px;
    transform: scale(1.02);
}}

#entry:active {{
    background-color: {accent_color};
    transform: scale(0.96);
}}

#text {{
    color: inherit;
}}

#input {{
    background-color: {entry_bg_color};
    border: 1px solid {entry_bg_color};
    border-radius: 9999px;
    color: {fg_color};
    padding: 8px 16px;
    margin-bottom: 12px;
}}

#input:focus {{
    border-color: {accent_color};
}}
"""
        try:
            target = os.path.join(self.WOFI_CONFIG_DIR, "style.css")
            os.makedirs(self.WOFI_CONFIG_DIR, exist_ok=True)
            with open(target, "w") as f:
                f.write(css)
            logger.info("Dynamically generated Wofi style.css")
        except Exception as e:
            logger.error(f"Failed to generate Wofi style.css: {e}")

    def ensure_config_exists(self):
        """Ensures that Wofi config files exist."""
        if not os.path.exists(self.WOFI_CONFIG_DIR):
            os.makedirs(self.WOFI_CONFIG_DIR)

        target = os.path.join(self.WOFI_CONFIG_DIR, "config")
        if not os.path.exists(target):
            src = os.path.join(self.APP_CONFIG_DIR, "config")
            if os.path.exists(src):
                try:
                    shutil.copy(src, target)
                    logger.info("Installed Wofi config")
                except Exception as e:
                    logger.error(f"Failed to copy Wofi config: {e}")

        self.generate_dynamic_style()

    def check_config_match(self):
        """Checks if current Wofi config matches the app default."""
        target = os.path.join(self.WOFI_CONFIG_DIR, "config")
        src = os.path.join(self.APP_CONFIG_DIR, "config")

        if not os.path.exists(target) or not os.path.exists(src):
            return False

        try:
            return filecmp.cmp(src, target, shallow=False)
        except Exception:
            return False

    def force_install_config(self):
        """Overwrites Wofi config with app defaults."""
        if not os.path.exists(self.WOFI_CONFIG_DIR):
            os.makedirs(self.WOFI_CONFIG_DIR)

        src = os.path.join(self.APP_CONFIG_DIR, "config")
        target = os.path.join(self.WOFI_CONFIG_DIR, "config")

        if os.path.exists(src):
            try:
                shutil.copy(src, target)
                logger.info("Forced install of Wofi config")
            except Exception as e:
                logger.error(f"Failed to force copy Wofi config: {e}")

        self.generate_dynamic_style()
