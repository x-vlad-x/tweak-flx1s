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
from loguru import logger
from tweak_flx1s.utils import run_command, get_device_model
from tweak_flx1s.gui.dialogs import ExecutionDialog, KeyboardSelectionDialog
try:
    from tweak_flx1s.gui.password_dialog import PasswordChangeDialog
except ImportError:
    PasswordChangeDialog = None

from tweak_flx1s.system.package_manager import PackageManager
from tweak_flx1s.system.keyboard import KeyboardManager
from tweak_flx1s.system.telephony import TelephonyManager
from tweak_flx1s.system.bat_mon import BatMonManager
from tweak_flx1s.system.pam import PamManager
from tweak_flx1s.system.debui import DebUiManager
from tweak_flx1s.system.update_manager import UpdateManager
import threading
import datetime


BUILD_DEPS_TWEAK = "git build-essential debhelper dh-python python3-setuptools pybuild-plugin-pyproject python3-gi python3-loguru python3-requests python3-psutil"

class SystemPage(Adw.PreferencesPage):
    """
    Page for system-level settings.
    Includes: Keyboard, Environment, Updates, Security, Bat-Mon, Telephony, Wofi.
    """
    def __init__(self, window, **kwargs):
        self._ignore_short_pass_toggle = False
        super().__init__(title="System", icon_name="emblem-system-symbolic", **kwargs)
        self.window = window
        self.pkg_mgr = PackageManager()
        self.kbd_mgr = KeyboardManager()
        self.telephony_mgr = TelephonyManager()
        self.bat_mgr = BatMonManager()
        self.pam_mgr = PamManager()
        self.debui_mgr = DebUiManager()
        self.update_mgr = UpdateManager()

        def create_update_btn(package_name, repo_url, is_self_update=False):
            btn = Gtk.Button(icon_name="view-refresh-symbolic")
            btn.add_css_class("circular")
            btn.set_valign(Gtk.Align.CENTER)
            btn.set_tooltip_text("Check for updates")
            btn.set_visible(False)
            btn.connect("clicked", lambda b: self._on_check_update_clicked(b, package_name, repo_url, is_self_update))
            return btn

        self.create_update_btn = create_update_btn

        android_group = Adw.PreferencesGroup(title="Android")
        self.add(android_group)
        android_row = Adw.ActionRow(title="Background Activity",
                                    subtitle="Configure battery use for each Android app")
        android_button = Gtk.Button(label="Configure", valign=Gtk.Align.CENTER)
        def open_android(button):
            from tweak_flx1s.gui.android_dialog import AndroidBackgroundDialog
            AndroidBackgroundDialog(self.window).present()
        android_button.connect("clicked", open_android)
        android_row.add_suffix(android_button)
        android_group.add(android_row)

        def run_pkg_cmd(title, cmd):
            try:
                logger.info(f"Starting execution dialog: {title}")
                dlg = ExecutionDialog(self.window, title, cmd, as_root=True)
                dlg.present()
            except Exception as e:
                logger.error(f"Failed to start execution dialog for {title}: {e}")

        kbd_group = Adw.PreferencesGroup(title="Keyboard")
        self.add(kbd_group)

        self.kbd_row = Adw.ActionRow(title="Active Keyboard")
        self.kbd_row.set_title_lines(0)
        self.kbd_row.set_subtitle_lines(0)
        self._update_kbd_subtitle()

        change_kbd_btn = Gtk.Button(label="Change Keyboard")
        change_kbd_btn.set_valign(Gtk.Align.CENTER)
        change_kbd_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_change_keyboard_clicked(b) or False))
        self.kbd_row.add_suffix(change_kbd_btn)
        kbd_group.add(self.kbd_row)

        self.fi_row = Adw.SwitchRow(title="Finnish Layout", subtitle="Install custom Squeekboard layout")
        self.fi_row.set_title_lines(0)
        self.fi_row.set_subtitle_lines(0)
        self.fi_row.set_active(self.kbd_mgr.is_finnish_layout_installed())
        self.fi_row.connect("notify::active", lambda r, p: GLib.idle_add(lambda: self._on_fi_toggled(r, p) or False))
        kbd_group.add(self.fi_row)



        env_group = Adw.PreferencesGroup(title="Environment")
        self.add(env_group)

        self.env_row = Adw.ActionRow(title="Current Environment")
        self.env_row.set_title_lines(0)
        self.env_row.set_subtitle_lines(0)
        env_group.add(self.env_row)

        self.env_btn = Gtk.Button()
        self.env_btn.set_valign(Gtk.Align.CENTER)
        self.env_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_env_clicked(b) or False))
        self.env_row.add_suffix(self.env_btn)
        self._refresh_env_ui()

        upg_group = Adw.PreferencesGroup(title="Updates")
        self.add(upg_group)
        upg_row = Adw.ActionRow(title="System Upgrade")
        upg_row.set_title_lines(0)
        upg_row.set_subtitle_lines(0)
        upg_group.add(upg_row)

        upg_btn = Gtk.Button(label="Upgrade FuriOS")
        upg_btn.add_css_class("suggested-action")
        upg_btn.set_valign(Gtk.Align.CENTER)
        upg_btn.connect("clicked", lambda x: GLib.idle_add(lambda: run_pkg_cmd("Upgrading System", self.pkg_mgr.upgrade_system()) or False))
        upg_row.add_suffix(upg_btn)

        app_group = Adw.PreferencesGroup(title="Applications")
        self.add(app_group)

        sq_row = Adw.ActionRow(title="Squeekboard", subtitle="On-screen keyboard")
        sq_row.set_title_lines(0)
        sq_row.set_subtitle_lines(0)
        app_group.add(sq_row)

        self.sq_btn = Gtk.Button()
        self.sq_btn.set_valign(Gtk.Align.CENTER)
        self.sq_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_sq_clicked(b) or False))
        sq_row.add_suffix(self.sq_btn)
        self._refresh_squeekboard_ui()

        bat_row = Adw.ActionRow(title="FLX1s-Bat-Mon", subtitle="Install custom battery monitor")
        bat_row.set_title_lines(0)
        bat_row.set_subtitle_lines(0)
        app_group.add(bat_row)

        self.bat_update_btn = self.create_update_btn("flx1s-bat-mon", "https://gitlab.com/Alaraajavamma/flx1s-bat-mon")
        bat_row.add_suffix(self.bat_update_btn)

        self.bat_btn = Gtk.Button(label="Install")
        self.bat_btn.set_valign(Gtk.Align.CENTER)
        self.bat_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_bat_mon_clicked(b) or False))
        bat_row.add_suffix(self.bat_btn)
        self._refresh_bat_mon()

        self.telephony_row = Adw.ActionRow(title="Telephony", subtitle="Alternative Phone and Messages App")
        self.telephony_row.set_title_lines(0)
        self.telephony_row.set_subtitle_lines(0)
        app_group.add(self.telephony_row)

        self.telephony_update_btn = self.create_update_btn("furios-app-telephony", "https://gitlab.com/Alaraajavamma/telephony")
        self.telephony_row.add_suffix(self.telephony_update_btn)

        self.telephony_btn = Gtk.Button(valign=Gtk.Align.CENTER)
        self.telephony_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_telephony_clicked(b) or False))
        self.telephony_row.add_suffix(self.telephony_btn)
        self._refresh_telephony()

        branchy_row = Adw.ActionRow(title="Branchy App Store")
        branchy_row.set_title_lines(0)
        branchy_row.set_subtitle_lines(0)
        app_group.add(branchy_row)

        self.branchy_btn = Gtk.Button(label="Install")
        self.branchy_btn.set_valign(Gtk.Align.CENTER)
        self.branchy_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_branchy_clicked(b) or False))
        branchy_row.add_suffix(self.branchy_btn)
        self._refresh_branchy()

        deb_row = Adw.ActionRow(title="DebUI", subtitle="Debian Package Installer UI")
        deb_row.set_title_lines(0)
        deb_row.set_subtitle_lines(0)
        app_group.add(deb_row)

        self.deb_update_btn = self.create_update_btn("debui", "https://gitlab.com/Alaraajavamma/debui")
        deb_row.add_suffix(self.deb_update_btn)

        self.deb_btn = Gtk.Button()
        self.deb_btn.set_valign(Gtk.Align.CENTER)
        self.deb_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_debui_clicked(b) or False))
        deb_row.add_suffix(self.deb_btn)
        self._refresh_debui()

        tweak_row = Adw.ActionRow(title="Tweak-FLX1s", subtitle="System Tweak Tool")
        tweak_row.set_title_lines(0)
        tweak_row.set_subtitle_lines(0)
        app_group.add(tweak_row)

        self.tweak_update_btn = self.create_update_btn("tweak-flx1s", "https://gitlab.com/Alaraajavamma/tweak-flx1s", is_self_update=True)
        self.tweak_update_btn.set_visible(True)
        tweak_row.add_suffix(self.tweak_update_btn)

        sec_group = Adw.PreferencesGroup(title="Security")
        self.add(sec_group)

        self.short_pass_row = Adw.SwitchRow(title="Enable Shorter Passcodes", subtitle="Allow password shorter than 6 digits")
        self.short_pass_row.set_title_lines(0)
        self.short_pass_row.set_subtitle_lines(0)
        self.short_pass_row.set_active(self.pam_mgr.check_short_passwords_enabled())
        self.short_pass_row.connect("notify::active", lambda r, p: GLib.idle_add(lambda: self._on_short_pass_toggled(r, p) or False))
        sec_group.add(self.short_pass_row)

        self.change_pass_row = Adw.ActionRow(title="Change Password", subtitle="Change current user password")
        self.change_pass_row.set_title_lines(0)
        self.change_pass_row.set_subtitle_lines(0)

        self.change_pass_row.set_sensitive(self.short_pass_row.get_active())

        change_pass_btn = Gtk.Button(label="Change")
        change_pass_btn.set_valign(Gtk.Align.CENTER)
        change_pass_btn.connect("clicked", lambda x: GLib.idle_add(lambda: self._on_change_password_clicked() or False))
        self.change_pass_row.add_suffix(change_pass_btn)
        sec_group.add(self.change_pass_row)

        if get_device_model() == "FuriPhoneFLX1":
            self.fp_row = Adw.ActionRow(title="Fingerprint Authentication", subtitle="Configure PAM for fingerprint support")
            self.fp_row.set_title_lines(0)
            self.fp_row.set_subtitle_lines(0)
            sec_group.add(self.fp_row)

            self.fp_btn = Gtk.Button(label="Enable")
            self.fp_btn.set_valign(Gtk.Align.CENTER)
            self.fp_btn.connect("clicked", lambda x: GLib.idle_add(lambda: self._on_fp_clicked() or False))
            self.fp_row.add_suffix(self.fp_btn)
            self._refresh_fp_ui()

    def _update_kbd_subtitle(self):
        try:
            current = self.kbd_mgr.get_current_keyboard()
            name = current
            for opt in self.kbd_mgr.get_available_keyboards():
                 if opt["path"] == current:
                     name = opt["name"]
                     break
            self.kbd_row.set_subtitle(name)
        except Exception as e:
            logger.error(f"Failed to update keyboard subtitle: {e}")

    def _on_change_keyboard_clicked(self, btn):
        try:
            logger.info("Opening keyboard selection dialog")
            options = self.kbd_mgr.get_available_keyboards()

            def on_select(path):
                cmd = self.kbd_mgr.set_keyboard(path)

                def on_finish(success):
                    if success:
                        self._update_kbd_subtitle()
                        self.kbd_mgr.configure_osk_service(path)
                        restart_cmd = "systemctl --user daemon-reload && systemctl --user restart mobi.phosh.OSK.service && systemctl --user status mobi.phosh.OSK.service --no-pager"
                        try:
                            logger.info("Restarting keyboard service")
                            dlg = ExecutionDialog(self.window, "Restarting Keyboard Service", restart_cmd, as_root=False)
                            dlg.present()
                        except Exception as e:
                             logger.error(f"Failed to start restart dialog: {e}")

                if cmd:
                    dlg = ExecutionDialog(self.window, "Changing Keyboard", cmd, as_root=True, on_finish=on_finish)
                    dlg.present()

            dlg = KeyboardSelectionDialog(self.window, options, on_select)
            dlg.present()
        except Exception as e:
            logger.error(f"Failed to handle keyboard change click: {e}")

    def _on_fi_toggled(self, row, param):
        try:
            active = row.get_active()
            logger.info(f"Toggling Finnish layout: {active}")
            if active:
                if not self.kbd_mgr.install_finnish_layout():
                     row.set_active(False)
            else:
                self.kbd_mgr.remove_finnish_layout()
        except Exception as e:
             logger.error(f"Failed to toggle Finnish layout: {e}")



    def _refresh_squeekboard_ui(self):
        try:
            self.kbd_mgr.drop_orphan_override()
            installed = self.kbd_mgr.check_squeekboard_installed()
            if installed:
                self.sq_btn.set_label("Remove")
                self.sq_btn.add_css_class("destructive-action")
                self.sq_btn.remove_css_class("suggested-action")
                self.kbd_row.set_sensitive(True)
                self.fi_row.set_sensitive(True)
            else:
                self.sq_btn.set_label("Install")
                self.sq_btn.add_css_class("suggested-action")
                self.sq_btn.remove_css_class("destructive-action")
                self.kbd_row.set_sensitive(False)
                self.fi_row.set_sensitive(False)
        except Exception as e:
             logger.error(f"Failed to refresh squeekboard ui: {e}")

    def _on_sq_clicked(self, btn):
        try:
            installed = self.kbd_mgr.check_squeekboard_installed()
            if installed:
                logger.info("Removing Squeekboard")
                cmd = self.kbd_mgr.get_remove_cmd()

                def on_finish(success):
                    if success:
                        self.kbd_mgr.configure_osk_service("stevia")
                    self._refresh_squeekboard_ui()

                dlg = ExecutionDialog(self.window, "Removing Squeekboard", cmd, as_root=True, on_finish=on_finish)
                dlg.present()
            else:
                logger.info("Installing Squeekboard")
                cmd = self.kbd_mgr.get_install_cmd()
                dlg = ExecutionDialog(self.window, "Installing Squeekboard", cmd, as_root=True, on_finish=lambda s: self._refresh_squeekboard_ui())
                dlg.present()
        except Exception as e:
             logger.error(f"Failed to handle Squeekboard click: {e}")

    def _on_short_pass_toggled(self, row, param):
        if self._ignore_short_pass_toggle:
            return

        active = row.get_active()
        self.change_pass_row.set_sensitive(active)

        if active:

            dlg = Adw.MessageDialog(
                transient_for=self.window,
                heading="Shorter Passwords",
                body="How many digits is the minimum we would like to have?"
            )
            dlg.add_response("cancel", "Cancel")
            for i in range(1, 6):
                dlg.add_response(str(i), str(i))

            def on_response(d, r):
                if r == "cancel":
                    self._ignore_short_pass_toggle = True
                    row.set_active(False)
                    self._ignore_short_pass_toggle = False
                else:
                    try:
                        length = int(r)
                        cmd = f"python3 -c \"from tweak_flx1s.system.pam import PamManager; print(PamManager().enable_short_passwords({length}))\""
                        exec_dlg = ExecutionDialog(self.window, "Enabling Shorter Passwords", cmd, as_root=True)
                        exec_dlg.present()
                    except ValueError:
                        self._ignore_short_pass_toggle = True
                        row.set_active(False)
                        self._ignore_short_pass_toggle = False
                d.close()

            dlg.connect("response", on_response)
            dlg.present()
        else:
            cmd = f"python3 -c \"from tweak_flx1s.system.pam import PamManager; print(PamManager().disable_short_passwords())\""
            dlg = ExecutionDialog(self.window, "Disabling Shorter Passwords", cmd, as_root=True)
            dlg.present()

    def _on_change_password_clicked(self):
        if PasswordChangeDialog:
            dlg = PasswordChangeDialog(self.window)
            dlg.present()
        else:
            logger.error("PasswordChangeDialog not available (ImportError?)")

    def _refresh_debui(self):
        try:
            installed = self.debui_mgr.check_installed()
            if installed:
                self.deb_btn.set_label("Remove")
                self.deb_btn.add_css_class("destructive-action")
                self.deb_btn.remove_css_class("suggested-action")
                self.deb_update_btn.set_visible(True)
            else:
                self.deb_btn.set_label("Install")
                self.deb_btn.add_css_class("suggested-action")
                self.deb_btn.remove_css_class("destructive-action")
                self.deb_update_btn.set_visible(False)
        except Exception as e:
            logger.error(f"Failed to refresh DebUI: {e}")

    def _on_debui_clicked(self, btn):
        try:
            installed = self.debui_mgr.check_installed()
            if installed:
                logger.info("Removing DebUI")
                cmd = self.debui_mgr.get_remove_cmd()
                dlg = ExecutionDialog(self.window, "Removing DebUI", cmd, as_root=True, on_finish=lambda s: self._refresh_debui())
                dlg.present()
            else:
                logger.info("Installing DebUI")
                self._perform_update("debui", "https://gitlab.com/Alaraajavamma/debui", False)
        except Exception as e:
             logger.error(f"Failed to handle DebUI click: {e}")

    def _refresh_bat_mon(self):
        try:
            installed = self.bat_mgr.check_installed()
            if installed:
                self.bat_btn.set_label("Remove")
                self.bat_btn.add_css_class("destructive-action")
                self.bat_btn.remove_css_class("suggested-action")
                self.bat_update_btn.set_visible(True)
            else:
                self.bat_btn.set_label("Install")
                self.bat_btn.add_css_class("suggested-action")
                self.bat_btn.remove_css_class("destructive-action")
                self.bat_update_btn.set_visible(False)
        except Exception as e:
            logger.error(f"Failed to refresh Bat Mon status: {e}")

    def _on_bat_mon_clicked(self, btn):
        try:
            installed = self.bat_mgr.check_installed()
            if installed:
                logger.info("Removing FLX1s-Bat-Mon")
                cmd = self.bat_mgr.get_remove_cmd()
                dlg = ExecutionDialog(self.window, "Removing FLX1s-Bat-Mon", cmd, as_root=True, on_finish=lambda s: self._refresh_bat_mon())
                dlg.present()
            else:
                logger.info("Installing FLX1s-Bat-Mon")
                self._perform_update("flx1s-bat-mon", "https://gitlab.com/Alaraajavamma/flx1s-bat-mon", False)
        except Exception as e:
            logger.error(f"Failed to handle bat mon click: {e}")

    def _refresh_fp_ui(self):
        try:
            enabled = self.pam_mgr.check_fingerprint_status()
            if enabled:
                self.fp_btn.set_label("Remove")
                self.fp_btn.add_css_class("destructive-action")
                self.fp_btn.remove_css_class("suggested-action")
                self.fp_row.set_subtitle("Fingerprint is enabled")
            else:
                self.fp_btn.set_label("Enable")
                self.fp_btn.add_css_class("suggested-action")
                self.fp_btn.remove_css_class("destructive-action")
                self.fp_row.set_subtitle("Configure PAM for fingerprint support")
        except Exception as e:
            logger.error(f"Failed to refresh FP UI: {e}")

    def _on_fp_clicked(self):
        try:
            enabled = self.pam_mgr.check_fingerprint_status()
            if enabled:
                 cmd = "python3 -c \"from tweak_flx1s.system.pam import PamManager; print(PamManager().remove_fingerprint_configuration())\""
                 title = "Removing Fingerprint"
            else:
                 cmd = "python3 -c \"from tweak_flx1s.system.pam import PamManager; print(PamManager().configure_fingerprint())\""
                 title = "Configuring Fingerprint"

            def on_finish(success):
                if success: self._refresh_fp_ui()

            dlg = ExecutionDialog(self.window, title, cmd, as_root=True, on_finish=on_finish)
            dlg.present()
        except Exception as e:
            logger.error(f"Failed to handle FP click: {e}")

    def _refresh_telephony(self):
        try:
            installed = self.telephony_mgr.check_installed()
            if installed:
                self.telephony_btn.set_label("Remove")
                self.telephony_btn.add_css_class("destructive-action")
                self.telephony_btn.remove_css_class("suggested-action")
                self.telephony_row.set_subtitle("Installed")
                self.telephony_update_btn.set_visible(True)
            else:
                self.telephony_btn.set_label("Install")
                self.telephony_btn.add_css_class("suggested-action")
                self.telephony_btn.remove_css_class("destructive-action")
                self.telephony_row.set_subtitle("Alternative Phone and Messages App")
                self.telephony_update_btn.set_visible(False)
        except Exception as e:
            logger.error(f"Failed to refresh Telephony status: {e}")

    def _on_telephony_clicked(self, btn):
        try:
            installed = self.telephony_mgr.check_installed()
            if installed:
                logger.info("Removing Telephony")
                cmd = self.telephony_mgr.get_uninstall_root_cmd()
                def on_finish(success):
                    if success:
                        try:
                            self.telephony_mgr.finish_uninstall()
                        except Exception as e:
                            logger.error(f"Finish uninstall failed: {e}")
                        self._refresh_telephony()
                dlg = ExecutionDialog(self.window, "Removing Telephony", cmd, as_root=True, on_finish=on_finish)
                dlg.present()
            else:
                logger.info("Installing Telephony")
                try:
                    self._perform_update("furios-app-telephony", "https://gitlab.com/Alaraajavamma/telephony", False)
                except Exception as e:
                    logger.error(f"Install setup failed: {e}")
        except Exception as e:
            logger.error(f"Failed to handle Telephony click: {e}")

    def _refresh_branchy(self):
        try:
            installed = self.pkg_mgr.check_package_installed("furios-app-branchy")
            if installed:
                self.branchy_btn.set_label("Remove")
                self.branchy_btn.add_css_class("destructive-action")
                self.branchy_btn.remove_css_class("suggested-action")
            else:
                self.branchy_btn.set_label("Install")
                self.branchy_btn.add_css_class("suggested-action")
                self.branchy_btn.remove_css_class("destructive-action")
        except Exception as e:
            logger.error(f"Failed to refresh Branchy status: {e}")

    def _on_branchy_clicked(self, btn):
        try:
            installed = self.pkg_mgr.check_package_installed("furios-app-branchy")
            if installed:
                logger.info("Removing Branchy")
                cmd = "apt remove -y furios-app-branchy"
                dlg = ExecutionDialog(self.window, "Removing Branchy", cmd, as_root=True, on_finish=lambda s: self._refresh_branchy())
                dlg.present()
            else:
                logger.info("Installing Branchy")
                cmd = self.pkg_mgr.install_branchy()
                dlg = ExecutionDialog(self.window, "Installing Branchy", cmd, as_root=True, on_finish=lambda s: self._refresh_branchy())
                dlg.present()
        except Exception as e:
            logger.error(f"Failed to handle Branchy click: {e}")

    def _refresh_env_ui(self):
        try:
            is_staging = self.pkg_mgr.check_is_staging()
            if is_staging:
                self.env_row.set_subtitle("Staging")
                self.env_btn.set_label("Switch to Production")
                self.env_btn.add_css_class("suggested-action")
                self.env_btn.remove_css_class("destructive-action")
            else:
                self.env_row.set_subtitle("Production")
                self.env_btn.set_label("Switch to Staging")
                self.env_btn.add_css_class("destructive-action")
                self.env_btn.remove_css_class("suggested-action")
        except Exception as e:
             logger.error(f"Failed to refresh environment UI: {e}")

    def _on_env_clicked(self, btn):
        try:
            is_staging = self.pkg_mgr.check_is_staging()
            if is_staging:
                logger.info("Switching to Production")
                cmd = self.pkg_mgr.switch_to_production()
                title = "Switching to Production"
            else:
                logger.info("Switching to Staging")
                cmd = self.pkg_mgr.switch_to_staging()
                title = "Switching to Staging"

            dlg = ExecutionDialog(self.window, title, cmd, as_root=True, on_finish=lambda s: self._refresh_env_ui())
            dlg.present()
        except Exception as e:
            logger.error(f"Failed to handle environment switch: {e}")

    def _on_check_update_clicked(self, btn, package_name, repo_url, is_self_update):
        btn.set_sensitive(False)

        def run_check():
            try:
                has_update, local_ts, remote_ts, commit_id = self.update_mgr.check_for_updates(package_name, repo_url)
                GLib.idle_add(lambda: self._on_update_result(btn, has_update, local_ts, remote_ts, commit_id, package_name, repo_url, is_self_update))
            except Exception as e:
                logger.error(f"Update check failed: {e}")
                message = str(e)
                GLib.idle_add(lambda: self._on_update_error(btn, message))

        threading.Thread(target=run_check, daemon=True).start()

    def _on_update_result(self, btn, has_update, local_ts, remote_ts, commit_id, package_name, repo_url, is_self_update):
        btn.set_sensitive(True)
        if has_update:
            msg = "An update is available for {0}.\nLocal version date: {1}\nRemote version date: {2}\n\nDo you want to proceed?".format(
                package_name,
                datetime.datetime.fromtimestamp(local_ts).strftime('%Y-%m-%d %H:%M'),
                datetime.datetime.fromtimestamp(remote_ts).strftime('%Y-%m-%d %H:%M')
            )

            dlg = Adw.MessageDialog(
                transient_for=self.window,
                heading="Update Available",
                body=msg
            )
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("update", "Update")
            dlg.set_response_appearance("update", Adw.ResponseAppearance.SUGGESTED)

            def on_response(d, r):
                if r == "update":
                    self._perform_update(package_name, repo_url, is_self_update)
                d.close()

            dlg.connect("response", on_response)
            dlg.present()
        else:
            dlg = Adw.MessageDialog(
                transient_for=self.window,
                heading="Up to Date",
                body="You are running the latest version of {0}.".format(package_name)
            )
            dlg.add_response("ok", "OK")
            dlg.present()

    def _on_update_error(self, btn, error_msg):
        btn.set_sensitive(True)
        dlg = Adw.MessageDialog(
            transient_for=self.window,
            heading="Update Check Failed",
            body=error_msg
        )
        dlg.add_response("close", "Close")
        dlg.present()

    def _perform_update(self, package_name, repo_url, is_self_update):
        deps = ""
        post_root = ""
        post_user = ""
        pre_user = ""
        pre_build = ""

        if package_name == "furios-app-telephony":
             deps = self.telephony_mgr.get_build_deps()
             post_root = self.telephony_mgr.get_post_install_root_cmd()
             post_user = self.telephony_mgr.get_post_install_user_cmd()
             pre_user = self.telephony_mgr.get_pre_install_user_cmd()
             pre_build = self.telephony_mgr.get_pre_build_cmd()
             logger.info(f"Telephony install mode: {self.telephony_mgr.active_mode()}")
        elif package_name == "debui":
             deps = self.debui_mgr.get_build_deps()
        elif package_name == "flx1s-bat-mon":
             deps = self.bat_mgr.get_build_deps()
        elif package_name == "tweak-flx1s":
             deps = BUILD_DEPS_TWEAK

        if not deps:
             logger.warning(f"No build dependencies found for {package_name}, using default.")
             deps = "git build-essential debhelper dh-python python3-all python3-setuptools pybuild-plugin-pyproject"

        success = self.update_mgr.launch_updater(package_name, repo_url, deps, is_self_update, post_root, post_user, pre_user, pre_build)

        if success:
             logger.info(f"Updater launched for {package_name}")
        else:
             logger.error("Failed to launch updater")
             self._on_update_error(self.window, "Failed to launch updater script")
