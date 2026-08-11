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
from tweak_flx1s.gui.buttons_page import ButtonsPage
from tweak_flx1s.gui.gestures_page import GesturesPage


class ActionsPage(Adw.PreferencesPage):
    """
    Page for actions configuration.
    Includes: Buttons, Gestures.
    """
    def __init__(self, window, **kwargs):
        super().__init__(title="Actions", icon_name="input-gaming-symbolic", **kwargs)
        self.window = window

        group = Adw.PreferencesGroup(title="Input Configuration")
        self.add(group)

        btn_row = Adw.ActionRow(title="Hardware Buttons", subtitle="Configure short, double, and long press actions")
        btn_nav = Gtk.Button(icon_name="go-next-symbolic")
        btn_nav.add_css_class("flat")
        btn_nav.connect("clicked", lambda b: GLib.idle_add(lambda: self._open_buttons(b) or False))
        btn_row.add_suffix(btn_nav)
        group.add(btn_row)

        gst_row = Adw.ActionRow(title="Touch Gestures", subtitle="Configure edge swipes and shortcuts")
        gst_nav = Gtk.Button(icon_name="go-next-symbolic")
        gst_nav.add_css_class("flat")
        gst_nav.connect("clicked", lambda b: GLib.idle_add(lambda: self._open_gestures(b) or False))
        gst_row.add_suffix(gst_nav)
        group.add(gst_row)

    def _open_buttons(self, btn):
        win = Adw.Window(title="Buttons", transient_for=self.window, modal=True)
        win.set_default_size(400, 600)
        content = Adw.ToolbarView()
        win.set_content(content)
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        content.add_top_bar(header)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda b: GLib.idle_add(lambda: win.close() or False))
        header.pack_end(close_btn)

        page = ButtonsPage()
        content.set_content(page)
        win.present()

    def _open_gestures(self, btn):
        win = Adw.Window(title="Gestures", transient_for=self.window, modal=True)
        win.set_default_size(400, 600)
        content = Adw.ToolbarView()
        win.set_content(content)
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        content.add_top_bar(header)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda b: GLib.idle_add(lambda: win.close() or False))
        header.pack_end(close_btn)

        page = GesturesPage()
        content.set_content(page)
        win.present()
