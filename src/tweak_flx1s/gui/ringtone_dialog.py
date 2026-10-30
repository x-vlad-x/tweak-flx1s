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
"""Ringtone volume slider and a short feedbackd preview."""
import subprocess
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib
from tweak_flx1s.const import SERVICE_RINGTONE
from tweak_flx1s.system.ringtone import feedback_profile, get_volume, set_volume, notify_daemon


class RingtoneDialog(Adw.Window):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True, title="Ringtone Volume")
        self.set_default_size(360, 420)
        self.save_source = 0
        self.test_proc = None
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
        page = Adw.PreferencesPage()
        toolbar.set_content(page)
        group = Adw.PreferencesGroup(
            description="Independent of music and video volume. Master mute and feedback profiles still apply.")
        page.add(group)
        self.value_label = Gtk.Label(css_classes=["title-1"], margin_top=12, margin_bottom=6)
        group.add(self.value_label)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.scale.set_draw_value(False)
        self.scale.set_hexpand(True)
        self.scale.set_value(get_volume())
        self.value_label.set_label(f"{int(self.scale.get_value())}%")
        self.scale.connect("value-changed", self._on_changed)
        scale_box = Gtk.Box(spacing=6, margin_bottom=12)
        scale_box.append(Gtk.Image(icon_name="audio-volume-low-symbolic"))
        scale_box.append(self.scale)
        scale_box.append(Gtk.Image(icon_name="audio-volume-high-symbolic"))
        group.add(scale_box)
        self.message = Gtk.Label(wrap=True)
        group.add(self.message)
        preview = Adw.PreferencesGroup()
        page.add(preview)
        self.test_button = Gtk.Button(icon_name="media-playback-start-symbolic",
                                      valign=Gtk.Align.CENTER, tooltip_text="Test Ringtone")
        self.test_button.connect("clicked", self._on_test)
        test_row = Adw.ActionRow(title="Test Ringtone", subtitle="Play for six seconds",
                                 activatable_widget=self.test_button)
        test_row.add_suffix(self.test_button)
        preview.add(test_row)

    def _on_changed(self, scale):
        self.value_label.set_label(f"{int(scale.get_value())}%")
        if self.save_source:
            GLib.source_remove(self.save_source)
        self.save_source = GLib.timeout_add(150, self._save)

    def _save(self):
        self.save_source = 0
        value = int(self.scale.get_value())
        try:
            set_volume(value, notify=False)
        except Exception as exc:
            self.message.set_label(str(exc))
            return False
        def worker():
            try:
                notify_daemon()
            except Exception as exc:
                message = str(exc)
                def show_error():
                    if not self.closed:
                        self.message.set_label(message)
                    return False
                GLib.idle_add(show_error)
        threading.Thread(target=worker, daemon=True).start()
        return False

    def _on_test(self, button):
        if self.test_proc:
            self._stop_test()
            return
        button.set_sensitive(False)
        def worker():
            try:
                active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", SERVICE_RINGTONE],
                                        capture_output=True, timeout=5).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                active = False
            GLib.idle_add(self._start_test, active)
        threading.Thread(target=worker, daemon=True).start()

    def _start_test(self, active):
        if self.closed:
            return False
        button = self.test_button
        button.set_sensitive(True)
        if not active:
            self.message.set_label("Enable Independent Ringtone Volume in Tweaks before testing.")
            return False
        # feedbackd accepts the event in any profile, so silence is not an error to fbcli.
        profile = feedback_profile()
        self.message.set_label("" if profile in (None, "full") else
                               f"The active feedback profile is {profile}, so the ringtone may not be audible.")
        try:
            self.test_proc = Gio.Subprocess.new(
                ["fbcli", "-E", "phone-incoming-call", "-t", "6"],
                Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_SILENCE
                | Gio.SubprocessFlags.STDERR_SILENCE)
            self.test_proc.wait_async(None, self._test_finished)
            button.set_icon_name("media-playback-stop-symbolic")
            button.set_tooltip_text("Stop Test")
        except GLib.Error as exc:
            self.message.set_label(exc.message)
        return False

    def _stop_test(self):
        try:
            self.test_proc.get_stdin_pipe().write_all(b"\n", None)
        except GLib.Error:
            self.test_proc.force_exit()

    def _test_finished(self, proc, result):
        proc.wait_finish(result)
        self.test_proc = None
        if not self.closed:
            if not proc.get_successful():
                self.message.set_label("Could not play the ringtone. Check feedbackd and the active sound profile.")
            self.test_button.set_icon_name("media-playback-start-symbolic")
            self.test_button.set_tooltip_text("Test Ringtone")

    def _on_close(self, window):
        self.closed = True
        if self.save_source:
            GLib.source_remove(self.save_source)
            self._save()
        if self.test_proc:
            self._stop_test()
        return False
