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
'Power tools: Android, idle inhibitors, orphaned processes, batman cores and services.'
from gi.repository import Adw, Gio, GLib, Gtk

from tweak_flx1s.core.offline_pause import system
from .common import cpu_list, in_thread

SESSION_TEXT = {"RUNNING": 'running', "STOPPED": 'stopped', "FROZEN": 'frozen',
                "STARTING": 'starting', "STOPPING": 'stopping'}


def esc(text):
    return GLib.markup_escape_text(str(text))


def app_name(app_id):
    """Human name for an inhibitor's app id (desktop file id, android.<pkg>...)."""
    if not app_id:
        return 'Unknown Application'
    try:
        info = Gio.DesktopAppInfo.new(app_id + ".desktop")
        if info:
            return info.get_name()
    except TypeError:
        pass
    if app_id.startswith("android."):
        return f"Android: {app_id[8:]}"
    return app_id


class Group:
    """A PreferencesGroup whose rows are replaced on every refresh."""

    def __init__(self, page, title, description=None):
        self.group = Adw.PreferencesGroup(title=title, description=description)
        page.add(self.group)
        self.rows = []

    def clear(self):
        for row in self.rows:
            self.group.remove(row)
        self.rows = []

    def add(self, row):
        self.group.add(row)
        self.rows.append(row)
        return row


def busy_button(label, css=None):
    button = Gtk.Button(label=label, valign=Gtk.Align.CENTER, css_classes=css or [])
    return button


class SystemPage(Adw.PreferencesPage):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.loading = False
        self.updating = False

        # --- Android ---
        self.android = Group(self, "Android (Andromeda)",
                             'Stopping also stops the Android container service. Start it here again when you need Android apps.')
        # --- inhibitors ---
        self.inhibit = Group(self, 'Sleep and Screen Inhibitors',
                             'Apps can prevent the screen from turning off. Stale inhibitors remain after an app window has closed.')
        self.release = Adw.ButtonRow(title='Release Window and Player Inhibitors',
                                     start_icon_name="edit-clear-symbolic")
        self.release.connect("activated", self.on_release)
        # --- orphans ---
        self.orphans = Group(self, 'Orphaned Processes',
                             'Processes whose parent has exited and that can no longer perform their work.')
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                             css_classes=["flat"], tooltip_text='Refresh')
        refresh.connect("clicked", lambda b: self.refresh())
        self.orphans.group.set_header_suffix(refresh)
        # --- batman ---
        self.cores = Group(self, 'Cores While the Screen Is Off',
                           'batman offlines cores from cpu0 to the selected boundary. FuriOS power profiles reset the start of this range, so only the last cores can be kept online.')
        # --- notification wake-ups ---
        self.wake = Group(self, 'Screen and Notifications',
                          'Choose which notifications wake the screen to reduce power use while idle.')
        self.wake_settings = system.wake_settings()
        if self.wake_settings:
            self.wake_handler = self.wake_settings.connect("changed", self.on_wake_changed)
        self.show_wake()
        # --- services ---
        self.services = Group(self, 'Background Services')

    # ---------------------------------------------------------- loading

    def refresh(self):
        if self.loading:
            return
        self.loading = True

        def safe(fn, *args):
            try:
                return fn(*args)
            except Exception as exc:     # shown inside that section only
                return exc

        def work():
            data = {"android": safe(system.andromeda_state),
                    "inhibitors": safe(system.gnome_inhibitors, self.bus),
                    "logind": safe(system.logind_inhibitors),
                    "orphans": safe(system.find_orphans),
                    "cpus": safe(system.cpu_topology),
                    "batman": safe(system.batman_settings),
                    "services": [safe(system.service_info, u) for u, _, _ in system.SERVICES]}
            session = None if isinstance(data["android"], Exception) else data["android"]["session"]
            items = [] if isinstance(data["inhibitors"], Exception) else data["inhibitors"]
            data["stale"] = {i["path"]: safe(system.stale_inhibitor, i, session) for i in items}
            data["inhibitor_pids"] = {i["path"]: safe(system.closable_processes, i["app_id"])
                                      for i in items}
            return data
        in_thread(work, self.show)

    def error_row(self, group, error):
        group.clear()
        group.add(Adw.ActionRow(title='Could Not Read State', subtitle=esc(error), subtitle_lines=3))

    def show(self, data):
        self.loading = False
        if isinstance(data, Exception):
            self.window.toast(f"Could not read system state: {data}")
            return
        self.updating = True
        try:
            for group, value, show in (
                    (self.android, data["android"], lambda: self.show_android(data["android"])),
                    (self.inhibit, data["inhibitors"], lambda: self.show_inhibitors(data)),
                    (self.orphans, data["orphans"], lambda: self.show_orphans(data["orphans"])),
                    (self.cores, data["cpus"], lambda: self.show_cores(data["cpus"], data["batman"])),
                    (self.services, None, lambda: self.show_services(data["services"]))):
                if isinstance(value, Exception):
                    self.error_row(group, value)
                    continue
                try:
                    show()
                except Exception as exc:
                    self.error_row(group, exc)
        finally:
            self.updating = False

    def action(self, work, done_text=None, refresh=True):
        """Run a system action in a thread, toast its error or result, then refresh."""
        def done(result):
            if isinstance(result, Exception):
                self.window.toast(str(result))
            elif result:
                self.window.toast(result if isinstance(result, str) else "; ".join(result))
            elif done_text:
                self.window.toast(done_text)
            if refresh:
                self.refresh()
        in_thread(work, done)

    # ---------------------------------------------------------- Android

    def show_android(self, state):
        self.android.clear()
        if not state["installed"]:
            self.android.add(Adw.ActionRow(title='Andromeda Is Not Installed'))
            return
        session = SESSION_TEXT.get(state["session"], state["session"] or 'unknown')
        service = 'running' if state["service"] == "active" else 'stopped'
        subtitle = f"Session: {session} · container service: {service}"
        if not state["restartable"]:
            subtitle += ('\nRestart support is missing. See /usr/share/doc/tweak-flx1s/ before stopping Android.')
        row = Adw.ActionRow(title="Android", subtitle=esc(subtitle), subtitle_lines=4)
        row.add_prefix(Gtk.Image(icon_name="phone-symbolic"))
        running = state["service"] == "active"
        button = busy_button('Stop' if running else 'Start',
                             ["destructive-action"] if running else ["suggested-action"])
        button.connect("clicked", self.on_android, running)
        row.add_suffix(button)
        self.android.add(row)

    def on_android(self, button, running):
        button.set_sensitive(False)
        button.set_child(Adw.Spinner())
        if running:
            self.action(system.andromeda_stop, 'Android stopped')
        else:
            self.action(system.andromeda_start, 'Android is starting — this can take about a minute')

    # ---------------------------------------------------------- inhibitors

    def show_inhibitors(self, data):
        self.inhibit.clear()
        if self.release.get_parent():
            self.inhibit.group.remove(self.release)
        items = data["inhibitors"]
        logind = [] if isinstance(data["logind"], Exception) else data["logind"]
        for item in items:
            stale = data["stale"].get(item["path"]) is True
            pids = data["inhibitor_pids"].get(item["path"], [])
            if isinstance(pids, Exception):
                pids = []
            subtitle = f"Inhibits: {system.flags_text(item['flags'])}"
            if item.get("reason"):
                subtitle += f" · {item['reason']}"
            if stale:
                subtitle += ' · stale'
            row = Adw.ActionRow(title=esc(app_name(item["app_id"])), subtitle=esc(subtitle),
                                subtitle_lines=3)
            row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic" if stale
                                     else "changes-prevent-symbolic"))
            if pids:
                button = busy_button('Close')
                button.set_tooltip_text('Close the app to release its inhibitors')
                button.connect("clicked", self.on_close_app, pids)
                row.add_suffix(button)
            self.inhibit.add(row)
        for item in logind:
            row = Adw.ActionRow(title=esc(item["who"]),
                                subtitle=esc(f"Inhibits: {item['what']} · {item['why']} (logind)"),
                                subtitle_lines=3)
            row.add_prefix(Gtk.Image(icon_name="changes-prevent-symbolic"))
            self.inhibit.add(row)
        if not items and not logind:
            self.inhibit.add(Adw.ActionRow(title='No Inhibitors',
                                           subtitle='The screen can turn off on its normal timer'))
        self.release.set_sensitive(bool(items))
        self.inhibit.group.add(self.release)

    def on_close_app(self, button, pids):
        button.set_sensitive(False)
        self.action(lambda: None if system.terminate(pids) else 'Could not close app')

    def on_release(self, row):
        row.set_sensitive(False)

        def work():
            error = system.restart_screensaver_proxy(self.bus)
            if error:
                return error
            left = system.gnome_inhibitors(self.bus)
            return (f"Remaining inhibitors: {len(left)}. Close the apps holding them "
                    "using their Close buttons.") if left else None
        self.action(work, 'Inhibitors released')

    # ---------------------------------------------------------- orphans

    def show_orphans(self, found):
        self.orphans.clear()
        if not found:
            self.orphans.add(Adw.ActionRow(title='None Found'))
            return
        for item in found:
            row = Adw.ActionRow(title=esc(f"{item['comm']} (PID {item['pid']})"),
                                subtitle=esc(f"{item['reason']}\n{item['cmdline']}"),
                                subtitle_lines=4)
            row.add_prefix(Gtk.Image(icon_name="process-stop-symbolic"))
            button = busy_button('Terminate', ["destructive-action"])
            button.connect("clicked", self.on_kill, item)
            row.add_suffix(button)
            self.orphans.add(row)

    def on_kill(self, button, item):
        button.set_sensitive(False)

        def work():
            info = system.procs.read_proc(item["pid"])
            if not info or info["start"] != item["start"]:
                return 'Process has already exited'
            return None if system.terminate([item["pid"]]) else 'Could not terminate process'
        self.action(work, f"{item['comm']} terminated")

    # ---------------------------------------------------------- cores

    def show_cores(self, cpus, settings):
        self.cores.clear()
        if isinstance(settings, Exception):
            raise settings
        if not settings.get("available"):
            self.cores.add(Adw.ActionRow(title='batman Not Found'))
            return
        n = len(cpus)
        self.cpus = cpus
        online = [c["cpu"] for c in cpus if c["online"]]
        now = Adw.ActionRow(title='Currently Online', subtitle=cpu_list(online))
        self.cores.add(now)

        self.offline_row = self.cores.add(Adw.SwitchRow(
            title='Offline Cores When the Screen Is Off',
            subtitle='If disabled, all cores stay online and use their normal idle states',
            active=settings["offline"]))
        self.choice_values = [last for last, _ in system.choices(n)]
        labels = []
        for _last, kept in system.choices(n):
            names = {}
            for cpu in kept:
                core = cpus[cpu]["core"]
                names[core] = names.get(core, 0) + 1
            kinds = " + ".join(f"{k}× {name}" if k > 1 else name for name, k in names.items())
            labels.append(f"{cpu_list(kept)} · {kinds}")
        self.choice_row = self.cores.add(Adw.ComboRow(
            title='Keep Online', model=Gtk.StringList.new(labels)))
        current = settings["last"]
        if current in self.choice_values:
            self.choice_row.set_selected(self.choice_values.index(current))
        self.choice_row.set_sensitive(settings["offline"])
        self.offline_row.connect("notify::active",
                                 lambda r, p: (self.choice_row.set_sensitive(r.get_active()),
                                               self.cores_changed()))
        self.choice_row.connect("notify::selected", lambda r, p: self.cores_changed())
        self.cores_saved = (settings["offline"], current)

        self.apply_row = self.cores.add(Adw.ButtonRow(title='Apply',
                                                      start_icon_name="emblem-ok-symbolic"))
        self.apply_row.set_sensitive(False)
        self.apply_row.connect("activated", self.on_apply_cores)
        if not settings["writable"]:
            self.cores.add(Adw.ActionRow(title='No write access to /var/lib/batman'))
            self.apply_row.set_visible(False)

    def wanted_cores(self):
        selected = self.choice_row.get_selected()
        last = self.choice_values[selected] if 0 <= selected < len(self.choice_values) else None
        return self.offline_row.get_active(), last

    def cores_changed(self):
        if not self.updating:
            self.apply_row.set_sensitive(self.wanted_cores() != self.cores_saved)

    def on_apply_cores(self, row):
        row.set_sensitive(False)
        offline, last = self.wanted_cores()
        self.action(lambda: system.apply_batman(offline, last),
                    'Saved. The new core selection will apply when the screen turns off.')

    # ---------------------------------------------------------- notification wake-ups

    def show_wake(self):
        self.wake.clear()
        if not self.wake_settings:
            self.wake.add(Adw.ActionRow(title='Phosh notification settings not found'))
            return
        current = system.wake_preset(self.wake_settings)
        self.wake_ids = [p[0] for p in system.WAKE_PRESETS]
        labels = [p[1] for p in system.WAKE_PRESETS]
        if current == "custom":
            self.wake_ids.append("custom")
            labels.append('Custom')
        self.wake_row = self.wake.add(Adw.ComboRow(
            title='Wake Screen', model=Gtk.StringList.new(labels)))
        self.wake_row.set_selected(self.wake_ids.index(current))
        subtitle = next((p[2] for p in system.WAKE_PRESETS if p[0] == current),
                        'Configured manually; select a preset to replace it')
        self.wake_row.set_subtitle(subtitle)
        self.wake_row.connect("notify::selected", self.on_wake_selected)

    def on_wake_selected(self, row, pspec):
        if self.updating:
            return
        preset = self.wake_ids[row.get_selected()]
        if preset != "custom":
            system.set_wake_preset(self.wake_settings, preset)

    def shutdown(self):
        if self.wake_settings:
            self.wake_settings.disconnect(self.wake_handler)

    def on_wake_changed(self, settings, key):
        # Several keys change at once; rebuild after the last one.
        if not getattr(self, "wake_pending", False):
            self.wake_pending = True

            def rebuild():
                self.wake_pending = False
                self.updating = True
                try:
                    self.show_wake()
                finally:
                    self.updating = False
                return False
            GLib.idle_add(rebuild)

    # ---------------------------------------------------------- services

    def show_services(self, infos):
        self.services.clear()
        for (unit, title, subtitle), info in zip(system.SERVICES, infos):
            if isinstance(info, Exception):
                continue
            if info["enabled"] in ("not-found", "unknown") and info["active"] != "active":
                continue
            row = self.services.add(Adw.SwitchRow(title=title, subtitle=subtitle, active=info["on"]))
            row.connect("notify::active", self.on_service, unit)
        self.services.group.set_visible(bool(self.services.rows))

    def on_service(self, row, pspec, unit):
        if self.updating:
            return
        on = row.get_active()
        self.action(lambda: system.set_service(unit, on))
