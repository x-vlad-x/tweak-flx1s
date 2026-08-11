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
from gi.repository import Gtk, Adw, GLib
import subprocess
import threading
from loguru import logger
from tweak_flx1s.actions.buttons import PREDEFINED_ACTIONS
from tweak_flx1s.utils import invalidate_package_cache


class ExecutionDialog(Adw.MessageDialog):
    """Dialog that runs a shell command and shows output."""
    def __init__(self, parent, title, command, as_root=False, on_finish=None):
        super().__init__(heading=title, transient_for=parent)
        self.set_default_size(300, 400)
        self.add_response("close", "Close")
        self.set_response_enabled("close", False)
        self.connect("response", lambda d, r: GLib.idle_add(lambda: self._on_response(d, r) or False))
        self.on_finish_callback = on_finish

        if as_root:
            self.command = ["pkexec", "bash", "-c", command]
        else:
            self.command = ["bash", "-c", command]

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(300)
        scrolled.set_vexpand(True)

        self.textview = Gtk.TextView()
        self.textview.set_editable(False)
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview.set_monospace(True)

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"textview { font-size: 10px; }")
        self.textview.get_style_context().add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        scrolled.set_child(self.textview)
        self.set_extra_child(scrolled)

        self.buffer = self.textview.get_buffer()

        self.process = None
        # daemon: a hung command (e.g. an unanswered pkexec prompt) must not
        # keep the whole app process alive after the window is closed.
        self.thread = threading.Thread(target=self._run_process, daemon=True)
        self.thread.start()
        # Kill the child when the dialog goes away, or it lives on orphaned.
        self.connect("destroy", self._on_destroy)

    def _on_destroy(self, *_args):
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception:
                pass

    def _run_process(self):
        try:
            GLib.idle_add(self._append_text, f"Executing: {' '.join(self.command)}\n\n")

            self.process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            while True:
                line = self.process.stdout.readline()
                if not line and self.process.poll() is not None:
                    break
                if line:
                    GLib.idle_add(self._append_text, line)

            rc = self.process.poll()
            GLib.idle_add(self._finish, rc)

        except Exception as e:
            GLib.idle_add(self._append_text, f"\nError: {e}")
            GLib.idle_add(self._finish, -1)

    def _append_text(self, text):
        iter_end = self.buffer.get_end_iter()
        self.buffer.insert(iter_end, text)
        return False

    def _finish(self, rc):
        if rc == 0:
            self.buffer.insert(self.buffer.get_end_iter(), "\n\nSuccess!")
        else:
            self.buffer.insert(self.buffer.get_end_iter(), f"\n\nFailed with code {rc}")
        self.set_response_enabled("close", True)

        # The command may have installed/removed packages — drop cached states
        # so the pages' refresh callbacks re-query reality.
        invalidate_package_cache()

        if self.on_finish_callback:
            self.on_finish_callback(rc == 0)

        return False

    def _on_response(self, dialog, response_id):
        if response_id == "close":
             GLib.idle_add(lambda: self.close() or False)

class KeyboardSelectionDialog(Adw.Window):
    """Dialog to select a keyboard from available options."""
    def __init__(self, parent, keyboards, on_select):
        super().__init__(transient_for=parent, modal=True, title="Select Keyboard")
        self.set_default_size(350, 400)
        self.keyboards = keyboards
        self.on_select = on_select

        content = Adw.ToolbarView()
        self.set_content(content)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        content.add_top_bar(header)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda x: GLib.idle_add(lambda: self.close() or False))
        header.pack_end(close_btn)

        list_box = Gtk.ListBox()
        list_box.add_css_class("boxed-list")
        list_box.set_selection_mode(Gtk.SelectionMode.NONE)

        scroll = Gtk.ScrolledWindow()
        scroll.set_child(list_box)

        clamp = Adw.Clamp()
        clamp.set_child(scroll)
        content.set_content(clamp)

        for kbd in self.keyboards:
            row = Adw.ActionRow(title=kbd["name"])
            row.set_subtitle(kbd["path"])
            row.set_title_lines(0)
            row.set_subtitle_lines(0)
            row.set_activatable(True)
            row.connect("activated", lambda r, p=kbd["path"]: GLib.idle_add(lambda: self._on_row_activated(r, p) or False))
            list_box.append(row)

    def _on_row_activated(self, row, path):
        if self.on_select:
             self.on_select(path)
        GLib.idle_add(lambda: self.close() or False)

class WofiItemEditor(Adw.Window):
    """Editor for a single Wofi menu item."""
    def __init__(self, parent, item_data, on_save):
        super().__init__(transient_for=parent, modal=True, title="Edit Item")
        self.set_default_size(350, 600)
        self.item = item_data.copy()
        self.on_save = on_save

        content = Adw.ToolbarView()
        self.set_content(content)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        content.add_top_bar(header)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_save_clicked(b) or False))
        header.pack_start(save_btn)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda x: GLib.idle_add(lambda: self.close() or False))
        header.pack_end(close_btn)

        page = Adw.PreferencesPage()
        content.set_content(page)

        name_group = Adw.PreferencesGroup(title="Item Details")
        page.add(name_group)

        self.label_entry = Adw.EntryRow(title="Action Name")
        self.label_entry.set_text(self.item.get("label", ""))
        name_group.add(self.label_entry)

        type_group = Adw.PreferencesGroup(title="Action Type")
        page.add(type_group)

        current_cmd = self.item.get("cmd", "")
        is_predef = current_cmd in PREDEFINED_ACTIONS.values()
        is_custom = not is_predef

        self.selected_cmd = current_cmd

        predef_row = Adw.ActionRow(title="Predefined Action")
        predef_row.set_subtitle("Select from common actions")
        predef_row.set_title_lines(0)
        predef_row.set_subtitle_lines(0)
        predef_chk = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        predef_chk.set_active(is_predef)
        predef_chk.connect("toggled", lambda b, t="predefined": GLib.idle_add(lambda: self._on_type_toggled(b, t) or False))
        predef_row.add_prefix(predef_chk)
        type_group.add(predef_row)
        self.predef_chk = predef_chk

        cmd_row = Adw.ActionRow(title="Custom Command")
        cmd_row.set_subtitle("Execute a shell command")
        cmd_row.set_title_lines(0)
        cmd_row.set_subtitle_lines(0)
        cmd_chk = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        cmd_chk.set_group(predef_chk)
        cmd_chk.set_active(is_custom)
        cmd_chk.connect("toggled", lambda b, t="command": GLib.idle_add(lambda: self._on_type_toggled(b, t) or False))
        cmd_row.add_prefix(cmd_chk)
        type_group.add(cmd_row)
        self.cmd_chk = cmd_chk

        self.cmd_entry = Adw.EntryRow(title="Command")
        self.cmd_entry.set_text(current_cmd if is_custom else "")
        self.cmd_entry.set_visible(is_custom)
        type_group.add(self.cmd_entry)

        self.predef_group = Adw.PreferencesGroup(title="Predefined Actions")
        self.predef_group.set_visible(is_predef)
        page.add(self.predef_group)

        self.predef_rows = {}
        sorted_keys = sorted(PREDEFINED_ACTIONS.keys())

        for key in sorted_keys:
            val = PREDEFINED_ACTIONS[key]
            row = Adw.ActionRow(title=key)
            row.set_title_lines(0)
            row.set_activatable(True)

            icon = Gtk.Image.new_from_icon_name("object-select-symbolic")
            icon.set_visible(is_predef and current_cmd == val)
            row.add_suffix(icon)

            self.predef_rows[val] = icon

            row.connect("activated", lambda row, v=val, k=key: GLib.idle_add(lambda: self._on_predef_activated(v, k) or False))
            self.predef_group.add(row)

    def _on_type_toggled(self, chk, type_name):
        if not chk.get_active(): return

        if type_name == "command":
            self.cmd_entry.set_visible(True)
            self.predef_group.set_visible(False)
            self._update_predef_icons(None)

        elif type_name == "predefined":
            self.cmd_entry.set_visible(False)
            self.predef_group.set_visible(True)
            if self.selected_cmd in PREDEFINED_ACTIONS.values():
                 self._update_predef_icons(self.selected_cmd)
            else:
                 self._update_predef_icons(None)

    def _update_predef_icons(self, active_val):
        for val, icon in self.predef_rows.items():
            icon.set_visible(val == active_val)

    def _on_predef_activated(self, cmd_val, key_name):
        self.predef_chk.set_active(True)
        self.selected_cmd = cmd_val
        self._update_predef_icons(cmd_val)

        self.label_entry.set_text(key_name)

        self.cmd_entry.set_text(cmd_val)

    def _on_save_clicked(self, btn):
        if self.on_save:
            final_cmd = ""
            if self.predef_chk.get_active():
                final_cmd = self.selected_cmd
            else:
                final_cmd = self.cmd_entry.get_text()

            new_item = {
                "label": self.label_entry.get_text(),
                "cmd": final_cmd
            }
            self.on_save(new_item)
        GLib.idle_add(lambda: self.close() or False)

class WofiMenuEditor(Adw.Window):
    """Editor window for Wofi menus."""
    def __init__(self, parent, items, on_save):
        super().__init__(transient_for=parent, modal=True, title="Edit Menu")
        self.set_default_size(350, 500)
        self.items = items.copy()
        self.on_save = on_save

        content = Adw.ToolbarView()
        self.set_content(content)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        content.add_top_bar(header)

        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_add(b) or False))
        header.pack_end(add_btn)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_save_clicked(b) or False))
        header.pack_start(save_btn)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda x: GLib.idle_add(lambda: self.close() or False))
        header.pack_end(close_btn)

        self.list_box = Gtk.ListBox()
        self.list_box.add_css_class("boxed-list")
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)

        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self.list_box)

        clamp = Adw.Clamp()
        clamp.set_child(scroll)

        content.set_content(clamp)

        self._refresh_list()

    def _refresh_list(self):
        child = self.list_box.get_first_child()
        while child:
            self.list_box.remove(child)
            child = self.list_box.get_first_child()

        for idx, item in enumerate(self.items):
            row = Adw.ActionRow(title=item.get("label", "New Item"))
            row.set_subtitle(item.get("cmd", ""))
            row.set_title_lines(0)
            row.set_subtitle_lines(0)

            edit_btn = Gtk.Button(icon_name="document-edit-symbolic")
            edit_btn.add_css_class("flat")
            edit_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_edit(b, idx) or False))
            row.add_suffix(edit_btn)

            del_btn = Gtk.Button(icon_name="user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.add_css_class("error")
            del_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_delete(b, idx) or False))
            row.add_suffix(del_btn)

            self.list_box.append(row)

    def _on_add(self, btn):
        if len(self.items) >= 7:
            logger.warning("Max 7 items reached")
            return
        self._show_item_editor(None)

    def _on_edit(self, btn, idx):
        self._show_item_editor(idx)

    def _on_delete(self, btn, idx):
        self.items.pop(idx)
        self._refresh_list()

    def _show_item_editor(self, idx):
        is_new = idx is None
        item = self.items[idx] if not is_new else {"label": "", "cmd": ""}

        def on_save(new_item):
            if is_new:
                self.items.append(new_item)
            else:
                self.items[idx] = new_item
            self._refresh_list()

        win = WofiItemEditor(self, item, on_save)
        win.present()

    def _on_save_clicked(self, btn):
        if self.on_save:
            self.on_save(self.items)
        GLib.idle_add(lambda: self.close() or False)

class ActionSelectionDialog(Adw.Window):
    """Dialog to select an action type and configure it."""
    def __init__(self, parent, current_config, on_save):
        super().__init__(transient_for=parent, modal=True, title="Select Action")
        self.set_default_size(360, 600)
        self.config = current_config.copy()
        self.on_save = on_save

        content = Adw.ToolbarView()
        self.set_content(content)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        content.add_top_bar(header)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_save(b) or False))
        header.pack_start(save_btn)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda x: GLib.idle_add(lambda: self.close() or False))
        header.pack_end(close_btn)

        page = Adw.PreferencesPage()
        content.set_content(page)

        type_group = Adw.PreferencesGroup(title="Action Type")
        page.add(type_group)

        c_type = self.config.get("type", "command")
        c_val = self.config.get("value", "")
        is_wofi = (c_type == "wofi")
        is_predef = (c_type == "command" and c_val in PREDEFINED_ACTIONS.values())
        is_custom = (c_type == "command" and not is_predef)

        predef_row = Adw.ActionRow(title="Predefined Action")
        predef_row.set_subtitle("Select from common actions")
        predef_row.set_title_lines(0)
        predef_row.set_subtitle_lines(0)
        predef_chk = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        predef_chk.set_active(is_predef)
        predef_chk.connect("toggled", lambda b, t="predefined": GLib.idle_add(lambda: self._on_type_toggled(b, t) or False))
        predef_row.add_prefix(predef_chk)
        type_group.add(predef_row)
        self.predef_chk = predef_chk

        cmd_row = Adw.ActionRow(title="Custom Command")
        cmd_row.set_subtitle("Execute a shell command")
        cmd_row.set_title_lines(0)
        cmd_row.set_subtitle_lines(0)
        cmd_chk = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        cmd_chk.set_group(predef_chk)
        cmd_chk.set_active(is_custom)
        cmd_chk.connect("toggled", lambda b, t="command": GLib.idle_add(lambda: self._on_type_toggled(b, t) or False))
        cmd_row.add_prefix(cmd_chk)
        type_group.add(cmd_row)
        self.cmd_chk = cmd_chk

        self.cmd_entry = Adw.EntryRow(title="Command")
        self.cmd_entry.set_text(c_val if c_type == "command" else "")
        self.cmd_entry.set_visible(is_custom)
        type_group.add(self.cmd_entry)

        wofi_row = Adw.ActionRow(title="Wofi Menu")
        wofi_row.set_subtitle("Show a menu of actions")
        wofi_row.set_title_lines(0)
        wofi_row.set_subtitle_lines(0)
        wofi_chk = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        wofi_chk.set_group(predef_chk)
        wofi_chk.set_active(is_wofi)
        wofi_chk.connect("toggled", lambda b, t="wofi": GLib.idle_add(lambda: self._on_type_toggled(b, t) or False))
        wofi_row.add_prefix(wofi_chk)
        type_group.add(wofi_row)
        self.wofi_chk = wofi_chk

        self.edit_menu_btn = Gtk.Button(label="Edit Menu", valign=Gtk.Align.CENTER)
        self.edit_menu_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_edit_menu(b) or False))
        self.edit_menu_btn.set_visible(is_wofi)
        wofi_row.add_suffix(self.edit_menu_btn)

        predef_group = Adw.PreferencesGroup(title="Predefined Actions")
        page.add(predef_group)

        self.predef_rows = {}
        sorted_keys = sorted(PREDEFINED_ACTIONS.keys())

        for key in sorted_keys:
            val = PREDEFINED_ACTIONS[key]
            row = Adw.ActionRow(title=key)
            row.set_title_lines(0)
            row.set_activatable(True)

            icon = Gtk.Image.new_from_icon_name("object-select-symbolic")
            icon.set_visible(is_predef and c_val == val)
            row.add_suffix(icon)

            self.predef_rows[val] = icon

            row.connect("activated", lambda row, v=val: GLib.idle_add(lambda: self._on_predef_activated(v) or False))
            predef_group.add(row)

    def _on_type_toggled(self, chk, type_name):
        if not chk.get_active(): return

        if type_name == "command":
            self.config["type"] = "command"
            self.cmd_entry.set_visible(True)
            self.edit_menu_btn.set_visible(False)
            self._update_predef_icons(None)

        elif type_name == "wofi":
            self.config["type"] = "wofi"
            self.cmd_entry.set_visible(False)
            self.edit_menu_btn.set_visible(True)
            self._update_predef_icons(None)

        elif type_name == "predefined":
            self.config["type"] = "command"
            self.cmd_entry.set_visible(False)
            self.edit_menu_btn.set_visible(False)
            val = self.config.get("value")
            self._update_predef_icons(val)

    def _update_predef_icons(self, active_val):
        for val, icon in self.predef_rows.items():
            icon.set_visible(val == active_val)

    def _on_predef_activated(self, cmd_val):
        self.predef_chk.set_active(True)
        self.config["type"] = "command"
        self.config["value"] = cmd_val
        self.cmd_entry.set_text(cmd_val)
        self._update_predef_icons(cmd_val)

    def _on_edit_menu(self, btn):
        items = self.config.get("items", [])
        def save_items(new_items):
            self.config["items"] = new_items

        win = WofiMenuEditor(self, items, save_items)
        win.present()

    def _on_save(self, btn):
        if self.cmd_chk.get_active():
            self.config["value"] = self.cmd_entry.get_text()

        if self.on_save:
            self.on_save(self.config)
        GLib.idle_add(lambda: self.close() or False)
