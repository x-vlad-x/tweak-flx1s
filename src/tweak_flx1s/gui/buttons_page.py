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

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gio
from tweak_flx1s.actions.buttons import ButtonManager, PREDEFINED_ACTIONS
from tweak_flx1s.gui.dialogs import ActionSelectionDialog
from tweak_flx1s.utils import logger


class ButtonsPage(Adw.PreferencesPage):
    """Page for configuring buttons."""
    def __init__(self, **kwargs):
        super().__init__(title="Buttons", icon_name="input-keyboard-symbolic", **kwargs)
        self.manager = ButtonManager()
        self.config = self.manager.config
        self.rows = {}

        self._build_section("short_press", "Short Press")
        self._build_section("double_press", "Double Press")
        self._build_section("long_press", "Long Press")

    def _build_section(self, key, title):
        """Builds the UI section for a button press type."""
        group = Adw.PreferencesGroup(title=title)
        self.add(group)

        custom_row = Adw.SwitchRow(title="Use Custom Assistant File")
        custom_row.set_subtitle("Write ~/.config/assistant-button/...")
        custom_row.set_active(self.config.get(key, {}).get("use_custom_file", False))
        custom_row.connect("notify::active", lambda r, p, k=key: GLib.idle_add(lambda: self._on_custom_toggled(r, p, k) or False))
        group.add(custom_row)

        locked_row = Adw.ActionRow(title="Locked Action")
        self._update_subtitle(locked_row, key, "locked")

        locked_btn = Gtk.Button(label="Edit", valign=Gtk.Align.CENTER)
        locked_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_edit(b, key, "locked") or False))
        locked_row.add_suffix(locked_btn)
        group.add(locked_row)
        self.rows[(key, "locked")] = locked_row

        unlocked_row = Adw.ActionRow(title="Unlocked Action")
        self._update_subtitle(unlocked_row, key, "unlocked")

        unlocked_btn = Gtk.Button(label="Edit", valign=Gtk.Align.CENTER)
        unlocked_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_edit(b, key, "unlocked") or False))
        unlocked_row.add_suffix(unlocked_btn)
        group.add(unlocked_row)
        self.rows[(key, "unlocked")] = unlocked_row

    def _get_config_entry(self, key, state):
        if key not in self.config: self.config[key] = {}
        if state not in self.config[key]: self.config[key][state] = {}
        return self.config[key][state]

    def _update_subtitle(self, row, key, state):
        entry = self._get_config_entry(key, state)
        atype = entry.get("type", "command")
        val = entry.get("value", "")

        if atype == "wofi":
            row.set_subtitle("Wofi Menu")
        else:
            found = False
            for pname, pcmd in PREDEFINED_ACTIONS.items():
                if pcmd == val:
                    row.set_subtitle(pname)
                    found = True
                    break
            if not found:
                row.set_subtitle(val if val else "No Action")

    def _on_custom_toggled(self, row, param, key):
        if key not in self.config: self.config[key] = {}
        self.config[key]["use_custom_file"] = row.get_active()
        self.manager.save_config(self.config)

    def _on_edit(self, btn, key, state):
        entry = self._get_config_entry(key, state)

        def on_save(new_conf):
            self.config[key][state] = new_conf
            self.manager.save_config(self.config)
            row = self.rows[(key, state)]
            self._update_subtitle(row, key, state)

        dlg = ActionSelectionDialog(self.get_root(), entry, on_save)
        dlg.present()
