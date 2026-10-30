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
'Offline pause status, behaviour, apps, connectivity probes and history.'
import json
import subprocess
import time

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from tweak_flx1s.core.offline_pause import apps
from .common import ago, clock, duration, in_thread, short_error

SERVICE = "tweak-flx1s-offline-pause.service"
BUS_NAME = "io.FuriOS.TweakFLX1s.OfflinePause"
OBJECT_PATH = "/io/FuriOS/TweakFLX1s/OfflinePause"
INTERFACE = BUS_NAME
EVENTS_SHOWN = 30

TARGET_ICONS = {"bridge": "mail-send-receive-symbolic", "geary": "mail-unread-symbolic"}
EVENT_ICONS = {
    "pause": "media-playback-pause-symbolic",
    "resume": "media-playback-start-symbolic",
    "offline": "network-offline-symbolic",
    "online": "network-transmit-receive-symbolic",
}


def service_active():
    return subprocess.run(["systemctl", "--user", "is-active", "--quiet",
                           SERVICE]).returncode == 0


class PausePage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.status = None
        self.updating = False
        self.event_rows = []

        self.banner = Adw.Banner(title='Offline pause service is not running',
                                 button_label='Start')
        self.banner.connect("button-clicked", self.on_start_service)

        page = Adw.PreferencesPage()

        # --- status ---
        status_group = Adw.PreferencesGroup()
        page.add(status_group)
        self.status_icon = Gtk.Image(pixel_size=64, margin_top=12)
        self.status_title = Gtk.Label(css_classes=["title-2"], wrap=True,
                                      justify=Gtk.Justification.CENTER)
        self.status_subtitle = Gtk.Label(css_classes=["dim-label"], wrap=True,
                                         wrap_mode=Pango.WrapMode.WORD_CHAR,
                                         max_width_chars=32,
                                         justify=Gtk.Justification.CENTER,
                                         margin_bottom=6)
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for widget in (self.status_icon, self.status_title, self.status_subtitle):
            hero.append(widget)
        status_group.add(hero)

        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=12)
        self.check_button = Gtk.Button(label='Check Now', css_classes=["pill"])
        self.check_button.connect("clicked", lambda b: self.call("CheckNow"))
        self.resume_button = Gtk.Button(label='Resume', css_classes=["pill", "suggested-action"])
        self.resume_button.connect("clicked", lambda b: self.call("ResumeNow"))
        buttons.append(self.check_button)
        buttons.append(self.resume_button)
        status_group.add(buttons)

        # Rows go to a separate group: Adw puts a group's rows before other widgets.
        state_group = Adw.PreferencesGroup()
        page.add(state_group)
        self.state_row = Adw.ActionRow(title='Current State', subtitle_lines=3)
        state_group.add(self.state_row)

        # --- behaviour ---
        behaviour = Adw.PreferencesGroup(title='Behaviour')
        page.add(behaviour)
        self.enabled_row = Adw.SwitchRow(
            title='Pause Apps While Offline',
            subtitle='Detect internet outages even when Wi-Fi stays connected')
        self.enabled_row.connect("notify::active", self.on_switch, "enabled")
        behaviour.add(self.enabled_row)
        self.screen_row = Adw.SwitchRow(
            title='Only While the Screen Is Off',
            subtitle='If disabled, paused app windows will stop responding until connectivity returns')
        self.screen_row.connect("notify::active", self.on_switch, "only_screen_off")
        behaviour.add(self.screen_row)

        # --- apps ---
        self.apps_group = Adw.PreferencesGroup(
            title='Applications',
            description='Selected apps are paused while offline. System services, calls and SMS are excluded.')
        page.add(self.apps_group)
        self.target_rows = {}
        self.add_row = Adw.ButtonRow(title='Add Application…', start_icon_name="list-add-symbolic")
        self.add_row.connect("activated", self.on_add_app)
        self.apps_group.add(self.add_row)

        # --- probing ---
        probing = Adw.PreferencesGroup(
            title='Connectivity Checks',
            description='Pause after two failed checks in a row; resume after the first successful check.')
        page.add(probing)
        self.spin_rows = {}
        for key, title, low, high in (
                ("online_interval", 'Interval While Online', 1, 30),
                ("offline_interval", 'Interval While Offline', 1, 30),
                ("hold_minutes", 'Resume Duration', 5, 240)):
            row = Adw.SpinRow.new_with_range(low, high, 1)
            row.set_title(title)
            row.set_subtitle('minutes')
            row.connect("notify::value", self.on_spin, key)
            probing.add(row)
            self.spin_rows[key] = row

        # --- history ---
        self.history = Adw.PreferencesGroup(title='History')
        clear = Gtk.Button(icon_name="edit-clear-all-symbolic", valign=Gtk.Align.CENTER,
                           css_classes=["flat"], tooltip_text='Clear History')
        clear.connect("clicked", lambda b: self.call("ClearEvents"))
        self.history.set_header_suffix(clear)
        self.history_empty = Adw.ActionRow(title='No Events Yet',
                                           subtitle='Pauses and battery levels will appear here')
        self.history.add(self.history_empty)
        page.add(self.history)

        self.append(self.banner)
        self.append(page)
        page.set_vexpand(True)

        self.signal_id = self.bus.signal_subscribe(
            BUS_NAME, INTERFACE, "StatusChanged", OBJECT_PATH, None,
            Gio.DBusSignalFlags.NONE, self.on_status_signal)
        self.watch_id = Gio.bus_watch_name_on_connection(
            self.bus, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            self.on_daemon_appeared, self.on_daemon_vanished)
        self.tick_id = GLib.timeout_add_seconds(30, self.on_tick)
        self.show_disconnected()

    # ---------- D-Bus ----------

    def call(self, method, arg=None, callback=None):
        params = GLib.Variant("(s)", (arg,)) if arg is not None else None

        def done(bus, result):
            try:
                reply = bus.call_finish(result)
            except GLib.Error as exc:
                self.window.toast(exc.message)
                return
            if callback:
                callback(reply.unpack()[0] if reply.n_children() else None)
        self.bus.call(BUS_NAME, OBJECT_PATH, INTERFACE, method, params, None,
                      Gio.DBusCallFlags.NONE, 15000, None, done)

    def refresh(self):
        self.call("GetStatus", callback=lambda s: self.show_status(json.loads(s)))
        self.call("GetEvents", callback=lambda s: self.show_events(json.loads(s)))

    def on_status_signal(self, conn, sender, path, iface, signal, params):
        self.show_status(json.loads(params.unpack()[0]))
        self.call("GetEvents", callback=lambda s: self.show_events(json.loads(s)))

    def on_daemon_appeared(self, conn, name, owner):
        self.banner.set_revealed(False)
        self.refresh()

    def on_daemon_vanished(self, conn, name):
        self.status = None
        self.show_disconnected()

    def on_start_service(self, banner):
        def start():
            legacy = subprocess.run(["systemctl", "--user", "is-active", "--quiet",
                                     "offline-pause.service"], capture_output=True, timeout=10)
            if legacy.returncode == 0:
                raise RuntimeError("Stop the standalone Offline Pause service first. See /usr/share/doc/tweak-flx1s/.")
            subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE],
                           check=True, capture_output=True, timeout=30)
        def done(result):
            if isinstance(result, Exception):
                self.window.toast(str(result))
            banner.set_revealed(not service_active())
        in_thread(start, done)

    # ---------- view ----------

    def show_disconnected(self):
        self.banner.set_revealed(True)
        self.status_icon.set_from_icon_name("dialog-question-symbolic")
        self.status_title.set_label('Service unavailable')
        self.status_subtitle.set_label('Start the service using the button above')
        self.state_row.set_subtitle("")
        for widget in (self.check_button, self.resume_button, self.enabled_row, self.add_row,
                       self.screen_row, *self.spin_rows.values(), *self.target_rows.values()):
            widget.set_sensitive(False)

    def show_status(self, status):
        self.status = status
        conf = status["config"]
        self.updating = True
        try:
            for widget in (self.check_button, self.enabled_row, self.screen_row, self.add_row,
                           *self.spin_rows.values()):
                widget.set_sensitive(True)
            self.enabled_row.set_active(conf["enabled"])
            self.screen_row.set_active(conf["only_screen_off"])
            self.spin_rows["online_interval"].set_value(conf["online_interval"] // 60)
            self.spin_rows["offline_interval"].set_value(conf["offline_interval"] // 60)
            self.spin_rows["hold_minutes"].set_value(conf["hold_minutes"])
            self.resume_button.set_tooltip_text(f"Resume for {conf['hold_minutes']} min")
            self.show_targets(status)
        finally:
            self.updating = False
        self.show_summary()

    def show_targets(self, status):
        conf = status["config"]
        custom = {c["key"] for c in conf.get("custom_targets", [])}
        for key in [k for k in self.target_rows if k not in status["names"]]:
            self.apps_group.remove(self.target_rows.pop(key))
        for key, name in status["names"].items():
            row = self.target_rows.get(key)
            if not row:
                row = Adw.SwitchRow(title=GLib.markup_escape_text(name))
                icon = TARGET_ICONS.get(key) or apps.describe(key)[1] or "application-x-executable-symbolic"
                image = Gtk.Image(pixel_size=24)
                if icon.startswith("/"):
                    image.set_from_file(icon)
                else:
                    image.set_from_icon_name(icon)
                row.add_prefix(image)
                if key in custom:
                    remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                        css_classes=["flat"], tooltip_text='Remove from List')
                    remove.connect("clicked", self.on_remove_app, key)
                    row.add_suffix(remove)
                row.connect("notify::active", self.on_target, key)
                # Keep Add Application last.
                self.apps_group.remove(self.add_row)
                self.apps_group.add(row)
                self.apps_group.add(self.add_row)
                self.target_rows[key] = row
            row.set_sensitive(True)
            row.set_active(conf["targets"].get(key, False))
            if key in status["paused"]:
                row.set_subtitle('paused')
            elif status["running"].get(key):
                row.set_subtitle('running')
            else:
                row.set_subtitle('not running')

    def show_summary(self):
        status = self.status
        if not status:
            return
        conf = status["config"]
        if status["checking"]:
            icon, title = "content-loading-symbolic", 'Checking connectivity…'
        elif status["online"] is None and status["failures"]:
            icon, title = "network-wireless-no-route-symbolic", 'Connection lost? Checking again'
        elif status["online"] is None:
            icon, title = "network-wireless-symbolic", 'Connectivity not checked yet'
        elif status["online"]:
            icon, title = "network-transmit-receive-symbolic", 'Online'
        else:
            icon, title = "network-offline-symbolic", 'Offline'
        self.status_icon.set_from_icon_name(icon)
        self.status_title.set_label(title)
        subtitle = 'Checked ' + ago(status["last_check"])
        if status["online"] is not True and status["last_error"]:
            subtitle += "\n" + short_error(status["last_error"])
        if not conf["enabled"]:
            subtitle += '\nAutomatic checks disabled'
        self.status_subtitle.set_label(subtitle)

        names = status["names"]
        if status["paused"]:
            since = status["pause_started"]
            text = 'Paused: ' + ", ".join(names.get(k, k) for k in status["paused"])
            if since:
                text += f" — since {clock(since)} ({duration(time.time() - since)})"
            text += f"\nResume will unpause apps for {conf['hold_minutes']} min"
        elif status["hold_until"]:
            text = f"Pause postponed until {clock(status['hold_until'])}"
        elif not conf["enabled"]:
            text = 'Service disabled — apps are left running'
        elif status["online"] is False and conf["only_screen_off"] and status["screen_on"]:
            text = 'Running; will pause when the screen turns off'
        else:
            text = 'Apps are running normally'
        self.state_row.set_subtitle(GLib.markup_escape_text(text))
        self.resume_button.set_sensitive(bool(status["paused"]))

    def show_events(self, events):
        for row in self.event_rows:
            self.history.remove(row)
        self.event_rows = []
        self.history_empty.set_visible(not events)
        for event in reversed(events[-EVENTS_SHOWN:]):
            row = Adw.ActionRow(title=GLib.markup_escape_text(event["text"]), title_lines=3)
            details = [clock(event["time"], with_day=True)]
            if event["kind"] == "resume" and event.get("duration"):
                details.append('paused for ' + duration(event["duration"]))
                start, end = event.get("battery_start"), event.get("battery")
                if start is not None and end is not None:
                    details.append(f"battery {start}% → {end}%")
            elif event.get("battery") is not None:
                details.append(f"battery {event['battery']}%")
            row.set_subtitle(" · ".join(details))
            row.add_prefix(Gtk.Image(icon_name=EVENT_ICONS.get(event["kind"], "dialog-information-symbolic")))
            self.history.add(row)
            self.event_rows.append(row)

    # ---------- apps ----------

    def custom_targets(self):
        return list(self.status["config"].get("custom_targets", [])) if self.status else []

    def on_add_app(self, _row):
        if not self.status:
            return
        existing = set(self.status["names"])
        dialog = AddAppDialog(existing, self.add_app)
        dialog.present(self.window)

    def add_app(self, key, name):
        targets = self.custom_targets() + [{"key": key, "name": name}]
        self.call("SetConfig", json.dumps({"custom_targets": targets, "targets": {key: True}}))
        self.window.toast(f"'{name}' will be paused while offline")

    def on_remove_app(self, _button, key):
        targets = [c for c in self.custom_targets() if c["key"] != key]
        self.call("SetConfig", json.dumps({"custom_targets": targets}))

    # ---------- input ----------

    def set_config(self, update):
        if not self.updating and self.status:
            self.call("SetConfig", json.dumps(update))

    def on_switch(self, row, pspec, key):
        self.set_config({key: row.get_active()})

    def on_target(self, row, pspec, key):
        self.set_config({"targets": {key: row.get_active()}})

    def on_spin(self, row, pspec, key):
        value = int(row.get_value())
        self.set_config({key: value if key == "hold_minutes" else value * 60})

    def on_tick(self):
        self.show_summary()
        return GLib.SOURCE_CONTINUE

    def shutdown(self):
        GLib.source_remove(self.tick_id)
        self.bus.signal_unsubscribe(self.signal_id)
        Gio.bus_unwatch_name(self.watch_id)


class AddAppDialog(Adw.Dialog):
    """Pick a running app to add to the offline-pause list."""

    def __init__(self, existing, on_pick):
        super().__init__(title='Add Application', content_width=380, content_height=600)
        self.on_pick = on_pick
        self.existing = existing
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.search = Gtk.SearchEntry(placeholder_text='Search', hexpand=True)
        self.search.connect("search-changed", lambda e: self.list.invalidate_filter())
        toolbar.add_top_bar(header)
        bar = Gtk.Box(margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        bar.append(self.search)
        toolbar.add_top_bar(bar)

        self.list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE,
                                margin_start=12, margin_end=12, margin_top=6, margin_bottom=12,
                                valign=Gtk.Align.START)
        self.list.set_filter_func(self.filter)
        self.spinner = Adw.Spinner(height_request=48, margin_top=24)
        self.hint = Gtk.Label(label='Only running apps are shown. Start the app first if it is missing, then reopen this list.',
                              wrap=True, css_classes=["dim-label"], margin_start=18, margin_end=18,
                              margin_top=6, margin_bottom=18, justify=Gtk.Justification.CENTER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.spinner)
        box.append(self.list)
        box.append(self.hint)
        toolbar.set_content(Gtk.ScrolledWindow(child=box, vexpand=True,
                                               hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.set_child(toolbar)
        in_thread(lambda: apps.candidates(existing), self.fill)

    def message_row(self, title, subtitle=""):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        row.search_text = ""
        return row

    def filter(self, row):
        text = self.search.get_text().strip().lower()
        return not text or text in row.search_text

    def fill(self, found):
        self.spinner.set_visible(False)
        if isinstance(found, Exception):
            self.list.append(self.message_row('Could not list apps', GLib.markup_escape_text(str(found))))
            return
        if not found:
            self.list.append(self.message_row('No Eligible Running Apps'))
            return
        for app in found:
            kind, _, value = app["key"].partition(":")
            what = {"flatpak": "Flatpak", "script": 'script', "comm": 'application'}.get(kind, kind)
            row = Adw.ActionRow(title=GLib.markup_escape_text(app["name"]),
                                subtitle=GLib.markup_escape_text(f"{what} · {value}"),
                                activatable=True)
            row.search_text = f"{app['name']} {what} · {value}".lower()
            image = Gtk.Image(pixel_size=32)
            icon = app.get("icon")
            if icon and icon.startswith("/"):
                image.set_from_file(icon)
            else:
                image.set_from_icon_name(icon or "application-x-executable-symbolic")
            row.add_prefix(image)
            row.add_suffix(Gtk.Image(icon_name="list-add-symbolic"))
            row.connect("activated", self.on_activated, app)
            self.list.append(row)

    def on_activated(self, _row, app):
        self.on_pick(app["key"], app["name"])
        self.close()
