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
import os
import pty
import select
import subprocess
import threading
import time
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
from loguru import logger


class PasswordChangeDialog(Adw.Window):
    """Dialog to change the user's password."""
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True, title="Change Password")
        self.set_default_size(350, 450)

        content = Adw.ToolbarView()
        self.set_content(content)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        content.add_top_bar(header)

        self.change_btn = Gtk.Button(label="Change")
        self.change_btn.add_css_class("suggested-action")
        self.change_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_change_clicked(b) or False))
        header.pack_start(self.change_btn)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda x: GLib.idle_add(lambda: self.close() or False))
        header.pack_end(close_btn)

        page = Adw.PreferencesPage()
        content.set_content(page)

        group = Adw.PreferencesGroup(title="Password Details")
        page.add(group)

        self.current_entry = Adw.PasswordEntryRow(title="Current Password")
        group.add(self.current_entry)

        self.new_entry = Adw.PasswordEntryRow(title="New Password")
        group.add(self.new_entry)

        self.confirm_entry = Adw.PasswordEntryRow(title="Confirm New Password")
        group.add(self.confirm_entry)

        status_group = Adw.PreferencesGroup()
        page.add(status_group)

        self.status_label = Gtk.Label(label="")
        self.status_label.set_wrap(True)
        self.status_label.set_margin_top(20)
        self.status_label.set_margin_bottom(20)
        self.status_label.set_margin_start(20)
        self.status_label.set_margin_end(20)
        status_group.add(self.status_label)

    def _on_change_clicked(self, btn):
        current = self.current_entry.get_text()
        new_pass = self.new_entry.get_text()
        confirm = self.confirm_entry.get_text()

        if not current:
            self.status_label.set_text("Please enter current password.")
            return
        if not new_pass:
            self.status_label.set_text("Please enter new password.")
            return
        if new_pass != confirm:
            self.status_label.set_text("New passwords do not match.")
            return

        self.status_label.set_text("Changing password...")
        self.change_btn.set_sensitive(False)
        self.current_entry.set_sensitive(False)
        self.new_entry.set_sensitive(False)
        self.confirm_entry.set_sensitive(False)

        threading.Thread(target=self._run_passwd, args=(current, new_pass), daemon=True).start()

    def _run_passwd(self, current, new_pass):
        master_fd, slave_fd = pty.openpty()

        try:
            env = os.environ.copy()
            env["LANG"] = "C"
            env["LC_ALL"] = "C"

            p = subprocess.Popen(
                ["passwd"],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                text=True,
                env=env
            )
            os.close(slave_fd)

            output_log = ""
            stage = 0

            while p.poll() is None:
                r, w, x = select.select([master_fd], [], [], 1.0)
                if master_fd in r:
                    try:
                        data = os.read(master_fd, 1024).decode('utf-8', errors='ignore')
                        output_log += data

                        if stage == 0 and ("current" in data.lower() or "password:" in data.lower()):
                            logger.debug("Sending current password")
                            os.write(master_fd, (current + "\n").encode())
                            stage = 1
                            time.sleep(0.5)
                        elif stage == 1 and ("new" in data.lower() or "password:" in data.lower()):
                            logger.debug("Sending new password")
                            os.write(master_fd, (new_pass + "\n").encode())
                            stage = 2
                            time.sleep(0.5)
                        elif stage == 2 and ("retype" in data.lower() or "password:" in data.lower()):
                            logger.debug("Sending confirmation")
                            os.write(master_fd, (new_pass + "\n").encode())
                            stage = 3
                    except OSError:
                        break

            try:
                while True:
                    r, _, _ = select.select([master_fd], [], [], 0.1)
                    if not r: break
                    data = os.read(master_fd, 1024).decode('utf-8', errors='ignore')
                    output_log += data
            except OSError:
                pass

            os.close(master_fd)
            p.wait()
            rc = p.returncode

            GLib.idle_add(self._on_finished, rc, output_log)

        except Exception as e:
            logger.error(f"passwd failed: {e}")
            GLib.idle_add(self._on_finished, -1, str(e))

    def _on_finished(self, rc, output):
        self.change_btn.set_sensitive(True)
        self.current_entry.set_sensitive(True)
        self.new_entry.set_sensitive(True)
        self.confirm_entry.set_sensitive(True)

        if rc == 0:
            self.status_label.set_text("Password changed successfully!")
            self.current_entry.set_text("")
            self.new_entry.set_text("")
            self.confirm_entry.set_text("")
            GLib.timeout_add(1500, lambda: self.close() or False)
        else:
            msg = "Failed to change password."
            if "authentication token manipulation error" in output.lower():
                msg += " " + "Authentication error. Check current password."
            elif "bad password" in output.lower():
                msg += " " + "Bad password."
                lines = output.splitlines()
                for line in lines:
                    if "BAD PASSWORD" in line:
                        reason = line.split("BAD PASSWORD: ")[-1].strip()
                        if "too short" in reason:
                            reason = "it is too short"
                        elif "same as the old one" in reason:
                            reason = "is the same as the old one"
                        elif "palindrome" in reason:
                            reason = "is a palindrome"
                        elif "case changes only" in reason:
                            reason = "case changes only"
                        elif "too similar" in reason:
                            reason = "is too similar to the old one"
                        elif "does not contain enough DIFFERENT characters" in reason:
                            reason = "it does not contain enough DIFFERENT characters"
                        elif "dictionary word" in reason:
                            reason = "it is based on a dictionary word"

                        msg = "Bad password: " + reason

            self.status_label.set_text(msg)
