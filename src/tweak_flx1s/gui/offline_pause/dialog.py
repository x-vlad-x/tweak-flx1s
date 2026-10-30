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
"""Offline pause and power tools, embedded in Tweak-FLX1s."""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw
from .pause_page import PausePage
from .system_page import SystemPage
from .accounts_page import AccountsPage


class OfflinePauseDialog(Adw.Window):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True, title="Offline Pause and Power Tools")
        self.set_default_size(360, 600)
        toolbar = Adw.ToolbarView()
        self.toasts = Adw.ToastOverlay(child=toolbar)
        self.set_content(self.toasts)
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda b: self.close())
        header.pack_end(close)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        refresh.connect("clicked", lambda b: self.stack.get_visible_child().refresh())
        header.pack_start(refresh)
        toolbar.add_top_bar(header)
        self.stack = Adw.ViewStack()
        toolbar.set_content(self.stack)
        self.pause = PausePage(self)
        # Power and account state is loaded only when those tabs are opened.
        self.stack.add_titled_with_icon(self.pause, "pause", "Pause", "network-offline-symbolic")
        self.system = SystemPage(self)
        self.stack.add_titled_with_icon(self.system, "system", "System", "preferences-system-symbolic")
        self.stack.add_titled_with_icon(AccountsPage(self), "accounts", "Accounts", "system-users-symbolic")
        self.stack.connect("notify::visible-child-name", self._on_page)
        toolbar.add_bottom_bar(Adw.ViewSwitcherBar(stack=self.stack, reveal=True))
        self.connect("close-request", self._on_close)

    def toast(self, message):
        self.toasts.add_toast(Adw.Toast(title=str(message)[:300], timeout=5, use_markup=False))

    def _on_page(self, stack, param):
        stack.get_visible_child().refresh()

    def _on_close(self, window):
        self.pause.shutdown()
        self.system.shutdown()
        return False
