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
"""Choose an Android app and open its native battery settings."""
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
from tweak_flx1s.system import android


class AndroidBackgroundDialog(Adw.Window):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True,
                         title="Android Background Activity")
        self.set_default_size(360, 600)
        self.closed = False
        self.connect("close-request", self._on_close)
        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda b: self.close())
        header.pack_end(close)
        toolbar.add_top_bar(header)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        toolbar.set_content(box)
        self.search = Gtk.SearchEntry(placeholder_text="Search Android apps")
        self.search.connect("search-changed", lambda e: self.apps.invalidate_filter())
        self.search.connect("activate", self._open_first)
        box.append(self.search)
        self.message = Gtk.Label(label="Loading apps…", wrap=True)
        box.append(self.message)
        self.apps = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.apps.add_css_class("boxed-list")
        self.apps.set_filter_func(lambda row: self.search.get_text().casefold() in row.search_text)
        self.apps.connect("row-activated", self._open)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(self.apps)
        box.append(scroll)
        self._work(android.list_apps, self._show_apps)

    def _on_close(self, window):
        self.closed = True
        return False

    def _work(self, work, done):
        def worker():
            try:
                result = work()
            except Exception as exc:
                result = exc
            def finish():
                if not self.closed:
                    done(result)
                return False
            GLib.idle_add(finish)
        threading.Thread(target=worker, daemon=True).start()

    def _show_apps(self, result):
        if isinstance(result, Exception):
            self.message.set_label("Could not load apps. Check that Android is running.")
            return
        self.message.set_label("Choose an app to configure its background battery use in Android.")
        for package, name in result:
            row = Adw.ActionRow(title=GLib.markup_escape_text(name), subtitle=package,
                                activatable=True)
            row.package = package
            row.search_text = (name + " " + package).casefold()
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            self.apps.append(row)

    def _open_first(self, entry):
        row = self.apps.get_first_child()
        while row:
            if entry.get_text().casefold() in row.search_text:
                self._open(self.apps, row)
                break
            row = row.get_next_sibling()

    def _open(self, listbox, row):
        self.apps.set_sensitive(False)
        def done(result):
            self.apps.set_sensitive(True)
            if isinstance(result, Exception):
                self.message.set_label("Could not open Android settings. Check that Android is running.")
            else:
                self.close()
        self._work(lambda: android.open_battery_settings(row.package), done)
