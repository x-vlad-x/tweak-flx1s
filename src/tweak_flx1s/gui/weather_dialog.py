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
from tweak_flx1s.system.weather import WeatherManager
from tweak_flx1s.utils import logger
import threading


class WeatherDialog(Adw.Window):
    """Dialog for searching and adding weather locations."""
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True, title="Add Location")
        self.set_default_size(350, 200)
        self.manager = WeatherManager()

        content = Adw.ToolbarView()
        self.set_content(content)


        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        content.add_top_bar(header)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self.close() or False))
        header.pack_end(close_btn)


        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(20)
        box.set_margin_end(20)


        self.search_entry = Gtk.SearchEntry(placeholder_text="Enter city name")
        self.search_entry.connect("search-changed", lambda entry: GLib.idle_add(lambda: self._on_search_changed(entry) or False))
        box.append(self.search_entry)


        self.search_btn = Gtk.Button(label="Search")
        self.search_btn.add_css_class("suggested-action")
        self.search_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_search(b) or False))
        box.append(self.search_btn)


        self.result_listbox = Gtk.ListBox()
        self.result_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.result_listbox.add_css_class("boxed-list")
        self.result_listbox.connect("row-selected", self._on_row_selected)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_min_content_height(150)
        scrolled.set_child(self.result_listbox)
        box.append(scrolled)

        self.add_btn = Gtk.Button(label="Add Location")
        self.add_btn.add_css_class("suggested-action")
        self.add_btn.set_sensitive(False)
        self.add_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_add(b) or False))
        box.append(self.add_btn)

        content.set_content(box)

        self.current_data = None
        self.results = []

    def _on_search_changed(self, entry):
        pass

    def _on_row_selected(self, listbox, row):
        if row:
            self.add_btn.set_sensitive(True)
            self.current_data = self.results[row.get_index()]
        else:
            self.add_btn.set_sensitive(False)
            self.current_data = None

    def _on_search(self, btn):
        query = self.search_entry.get_text()
        if not query: return

        self.search_entry.set_sensitive(False)
        self.search_btn.set_sensitive(False)
        self.add_btn.set_sensitive(False)
        self.current_data = None


        while True:
            row = self.result_listbox.get_row_at_index(0)
            if not row: break
            self.result_listbox.remove(row)

        t = threading.Thread(target=self._perform_search, args=(query,), daemon=True)
        t.start()

    def _perform_search(self, query):
        data = self.manager.search_location(query)
        GLib.idle_add(self._on_search_complete, data)

    def _on_search_complete(self, data):
        self.search_entry.set_sensitive(True)
        self.search_btn.set_sensitive(True)
        self.results = data

        if data:
            for item in data:
                row = Adw.ActionRow(title=item.get("display_name", "Unknown"))
                row.set_title_lines(0)
                self.result_listbox.append(row)
        else:
            row = Adw.ActionRow(title="No location found.")
            row.set_sensitive(False)
            self.result_listbox.append(row)

    def _on_add(self, btn):
        if self.current_data:
            success = self.manager.add_location(self.current_data)
            if success:
                GLib.idle_add(lambda: self.close() or False)
            else:
                dlg = Adw.MessageDialog(
                    transient_for=self,
                    heading="Error",
                    body="Failed to add location."
                )
                dlg.add_response("ok", "OK")
                dlg.present()
