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
from tweak_flx1s.const import HOME_DIR, CACHE_DIR

class WofiManager:
    """Manages Wofi configuration."""

    def __init__(self):
        self.WOFI_CONFIG_DIR = os.path.join(HOME_DIR, ".config/wofi")
        self.APP_CONFIG_DIR = "/usr/share/tweak-flx1s/configs/wofi"
        if not os.path.exists(self.APP_CONFIG_DIR):
             self.APP_CONFIG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../data/configs/wofi"))

    def _read_theme_state(self):
        """Reads (color-scheme, gtk-theme, accent-color) in-process via
        Gio.Settings — no gsettings subprocess spawns on the press path."""
        color_scheme = ""
        gtk_theme = ""
        accent = ""
        try:
            import gi
            gi.require_version('Gio', '2.0')
            from gi.repository import Gio
            source = Gio.SettingsSchemaSource.get_default()
            schema = source.lookup("org.gnome.desktop.interface", True) if source else None
            if schema:
                settings = Gio.Settings.new("org.gnome.desktop.interface")
                if schema.has_key("color-scheme"):
                    color_scheme = settings.get_string("color-scheme")
                if schema.has_key("gtk-theme"):
                    gtk_theme = settings.get_string("gtk-theme")
                if schema.has_key("accent-color"):
                    accent = settings.get_string("accent-color")
        except Exception as e:
            logger.warning(f"Failed to read theme settings: {e}")
        return color_scheme, gtk_theme, accent

    def generate_dynamic_style(self):
        """Generates style.css dynamically based on system theme and accent
        color. Skipped when neither has changed since the last generation,
        so repeated menu opens cost no settings reads or file writes."""
        bg_color = "#1e1e1e"
        fg_color = "#ffffff"
        accent_color = "#3584e4"
        entry_bg_color = "#2d2d2d"

        color_scheme, gtk_theme, acc = self._read_theme_state()

        # Regenerate only when the theme state actually changed. The leading
        # number is the CSS template version — bump it whenever the template
        # below changes so cached copies on existing installs refresh.
        state_key = f"5|{color_scheme}|{gtk_theme}|{acc}"
        key_file = os.path.join(CACHE_DIR, "wofi-style.key")
        target = os.path.join(self.WOFI_CONFIG_DIR, "style.css")
        try:
            if os.path.exists(target) and os.path.exists(key_file):
                with open(key_file) as f:
                    if f.read() == state_key:
                        logger.debug("Wofi style.css up to date, skipping regeneration")
                        return
        except Exception:
            pass

        is_dark = True
        if "prefer-light" in color_scheme:
            is_dark = False
        elif "prefer-dark" not in color_scheme and "dark" not in gtk_theme.lower():
            is_dark = False

        if not is_dark:
            bg_color = "#f6f6f6"
            fg_color = "#000000"
            entry_bg_color = "#e8e8e8"

        try:
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

/* GTK reserves an invisible ~35px shadow margin on every side of the
   surface, which breaks pixel-exact sizing and centering — remove it. */
window decoration {{
    box-shadow: none;
    margin: 0;
    padding: 0;
}}

#outer {{
    background-color: transparent;
    padding: 0;
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
}}

#entry:selected {{
    background-color: {accent_color};
    color: #ffffff;
    border: none;
    border-radius: 9999px;
}}

#entry:active {{
    background-color: {accent_color};
}}

#text {{
    color: inherit;
}}

#input {{
    background-color: transparent;
    border: none;
    color: transparent;
    caret-color: transparent;
    outline: none;
    padding: 0;
    margin: 0;
    min-height: 0;
    font-size: 1px;
}}

#input image {{
    -gtk-icon-transform: scale(0);
}}
"""
        try:
            os.makedirs(self.WOFI_CONFIG_DIR, exist_ok=True)
            with open(target, "w") as f:
                f.write(css)
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(key_file, "w") as f:
                f.write(state_key)
            logger.info("Dynamically generated Wofi style.css")
        except Exception as e:
            logger.error(f"Failed to generate Wofi style.css: {e}")

    def _migrate_config(self):
        """Drops broken options from an existing user config. hide_scroll
        combined with our stylesheet makes wofi compute a zero-height window
        (GDK 'height > 0' assertion), and hide_search makes wofi relayout
        right after mapping, which erases the entry rows on Phosh — both make
        the menu randomly appear empty or not at all."""
        target = os.path.join(self.WOFI_CONFIG_DIR, "config")
        try:
            if not os.path.exists(target):
                return
            with open(target) as f:
                lines = f.readlines()
            kept = [l for l in lines
                    if not l.strip().startswith(("hide_scroll", "hide_search"))]
            if len(kept) != len(lines):
                with open(target, "w") as f:
                    f.writelines(kept)
                logger.info("Removed hide_scroll/hide_search from Wofi config (render bugs)")
        except Exception as e:
            logger.error(f"Failed to migrate Wofi config: {e}")

    def ensure_config_exists(self):
        """Ensures that Wofi config files exist."""
        if not os.path.exists(self.WOFI_CONFIG_DIR):
            os.makedirs(self.WOFI_CONFIG_DIR)

        self._migrate_config()

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
