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

import os
import shutil
import subprocess
import gi
import threading
import glob
import re
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
from tweak_flx1s.const import SERVICE_ALARM, SERVICE_GESTURES, SERVICE_DATAMONITOR, APP_NAME
from tweak_flx1s.utils import run_command, check_dependency, logger
from tweak_flx1s.system.sounds import SoundManager
from tweak_flx1s.gui.dialogs import ExecutionDialog
from tweak_flx1s.gui.weather_dialog import WeatherDialog
from tweak_flx1s.core.datamonitor import DataMonitor
from tweak_flx1s.const import SERVICE_OFFLINE_PAUSE

class BypassDialog(Adw.MessageDialog):
    def __init__(self, parent, callback):
        super().__init__(
            transient_for=parent,
            heading="Temporary Limit Bypass",
            body="Specify the data allowance (in MB) for this bypass:",
            modal=True
        )
        self.callback = callback

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.entry = Gtk.Entry()
        self.entry.set_text("500")
        self.entry.set_placeholder_text("Allowance in MB")
        self.entry.set_activates_default(True)
        box.append(self.entry)

        self.set_extra_child(box)

        self.add_response("cancel", "Cancel")
        self.add_response("ok", "Set Bypass")
        self.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("ok")

        self.connect("response", self._on_response)

    def _on_response(self, dialog, response):
        if response == "ok":
            try:
                val = int(self.entry.get_text().strip())
                if val > 0:
                    self.callback(val)
                    return
            except ValueError:
                pass
        self.callback(None)

class TweaksPage(Adw.PreferencesPage):
    """
    Page for general tweaks.
    Includes: Alarm Volume, Custom Sounds, Appearance.
    """
    def __init__(self, window, **kwargs):
        super().__init__(title="Tweaks", icon_name="preferences-system-symbolic", **kwargs)
        self.window = window
        self.sounds = SoundManager()
        self._bypass_start_bytes = 0

        appearance_grp = Adw.PreferencesGroup(title="Appearance")
        self.add(appearance_grp)

        css_row = Adw.SwitchRow(title="GTK3 CSS Tweak", subtitle="Apply custom UI scaling tweaks for GTK3 apps")
        css_row.set_active(self._is_gtk_tweak_active())
        css_row.connect("notify::active", lambda r, p: GLib.idle_add(lambda: self._on_css_toggled(r, p) or False))
        appearance_grp.add(css_row)

        weather_row = Adw.ActionRow(title="Weather Location", subtitle="Configure GNOME Weather location")
        weather_btn = Gtk.Button(label="Configure")
        weather_btn.set_valign(Gtk.Align.CENTER)
        weather_btn.connect("clicked", lambda x: GLib.idle_add(lambda: self._on_weather_clicked() or False))
        weather_row.add_suffix(weather_btn)
        appearance_grp.add(weather_row)

        svc_group = Adw.PreferencesGroup(title="Background Services")
        self.add(svc_group)

        self._add_service_row(svc_group, "Alarm Volume Fix", "Ensure alarm plays at full volume", SERVICE_ALARM)

        self._add_service_row(svc_group, "Offline Pause", "Pause selected apps when internet is unavailable", SERVICE_OFFLINE_PAUSE)
        power_row = Adw.ActionRow(title="Offline Pause and Power Tools",
                                  subtitle="Apps, background services and account synchronization")
        power_button = Gtk.Button(label="Configure", valign=Gtk.Align.CENTER)
        def open_power_tools(button):
            from tweak_flx1s.gui.offline_pause.dialog import OfflinePauseDialog
            OfflinePauseDialog(self.window).present()
        power_button.connect("clicked", open_power_tools)
        power_row.add_suffix(power_button)
        svc_group.add(power_row)


        self._build_datamonitor_section()

        sound_group = Adw.PreferencesGroup(title="Audio")
        self.add(sound_group)

        sound_row = Adw.SwitchRow(title="Custom Sound Theme", subtitle="Use fastflx1 custom sounds")
        sound_row.set_active(self.sounds.is_custom_theme_active())
        sound_row.connect("notify::active", lambda r, p: GLib.idle_add(lambda: self._on_sound_toggled(r, p) or False))
        sound_group.add(sound_row)

    def _get_css_paths(self):
        """Returns (source_path, target_path) for GTK3 CSS."""
        source = "/usr/share/tweak-flx1s/configs/gtk-3.0/gtk.css"
        if not os.path.exists(source):
            source = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../data/configs/gtk-3.0/gtk.css"))
        target = os.path.expanduser("~/.config/gtk-3.0/gtk.css")
        return source, target

    GTK_CSS_MARKER = "/* managed by tweak-flx1s */"

    def _install_battery_charging_icons(self):
        """Generates and installs green charging symbolic icons into ~/.local/share/icons."""
        src_status = "/usr/share/icons/Adwaita/symbolic/status"
        src_legacy = "/usr/share/icons/Adwaita/symbolic/legacy"
        if not os.path.exists(src_status):
            logger.warning(f"Adwaita status icon dir not found at {src_status}")
            return

        files = (
            glob.glob(f"{src_status}/battery*charging*.svg") +
            glob.glob(f"{src_status}/battery-level-100-charged-symbolic.svg")
        )
        legacy_files = (
            glob.glob(f"{src_legacy}/battery*charging*.svg")
            if os.path.isdir(src_legacy) else []
        )

        dest_themes = ["Adwaita", "Yaru", "hicolor"]
        for theme in dest_themes:
            dest_status_dir = os.path.expanduser(f"~/.local/share/icons/{theme}/symbolic/status")
            dest_legacy_dir = os.path.expanduser(f"~/.local/share/icons/{theme}/symbolic/legacy")
            os.makedirs(dest_status_dir, exist_ok=True)
            os.makedirs(dest_legacy_dir, exist_ok=True)

            for f in files:
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        content = fp.read()
                    new_content = re.sub(r"<path(?!\s+class=)", r'<path class="success"', content)
                    new_content = re.sub(r"<g(?!\s+class=)", r'<g class="success"', new_content)
                    with open(os.path.join(dest_status_dir, os.path.basename(f)), "w", encoding="utf-8") as fp:
                        fp.write(new_content)
                except Exception as e:
                    logger.error(f"Failed to install {f}: {e}")

            for f in legacy_files:
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        content = fp.read()
                    new_content = re.sub(r"<path(?!\s+class=)", r'<path class="success"', content)
                    new_content = re.sub(r"<g(?!\s+class=)", r'<g class="success"', new_content)
                    with open(os.path.join(dest_legacy_dir, os.path.basename(f)), "w", encoding="utf-8") as fp:
                        fp.write(new_content)
                except Exception as e:
                    logger.error(f"Failed to install legacy {f}: {e}")

            if check_dependency("gtk-update-icon-cache"):
                run_command(f"gtk-update-icon-cache -f -q -t ~/.local/share/icons/{theme}", check=False)
        logger.info("Installed green battery charging icons")

    def _remove_battery_charging_icons(self):
        """Removes custom green charging symbolic icons from ~/.local/share/icons."""
        dest_themes = ["Adwaita", "Yaru", "hicolor"]
        for theme in dest_themes:
            for subdir in ["status", "legacy"]:
                dir_path = os.path.expanduser(f"~/.local/share/icons/{theme}/symbolic/{subdir}")
                if os.path.isdir(dir_path):
                    for f in glob.glob(f"{dir_path}/battery*charging*.svg") + glob.glob(f"{dir_path}/battery-level-100-charged-symbolic.svg"):
                        try:
                            os.remove(f)
                        except Exception as e:
                            logger.error(f"Failed to remove {f}: {e}")
            if check_dependency("gtk-update-icon-cache"):
                run_command(f"gtk-update-icon-cache -f -q -t ~/.local/share/icons/{theme}", check=False)
        logger.info("Removed green battery charging icons")

    def _is_gtk_tweak_active(self):
        """Checks if OUR custom GTK CSS is applied — a gtk.css the user wrote
        themselves must not read as active (toggling off would delete it)."""
        source, target = self._get_css_paths()
        if not os.path.exists(target):
            return False
        try:
            with open(target, "r") as f:
                content = f.read()
            if self.GTK_CSS_MARKER in content:
                return True
            # Backward compat: earlier versions copied the source verbatim.
            if os.path.exists(source):
                with open(source, "r") as f:
                    if f.read() == content:
                        return True
        except Exception as e:
            logger.error(f"Failed to inspect {target}: {e}")
        return False

    def _on_css_toggled(self, row, param):
        source, target = self._get_css_paths()
        active = row.get_active()

        if active:
            try:
                target_dir = os.path.dirname(target)
                os.makedirs(target_dir, exist_ok=True)
                with open(source, "r") as f:
                    css = f.read()
                with open(target, "w") as f:
                    f.write(f"{self.GTK_CSS_MARKER}\n{css}")
                self._install_battery_charging_icons()
                logger.info(f"Applied GTK3 CSS tweak to {target}")
            except Exception as e:
                logger.error(f"Failed to apply GTK3 CSS tweak: {e}")
        else:
            try:
                if os.path.exists(target):
                    if self._is_gtk_tweak_active():
                        os.remove(target)
                        logger.info(f"Removed GTK3 CSS tweak from {target}")
                    else:
                        logger.warning(f"{target} was not created by Tweak-FLX1s — leaving it untouched")
                self._remove_battery_charging_icons()
            except Exception as e:
                logger.error(f"Failed to remove GTK3 CSS tweak: {e}")


        dlg = Adw.MessageDialog(
            transient_for=self.window,
            heading="Restart Phosh",
            body="Should we restart phosh to take affect?"
        )
        dlg.add_response("cancel", "No")
        dlg.add_response("restart", "Yes")
        dlg.set_response_appearance("restart", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, r):
            if r == "restart":
                cmd = "systemctl restart phosh"
                exec_dlg = ExecutionDialog(self.window, "Restarting Phosh", cmd, as_root=True)
                exec_dlg.present()
            d.close()

        dlg.connect("response", on_response)
        dlg.present()

    def _add_service_row(self, group, title, subtitle, service_name):
        row = Adw.SwitchRow(title=title, subtitle=subtitle)

        is_running = self._is_service_active_and_enabled(service_name)
        row.set_active(is_running)

        handler_id = row.connect("notify::active", lambda r, p, s=service_name: GLib.idle_add(lambda: self._on_switch_toggled(r, p, s) or False))
        row.handler_id = handler_id
        group.add(row)

    def _is_service_active_and_enabled(self, service, user_bus=True):
        """Checks if a service is both enabled and active."""
        try:
            cmd_enabled = ["systemctl"]
            cmd_active = ["systemctl"]
            if user_bus:
                cmd_enabled.append("--user")
                cmd_active.append("--user")
            cmd_enabled.extend(["is-enabled", "--quiet", service])
            cmd_active.extend(["is-active", "--quiet", service])
            return subprocess.call(cmd_enabled) == 0 and subprocess.call(cmd_active) == 0
        except Exception as e:
            logger.warning(f"Failed to check status for {service}: {e}")
            return False

    def _sync_switch_state(self, row, state):
        if hasattr(row, "handler_id"):
            row.handler_block(row.handler_id)
        row.set_active(state)
        if hasattr(row, "handler_id"):
            row.handler_unblock(row.handler_id)
        row.set_sensitive(True)

        if row == getattr(self, "dm_switch", None):
            self.dm.config.set("enabled", state)
            self._update_dm_ui_state()

    def _on_switch_toggled(self, row, param, service):
        should_be_active = row.get_active()

        GLib.idle_add(lambda: row.set_sensitive(False))

        def run_tweak_service_toggle():
            try:
                if should_be_active:
                    run_command(f"systemctl --user enable {service}")
                    run_command("systemctl --user daemon-reload")
                    run_command(f"systemctl --user start {service}")
                else:
                    run_command(f"systemctl --user stop {service}")
                    run_command(f"systemctl --user disable {service}")
                    run_command("systemctl --user daemon-reload")
            except Exception as e:
                logger.error(f"Failed to toggle service {service}: {e}")

            is_ok = self._is_service_active_and_enabled(service)
            GLib.idle_add(lambda: self._sync_switch_state(row, is_ok))

        threading.Thread(target=run_tweak_service_toggle, daemon=True).start()

    def _on_sound_toggled(self, row, param):
        active = row.get_active()
        success = False
        if active:
            success = self.sounds.enable_custom_theme()
            if success:
                logger.info("Custom sound theme enabled.")
        else:
            success = self.sounds.disable_custom_theme()
            if success:
                logger.info("Custom sound theme disabled.")

        if not success:
            logger.error("Failed to toggle sound theme.")
            row.set_active(not active)

    def _on_weather_clicked(self):
        try:
            dlg = WeatherDialog(self.get_native())
            dlg.present()
        except Exception as e:
            logger.error(f"Failed to open weather dialog: {e}")

    def _build_datamonitor_section(self):
        self.dm = DataMonitor()
        self._set_buttons = {}
        self._validation_errors = {}

        self.dm_group = Adw.PreferencesGroup(title="Mobile Data Monitor (Modem Only)", description="Monitor and restrict cellular modem data usage to prevent unexpected bills")
        self.add(self.dm_group)

        # Status banner (only visible if service is enabled and limits are full / bypass is active)
        self.status_banner = Adw.ActionRow()
        self.status_banner.set_title_lines(0)
        self.status_banner.set_subtitle_lines(0)
        self.status_banner_icon = Gtk.Image()
        self.status_banner.add_prefix(self.status_banner_icon)
        self.status_banner.set_visible(False)
        self.dm_group.add(self.status_banner)

        # Transient notice shown UNDER the status banner (e.g. the interval-
        # change hint). Separate row so it never overrides the enforcement
        # banner above it; auto-hides after a few seconds.
        self.notice_banner = Adw.ActionRow()
        self.notice_banner.set_title_lines(0)
        self.notice_banner.set_subtitle_lines(0)
        self.notice_banner_icon = Gtk.Image()
        self.notice_banner.add_prefix(self.notice_banner_icon)
        self.notice_banner.set_visible(False)
        self.dm_group.add(self.notice_banner)
        self._notice_timeout_id = 0

        self.dm_switch = Adw.SwitchRow(title="Enable Data Monitoring", subtitle="Requires vnstat daemon to be running")
        self.dm_switch.set_sensitive(False)
        self.dm_group.add(self.dm_switch)

        def load_initial_switch_state():
            is_active = self._is_service_active_and_enabled(SERVICE_DATAMONITOR)
            gsm_names = self.dm.list_gsm_connections()
            def apply_initial_state():
                self.dm_switch.set_active(is_active)
                handler_id = self.dm_switch.connect("notify::active", lambda r, p: GLib.idle_add(lambda: self._on_dm_toggled(r) or False))
                self.dm_switch.handler_id = handler_id
                self.dm_switch.set_sensitive(True)
                self._build_apn_row(gsm_names, is_active)
                self._update_dm_ui_state()
                self._validate_all_inputs()
                self._fetch_stats_on_demand()
            GLib.idle_add(apply_initial_state)

        threading.Thread(target=load_initial_switch_state, daemon=True).start()

        self.daily_row = self._create_usage_row("Daily Limit (MB)", "daily_limit_mb", "daily_mb")
        self.dm_group.add(self.daily_row)

        self.weekly_row = self._create_usage_row("Weekly Limit (MB)", "weekly_limit_mb", "weekly_mb")
        self.dm_group.add(self.weekly_row)

        self.monthly_row = self._create_usage_row("Monthly Limit (MB)", "monthly_limit_mb", "monthly_mb")
        self.dm_group.add(self.monthly_row)

        # Bypass Limit row is created here (so validation/_set_buttons are wired
        # before the async validate runs) but added to the group further down,
        # directly under the Temporary Limit Bypass toggle it belongs to.
        self.bypass_limit_row = self._create_usage_row("Bypass Limit (MB)", "bypass_limit_mb", "bypass_limit")

        self.interval_row = Adw.EntryRow(title="Check Interval (minutes)")
        self.interval_row.set_text(str(self.dm.config.get("check_interval_min", 5)))
        
        interval_set_btn = Gtk.Button(label="Set")
        interval_set_btn.set_valign(Gtk.Align.CENTER)
        self.interval_row.add_prefix(interval_set_btn)
        self._set_buttons["check_interval_min"] = interval_set_btn
        
        interval_set_btn.connect("clicked", lambda b: self._on_set_clicked(self.interval_row, "check_interval_min"))
        self.interval_row.connect("changed", lambda r: self._validate_all_inputs())
        self.dm_group.add(self.interval_row)

        self.action_row = Adw.SwitchRow(title="Disable Connection on Limit Exceeded", subtitle="Turn off mobile data when limits are reached (always sends notification)")
        self.action_row.set_active(self.dm.config.get("action", "notify") == "disable")
        self.action_row.connect("notify::active", lambda r, p: self._apply_setup_change("action", "disable" if r.get_active() else "notify"))
        self.dm_group.add(self.action_row)

        self.bypass_silent_row = Adw.SwitchRow(title="Bypass Silent Mode", subtitle="Play critical alert sound")
        self.bypass_silent_row.set_active(self.dm.config.get("bypass_silent", False))
        self.bypass_silent_row.connect("notify::active", lambda r, p: self._apply_setup_change("bypass_silent", r.get_active()))
        self.dm_group.add(self.bypass_silent_row)

        self.temp_bypass_row = Adw.SwitchRow(title="Temporary Limit Bypass", subtitle="Temporarily bypass limits to re-enable mobile data")
        self.temp_bypass_row.set_active(self.dm.config.get("temporary_bypass", False))
        temp_bypass_handler = self.temp_bypass_row.connect("notify::active", lambda r, p: GLib.idle_add(lambda: self._on_temp_bypass_toggled(r) or False))
        self.temp_bypass_row_handler_id = temp_bypass_handler
        self.dm_group.add(self.temp_bypass_row)

        # Sits right under its toggle; only visible while a bypass is active
        # (see _update_dm_ui_state).
        self.dm_group.add(self.bypass_limit_row)

        self.default_mobile_data_row = Adw.SwitchRow(title="Default Mobile Data", subtitle="Keep mobile data ON by default when not restricted by limits")
        self.default_mobile_data_row.set_active(self.dm.config.get("default_mobile_data", True))
        self.default_mobile_data_row.connect("notify::active", lambda r, p: self._apply_setup_change("default_mobile_data", r.get_active()))
        self.dm_group.add(self.default_mobile_data_row)

        self.default_roaming_row = Adw.SwitchRow(title="Default Roaming", subtitle="Keep roaming ON by default when not restricted by limits")
        self.default_roaming_row.set_active(self.dm.config.get("default_roaming", False))
        self.default_roaming_row.connect("notify::active", lambda r, p: self._apply_setup_change("default_roaming", r.get_active()))
        self.dm_group.add(self.default_roaming_row)

        self.default_autoconnect_row = Adw.SwitchRow(title="Default Autoconnect", subtitle="Keep autoconnect ON by default when not restricted by limits")
        self.default_autoconnect_row.set_active(self.dm.config.get("default_autoconnect", True))
        self.default_autoconnect_row.connect("notify::active", lambda r, p: self._apply_setup_change("default_autoconnect", r.get_active()))
        self.dm_group.add(self.default_autoconnect_row)

        # APN / GSM connection picker — built asynchronously once the GSM
        # profiles have been enumerated (see load_initial_switch_state), so the
        # nmcli listing never blocks the GTK main thread.
        self.apn_row = None
        self._apn_names = []

        # Initial UI state and validation are loaded asynchronously above

    def _create_usage_row(self, title, config_key, usage_key):
        row = Adw.EntryRow(title=title)
        row.set_text(str(self.dm.config.get(config_key, 1000)))
        
        set_btn = Gtk.Button(label="Set")
        set_btn.set_valign(Gtk.Align.CENTER)
        row.add_prefix(set_btn)
        
        self._set_buttons[config_key] = set_btn
        set_btn.connect("clicked", lambda b: self._on_set_clicked(row, config_key))
        row.connect("changed", lambda r: self._validate_all_inputs())
        
        return row

    def _on_set_clicked(self, row, config_key):
        text = row.get_text().strip()
        try:
            val = int(text)
            self._apply_setup_change(config_key, val)
        except ValueError:
            pass

    def _apply_setup_change(self, config_key, val):
        self.dm_group.set_sensitive(False)

        def run_change_flow():
            try:
                logger.info(f"Saving config change: {config_key} = {val}")
                # Captured before the write: the notice below lives for one
                # old interval, after which the daemon has definitely ticked
                # and rescheduled — i.e. the change is guaranteed in effect.
                prev_interval = self.dm.config.get("check_interval_min", 5)
                self.dm.config.set(config_key, val)

                usage = self.dm.get_usage()
                if usage:
                    d_limit = self.dm.config.get("daily_limit_mb", 1000)
                    w_limit = self.dm.config.get("weekly_limit_mb", 5000)
                    m_limit = self.dm.config.get("monthly_limit_mb", 20000)

                    limit_exceeded = (usage["daily_mb"] >= d_limit or
                                      usage["weekly_mb"] >= w_limit or
                                      usage["monthly_mb"] >= m_limit)

                    over_limit = limit_exceeded and not self.dm.config.get("temporary_bypass", False)
                    if over_limit and self.dm.config.get("action", "notify") == "disable":
                        logger.info("Limit exceeded after config change. Disabling connection.")
                        if self.dm.disable_connection():
                            GLib.idle_add(self._notify_restricted)
                    else:
                        # Not suppressing (limits clear, or switched to notify
                        # while over limit) → bring a previously disabled link
                        # back up so turning off auto-disable restores data.
                        logger.info("Not suppressing after config change. Restoring connection.")
                        self.dm.restore_connection()

                # No service poke on interval change: the running daemon
                # reloads its config and reschedules itself on its next tick
                # (see _on_timer_check). A shortened interval therefore takes
                # effect after at most one old cycle — deliberately avoiding a
                # restart, which would reset the daemon's restricted-state
                # tracking and could re-enable a connection the user turned off.

                def update_ui():
                    self._update_dm_ui_state()
                    self.dm_group.set_sensitive(True)
                    self._validate_all_inputs()
                    self._fetch_stats_on_demand()
                    # Interval changes only take effect on the daemon's next
                    # tick, so warn the user — as a separate banner under any
                    # active enforcement message, never replacing it. The
                    # banner clears itself after one old interval, by which
                    # point the new interval is guaranteed to be in effect.
                    if config_key == "check_interval_min" and self.dm_switch.get_active():
                        try:
                            prev_min = max(1, int(prev_interval))
                        except (TypeError, ValueError):
                            prev_min = None
                        try:
                            changed = int(prev_interval) != int(val)
                        except (TypeError, ValueError):
                            changed = True
                        if changed:
                            hide_after = (prev_min or 1) * 60
                            # Naming the old interval explains the wait — and
                            # why this banner may stay up for a while.
                            wait = f" — up to {prev_min} min (the previous interval)" if prev_min else ""
                            self._show_notice(
                                "Interval Updated",
                                f"The new check interval takes effect after the current polling cycle finishes{wait}.",
                                timeout_s=hide_after,
                            )

                GLib.idle_add(update_ui)
            except Exception as e:
                logger.error(f"Failed in config change flow: {e}")
                GLib.idle_add(lambda: self.dm_group.set_sensitive(True))

        threading.Thread(target=run_change_flow, daemon=True).start()

    def _validate_all_inputs(self):
        daily_text = self.daily_row.get_text().strip()
        weekly_text = self.weekly_row.get_text().strip()
        monthly_text = self.monthly_row.get_text().strip()
        interval_text = self.interval_row.get_text().strip()
        bypass_text = self.bypass_limit_row.get_text().strip()

        daily_val = None
        daily_error = None
        try:
            daily_val = int(daily_text)
            if daily_val <= 0:
                daily_error = "Must be a positive integer"
        except ValueError:
            daily_error = "Must be a valid integer"

        weekly_val = None
        weekly_error = None
        try:
            weekly_val = int(weekly_text)
            if weekly_val <= 0:
                weekly_error = "Must be a positive integer"
        except ValueError:
            weekly_error = "Must be a valid integer"

        monthly_val = None
        monthly_error = None
        try:
            monthly_val = int(monthly_text)
            if monthly_val <= 0:
                monthly_error = "Must be a positive integer"
        except ValueError:
            monthly_error = "Must be a valid integer"

        interval_val = None
        interval_error = None
        try:
            interval_val = int(interval_text)
            if interval_val < 1:
                interval_error = "Must be at least 1 minute"
        except ValueError:
            interval_error = "Must be a valid integer"

        bypass_val = None
        bypass_error = None
        try:
            bypass_val = int(bypass_text)
            if bypass_val <= 0:
                bypass_error = "Must be a positive integer"
        except ValueError:
            bypass_error = "Must be a valid integer"

        if not daily_error and not weekly_error:
            if daily_val > weekly_val:
                daily_error = f"Cannot exceed weekly limit ({weekly_val} MB)"
                weekly_error = f"Cannot be less than daily limit ({daily_val} MB)"

        if not weekly_error and not monthly_error:
            if weekly_val > monthly_val:
                weekly_error = f"Cannot exceed monthly limit ({monthly_val} MB)"
                monthly_error = f"Cannot be less than weekly limit ({weekly_val} MB)"

        if not daily_error and not monthly_error:
            if daily_val > monthly_val:
                daily_error = f"Cannot exceed monthly limit ({monthly_val} MB)"
                monthly_error = f"Cannot be less than daily limit ({daily_val} MB)"

        self._validation_errors = {
            "daily_limit_mb": daily_error,
            "weekly_limit_mb": weekly_error,
            "monthly_limit_mb": monthly_error,
            "check_interval_min": interval_error,
            "bypass_limit_mb": bypass_error
        }

        self._apply_row_validation(self.daily_row, self._set_buttons["daily_limit_mb"], "Daily Limit (MB)", daily_error)
        self._apply_row_validation(self.weekly_row, self._set_buttons["weekly_limit_mb"], "Weekly Limit (MB)", weekly_error)
        self._apply_row_validation(self.monthly_row, self._set_buttons["monthly_limit_mb"], "Monthly Limit (MB)", monthly_error)
        self._apply_row_validation(self.interval_row, self._set_buttons["check_interval_min"], "Check Interval (minutes)", interval_error)
        self._apply_row_validation(self.bypass_limit_row, self._set_buttons["bypass_limit_mb"], "Bypass Limit (MB)", bypass_error)

    def _apply_row_validation(self, row, set_btn, base_title, error_msg):
        if error_msg:
            row.add_css_class("error")
            row.set_tooltip_text(error_msg)
            current_title = row.get_title()
            if " - Error:" in current_title:
                current_title = current_title.split(" - Error:")[0]
            row.set_title(f"{current_title} - Error: {error_msg}")
            set_btn.set_sensitive(False)
        else:
            row.remove_css_class("error")
            row.set_tooltip_text(None)
            current_title = row.get_title()
            if " - Error:" in current_title:
                row.set_title(current_title.split(" - Error:")[0])
            set_btn.set_sensitive(True)

    def _show_notice(self, title, subtitle, icon_name="dialog-warning-symbolic", css_class="warning", timeout_s=12):
        """Shows the transient notice banner under the status banner. Does not
        touch the status banner, so an active enforcement message stays put."""
        if self._notice_timeout_id > 0:
            GLib.source_remove(self._notice_timeout_id)
            self._notice_timeout_id = 0
        for c in ("error", "warning", "success"):
            self.notice_banner.remove_css_class(c)
        self.notice_banner.set_title(title)
        self.notice_banner.set_subtitle(subtitle)
        self.notice_banner_icon.set_from_icon_name(icon_name)
        if css_class:
            self.notice_banner.add_css_class(css_class)
        self.notice_banner.set_visible(True)
        if timeout_s > 0:
            self._notice_timeout_id = GLib.timeout_add_seconds(timeout_s, self._hide_notice)

    def _hide_notice(self):
        self.notice_banner.set_visible(False)
        self._notice_timeout_id = 0
        return GLib.SOURCE_REMOVE

    def _update_status_banner(self, visible, title="", subtitle="", icon_name="", css_class=""):
        self.status_banner.remove_css_class("error")
        self.status_banner.remove_css_class("warning")
        self.status_banner.remove_css_class("success")
        
        if visible and self.dm.config.get("enabled", False):
            self.status_banner.set_title(title)
            self.status_banner.set_subtitle(subtitle)
            self.status_banner_icon.set_from_icon_name(icon_name)
            if css_class:
                self.status_banner.add_css_class(css_class)
            self.status_banner.set_visible(True)
        else:
            self.status_banner.set_visible(False)

    def _on_dm_toggled(self, row):
        active = row.get_active()
        self.dm.config.set("enabled", active)
        self._update_dm_ui_state()

        GLib.idle_add(lambda: row.set_sensitive(False))

        def run_dm_service_toggle():
            try:
                if active:
                    run_command(f"systemctl --user enable {SERVICE_DATAMONITOR}")
                    run_command("systemctl --user daemon-reload")
                    # Enforce + notify BEFORE starting the daemon so this GUI
                    # path (reliable, running loop) owns the alert; the daemon's
                    # first check then sees the link already down and stays
                    # silent — no race between the two, no missed notification.
                    usage = self.dm.get_usage()
                    if usage:
                        d_limit = self.dm.config.get("daily_limit_mb", 1000)
                        w_limit = self.dm.config.get("weekly_limit_mb", 5000)
                        m_limit = self.dm.config.get("monthly_limit_mb", 20000)
                        
                        limit_exceeded = (usage["daily_mb"] >= d_limit or
                                          usage["weekly_mb"] >= w_limit or
                                          usage["monthly_mb"] >= m_limit)
                        if limit_exceeded:
                            if not self.dm.config.get("temporary_bypass", False):
                                action = self.dm.config.get("action", "notify")
                                if action == "disable":
                                    logger.info("Limits exceeded on service start. Disabling connection.")
                                    if self.dm.disable_connection():
                                        GLib.idle_add(self._notify_restricted)
                    run_command(f"systemctl --user start {SERVICE_DATAMONITOR}")
                else:
                    run_command(f"systemctl --user stop {SERVICE_DATAMONITOR}")
                    run_command(f"systemctl --user disable {SERVICE_DATAMONITOR}")
                    run_command("systemctl --user daemon-reload")
            except Exception as e:
                logger.error(f"Failed to toggle service {SERVICE_DATAMONITOR}: {e}")

            is_ok = self._is_service_active_and_enabled(SERVICE_DATAMONITOR)
            def post_toggle():
                self._sync_switch_state(row, is_ok)
                self._fetch_stats_on_demand()
            GLib.idle_add(post_toggle)

        threading.Thread(target=run_dm_service_toggle, daemon=True).start()

    def _on_temp_bypass_toggled(self, row):
        active = row.get_active()
        if active and not self.dm.config.get("temporary_bypass", False):
            dialog = BypassDialog(self.get_native(), self._on_bypass_dialog_response)
            dialog.present()
        elif not active and self.dm.config.get("temporary_bypass", False):
            self._disable_bypass_flow()

    def _on_bypass_dialog_response(self, limit_mb):
        if limit_mb is not None:
            self.dm_group.set_sensitive(False)
            def run_enable_bypass():
                try:
                    current_bytes = self.dm.get_current_total_bytes()
                    self.dm.config.set("bypass_start_bytes", current_bytes)
                    self.dm.config.set("bypass_limit_mb", limit_mb)
                    self.dm.config.set("temporary_bypass", True)

                    # A bypass only stops the monitor from disabling data; it
                    # never forces data on. Restore to the configured defaults
                    # (respects Default Mobile Data / roaming / autoconnect).
                    self.dm.restore_connection()

                    def update_ui():
                        self.bypass_limit_row.set_text(str(limit_mb))
                        self._update_dm_ui_state()
                        self.dm_group.set_sensitive(True)
                        self._fetch_stats_on_demand()
                    GLib.idle_add(update_ui)
                except Exception as e:
                    logger.error(f"Failed to enable bypass: {e}")
                    GLib.idle_add(lambda: self.dm_group.set_sensitive(True))
                    GLib.idle_add(lambda: self._sync_switch_state(self.temp_bypass_row, False))
            threading.Thread(target=run_enable_bypass, daemon=True).start()
        else:
            self._sync_switch_state(self.temp_bypass_row, False)

    def _notify_restricted(self):
        """Alert that mobile data was just disabled by enforcement. Must run on
        the main thread (call via GLib.idle_add) so notify_critical's
        libfeedback works; respects Bypass Silent Mode. The daemon stays silent
        for the same event because the link is already down when it next checks."""
        from tweak_flx1s.services.datamonitor import notify_critical
        from tweak_flx1s.utils import send_notification
        title = "Mobile Data Restricted"
        body = ("A data limit was reached and mobile data has been disabled. "
                "Open Tweak-FLX1s to enable Limit Bypass or raise your limits.")
        if self.dm.config.get("bypass_silent", False):
            notify_critical(title, body)
        else:
            send_notification(title, body, id="data-limit-alert", urgent=True)
        return False

    def _disable_bypass_flow(self):
        self.dm_group.set_sensitive(False)
        def run_disable_bypass():
            try:
                self.dm.config.set("temporary_bypass", False)
                # Only pull data down if enforcement actually applies now (over
                # a limit AND Disable-on-exceeded on). In notify mode the app
                # must never disable data, so restore to defaults instead.
                usage = self.dm.get_usage()
                over_limit = bool(usage) and (
                    usage["daily_mb"] >= self.dm.config.get("daily_limit_mb", 1000) or
                    usage["weekly_mb"] >= self.dm.config.get("weekly_limit_mb", 5000) or
                    usage["monthly_mb"] >= self.dm.config.get("monthly_limit_mb", 20000)
                )
                if over_limit and self.dm.config.get("action", "notify") == "disable":
                    if self.dm.disable_connection():
                        GLib.idle_add(self._notify_restricted)
                else:
                    self.dm.restore_connection()

                def update_ui():
                    self._update_dm_ui_state()
                    self.dm_group.set_sensitive(True)
                    self._fetch_stats_on_demand()
                GLib.idle_add(update_ui)
            except Exception as e:
                logger.error(f"Failed to disable bypass: {e}")
                GLib.idle_add(lambda: self.dm_group.set_sensitive(True))
        threading.Thread(target=run_disable_bypass, daemon=True).start()

    def _update_dm_ui_state(self):
        active = self.dm.config.get("enabled", False)
        if not active:
            self._update_status_banner(False)
            # Drop any lingering notice (e.g. interval hint) when disabling.
            if self._notice_timeout_id > 0:
                GLib.source_remove(self._notice_timeout_id)
                self._notice_timeout_id = 0
            self.notice_banner.set_visible(False)
        self.daily_row.set_visible(active)
        self.weekly_row.set_visible(active)
        self.monthly_row.set_visible(active)
        self.interval_row.set_visible(active)
        self.action_row.set_visible(active)
        self.bypass_silent_row.set_visible(active)
        self.temp_bypass_row.set_visible(active)
        self.default_mobile_data_row.set_visible(active)
        self.default_roaming_row.set_visible(active)
        self.default_autoconnect_row.set_visible(active)
        if self.apn_row is not None:
            self.apn_row.set_visible(active)
        self.bypass_limit_row.set_visible(active and self.dm.config.get("temporary_bypass", False))

    def _build_apn_row(self, names, active):
        """(Re)build the GSM connection picker: a dropdown when several profiles
        exist, a read-only name when there is one, a hint when there is none.
        The chosen profile is stored in config and is what every nmcli command
        targets."""
        if self.apn_row is not None:
            self.dm_group.remove(self.apn_row)
            self.apn_row = None
        self._apn_names = names

        if not names:
            row = Adw.ActionRow(title="Mobile Data Connection",
                                subtitle="No mobile connection detected")
        elif len(names) == 1:
            row = Adw.ActionRow(title="Mobile Data Connection", subtitle=names[0])
            if self.dm.config.get("gsm_connection", "") != names[0]:
                self.dm.config.set("gsm_connection", names[0])
        else:
            row = Adw.ComboRow(title="Mobile Data Connection",
                               subtitle="Which mobile profile the monitor controls")
            model = Gtk.StringList()
            for n in names:
                model.append(n)
            row.set_model(model)
            configured = self.dm.config.get("gsm_connection", "")
            idx = names.index(configured) if configured in names else 0
            row.set_selected(idx)
            if configured not in names:
                self.dm.config.set("gsm_connection", names[idx])
            # Connect after the initial selection so it doesn't self-fire.
            row.connect("notify::selected", self._on_apn_selected)

        row.set_visible(active)
        self.dm_group.add(row)
        self.apn_row = row

    def _on_apn_selected(self, row, _param):
        idx = row.get_selected()
        if 0 <= idx < len(self._apn_names):
            self.dm.config.set("gsm_connection", self._apn_names[idx])
            logger.info(f"APN connection set to: {self._apn_names[idx]}")

    def _fetch_stats_on_demand(self):
        if not self.dm.config.get("enabled", False):
            self.daily_row.set_title("Daily Limit (MB)")
            self.weekly_row.set_title("Weekly Limit (MB)")
            self.monthly_row.set_title("Monthly Limit (MB)")
            return

        def fetch_stats():
            usage = self.dm.get_usage()
            GLib.idle_add(lambda: self._apply_dm_stats_ui(usage))

        threading.Thread(target=fetch_stats, daemon=True).start()

    def _apply_dm_stats_ui(self, usage):
        if not self.dm.config.get("enabled", False):
            self._update_status_banner(False)
            return

        if usage:
            d_limit = self.dm.config.get("daily_limit_mb", 1000)
            w_limit = self.dm.config.get("weekly_limit_mb", 5000)
            m_limit = self.dm.config.get("monthly_limit_mb", 20000)

            exceeded_limit = None
            used_val = 0
            limit_val = 0

            for key, row, base_title, u_val, l_val in [
                ("daily_limit_mb", self.daily_row, "Daily Limit (MB)", usage['daily_mb'], d_limit),
                ("weekly_limit_mb", self.weekly_row, "Weekly Limit (MB)", usage['weekly_mb'], w_limit),
                ("monthly_limit_mb", self.monthly_row, "Monthly Limit (MB)", usage['monthly_mb'], m_limit)
            ]:
                err = self._validation_errors.get(key)
                if err:
                    row.set_title(f"{base_title} (Used: {u_val:.1f} MB) - Error: {err}")
                else:
                    row.set_title(f"{base_title} (Used: {u_val:.1f} MB)")
                
                if not err and u_val >= l_val and exceeded_limit is None:
                    exceeded_limit = base_title.split(" ")[0]
                    used_val = u_val
                    limit_val = l_val

            if self.dm.config.get("temporary_bypass", False):
                b_limit = self.dm.config.get("bypass_limit_mb", 0)
                b_start = self.dm.config.get("bypass_start_bytes", 0)
                current_bytes = self.dm.get_current_total_bytes()
                used_bytes = max(0, current_bytes - b_start)
                used_mb = used_bytes / (1024 * 1024)
                self.bypass_limit_row.set_title(f"Bypass Limit (MB) (Used: {used_mb:.1f} MB)")
            else:
                self.bypass_limit_row.set_title("Bypass Limit (MB)")

            any_limit_reached = (exceeded_limit is not None)

            # Bypass only makes sense when the app actually disables data — i.e.
            # Disable-on-exceeded is on. In notify mode nothing is disabled, so
            # there is nothing to bypass: grey it out (and drop any stale on state).
            bypass_applicable = any_limit_reached and self.action_row.get_active()
            self.temp_bypass_row.set_sensitive(bypass_applicable)
            if not bypass_applicable and self.temp_bypass_row.get_active():
                self._sync_switch_state(self.temp_bypass_row, False)

            if any_limit_reached:
                if self.action_row.get_active():
                    if self.temp_bypass_row.get_active():
                        self._update_status_banner(
                            visible=True,
                            title="Mobile Data Bypassed",
                            subtitle=f"Bypass active. Connection restored despite exceeding {exceeded_limit} limit ({used_val:.1f} MB / {limit_val} MB).",
                            icon_name="dialog-warning-symbolic",
                            css_class="warning"
                        )
                    else:
                        self._update_status_banner(
                            visible=True,
                            title="Mobile Data Blocked",
                            subtitle=f"{exceeded_limit} limit exceeded ({used_val:.1f} MB / {limit_val} MB). Mobile data is now blocked.",
                            icon_name="dialog-error-symbolic",
                            css_class="error"
                        )
                else:
                    self._update_status_banner(
                        visible=True,
                        title="Mobile Data Limit Exceeded",
                        subtitle=f"{exceeded_limit} limit exceeded ({used_val:.1f} MB / {limit_val} MB).",
                        icon_name="dialog-warning-symbolic",
                        css_class="warning"
                    )
            else:
                self._update_status_banner(False)
