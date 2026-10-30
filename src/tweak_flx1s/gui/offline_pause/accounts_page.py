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
'GNOME Online Accounts features and per-source synchronization.'
from gi.repository import Adw, GLib, Gtk

from tweak_flx1s.core.offline_pause import eds
from .common import in_thread

INTERVALS = [(15, 'Every 15 minutes'), (30, 'Every 30 minutes'), (60, 'Every hour'),
             (120, 'Every 2 hours'), (240, 'Every 4 hours'), (480, 'Every 8 hours'),
             (720, 'Every 12 hours'), (1440, 'Daily'), (None, 'Manual only')]
KIND_ICONS = {"Calendar": "x-office-calendar-symbolic", "Task List": "checkbox-checked-symbolic",
              "Memo List": "text-x-generic-symbolic", "Address Book": "x-office-address-book-symbolic"}
PROVIDER_ICONS = {"google": "goa-account-google", "owncloud": "goa-account-owncloud",
                  "imap_smtp": "mail-unread-symbolic", "ms_graph": "goa-account-msn",
                  "exchange": "goa-account-exchange"}


def esc(text):
    return GLib.markup_escape_text(str(text))


def interval_index(src):
    if not src.refresh_enabled:
        return len(INTERVALS) - 1
    best = min(range(len(INTERVALS) - 1), key=lambda i: abs(INTERVALS[i][0] - src.refresh_minutes))
    return best


class AccountsPage(Adw.PreferencesPage):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.groups = []
        self.loading = False
        self.updating = False
        self.service = None
        intro = Adw.PreferencesGroup(
            description='Configure calendar, task, memo and contact synchronization through Evolution Data Server and GNOME Online Accounts. Less frequent synchronization can save power. Geary uses IMAP IDLE; Proton Bridge manages its own synchronization.')
        self.add(intro)

    def refresh(self):
        if self.loading:
            return
        self.loading = True
        in_thread(eds.load, self.show)

    def show(self, result):
        self.loading = False
        for group in self.groups:
            self.remove(group)
        self.groups = []
        if isinstance(result, Exception):
            group = Adw.PreferencesGroup()
            group.add(Adw.ActionRow(title='Could Not Read Accounts',
                                    subtitle=esc(result)))
            self.add(group)
            self.groups.append(group)
            return
        accounts, other, self.service = result
        self.updating = True
        try:
            for acc in accounts:
                self.add_account(acc)
            if other:
                group = Adw.PreferencesGroup(title='Other Sources')
                for src in other:
                    group.add(self.source_row(src))
                self.add(group)
                self.groups.append(group)
            if not accounts and not other:
                group = Adw.PreferencesGroup()
                group.add(Adw.ActionRow(title='No Online Accounts',
                                        subtitle='Add accounts in Settings → Online Accounts'))
                self.add(group)
                self.groups.append(group)
        finally:
            self.updating = False

    def add_account(self, acc):
        group = Adw.PreferencesGroup(title=esc(acc.provider or acc.provider_type),
                                     description=esc(acc.identity))
        for prop, label, on in acc.features:
            row = Adw.SwitchRow(title=label, active=on)
            row.connect("notify::active", self.on_feature, acc, prop)
            group.add(row)
        if acc.sources:
            for src in acc.sources:
                group.add(self.source_row(src))
        self.add(group)
        self.groups.append(group)

    def source_row(self, src):
        row = Adw.ExpanderRow(title=esc(src.name), show_enable_switch=True,
                              enable_expansion=src.enabled)
        row.add_prefix(Gtk.Image(icon_name=KIND_ICONS.get(src.kind, "folder-remote-symbolic")))
        self.set_source_subtitle(row, src)
        combo = Adw.ComboRow(title='Refresh', model=Gtk.StringList.new([t for _, t in INTERVALS]))
        combo.set_selected(interval_index(src))
        combo.connect("notify::selected", self.on_interval, src, row)
        row.add_row(combo)
        if not src.writable:
            row.set_sensitive(False)
            row.set_subtitle(row.get_subtitle() + ' · read-only')
        row.connect("notify::enable-expansion", self.on_enabled, src)
        return row

    def set_source_subtitle(self, row, src):
        state = eds.refresh_label(src) if src.enabled else 'disabled'
        row.set_subtitle(esc(f"{eds.kind_label(src.kind)} · {state}"))

    # ---------------------------------------------------------- writes

    def write(self, src, row, **change):
        data = src.updated(**change)

        def done(result):
            if isinstance(result, Exception):
                self.window.toast(f"Could not save '{src.name}': {result}")
                self.refresh()
                return
            # Mirror the change locally so the subtitle is right without a reload.
            src.data = data
            src.__init__(src.path, src.uid, data, src.writable)
            self.set_source_subtitle(row, src)
        in_thread(lambda: eds.write_source(src, self.service, data), done)

    def on_enabled(self, row, pspec, src):
        if not self.updating:
            self.write(src, row, enabled=row.get_enable_expansion())

    def on_interval(self, combo, pspec, src, row):
        if self.updating:
            return
        minutes = INTERVALS[combo.get_selected()][0]
        if minutes is None:
            self.write(src, row, refresh_enabled=False)
        else:
            self.write(src, row, refresh_enabled=True, refresh_minutes=minutes)

    def on_feature(self, row, pspec, acc, prop):
        if self.updating:
            return
        on = row.get_active()

        def done(result):
            if isinstance(result, Exception):
                self.window.toast(f"Could not change account: {result}")
            # Turning a feature on or off adds or removes EDS sources.
            GLib.timeout_add_seconds(2, lambda: (self.refresh(), False)[1])
        in_thread(lambda: eds.set_feature(acc, prop, on), done)
