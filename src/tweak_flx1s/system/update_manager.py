# Copyright (C) 2026 alaraajavamma aki@urheiluaki.fi
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import os
import requests
import datetime
import subprocess
import sys
from tweak_flx1s.utils import logger, run_command

UPDATER_SCRIPT_TEMPLATE = r"""#!/usr/bin/env python3
import sys
import os
import subprocess
import signal
import time
import shutil
import getpass
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Pango

class UpdaterApp(Adw.Application):
    def __init__(self, old_pid, package_name, repo_url, build_deps):
        super().__init__(application_id="com.tweak.updater", flags=0)
        self.old_pid = int(old_pid)
        self.package_name = package_name
        self.repo_url = repo_url
        self.build_deps = build_deps
        self.post_root_cmd = os.environ.get("POST_ROOT_CMD", "")
        self.post_user_cmd = os.environ.get("POST_USER_CMD", "")
        self.pre_user_cmd = os.environ.get("PRE_USER_CMD", "")
        self.pre_build_cmd = os.environ.get("PRE_BUILD_CMD", "")
        self.user = getpass.getuser()
        self.timestamp = int(time.time())
        self.work_dir = f"/tmp/tweak_update_{self.timestamp}"
        self.source_dir = os.path.join(self.work_dir, "source")
        self.process = None

    def do_activate(self):
        self.win = Adw.ApplicationWindow(application=self, title="Updating " + self.package_name)
        self.win.set_default_size(600, 400)

        content = Adw.ToolbarView()
        self.win.set_content(content)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        header.set_show_start_title_buttons(False)
        content.add_top_bar(header)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main_box.set_margin_top(24)
        main_box.set_margin_bottom(24)
        main_box.set_margin_start(24)
        main_box.set_margin_end(24)

        content.set_content(main_box)

        self.status_label = Gtk.Label(label="Initializing update...")
        self.status_label.add_css_class("title-2")
        main_box.append(self.status_label)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(32, 32)
        self.spinner.start()
        main_box.append(self.spinner)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_has_frame(True)

        self.textview = Gtk.TextView()
        self.textview.set_editable(False)
        self.textview.set_monospace(True)
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview.set_bottom_margin(10)
        self.textview.set_top_margin(10)
        self.textview.set_left_margin(10)
        self.textview.set_right_margin(10)

        scrolled.set_child(self.textview)
        main_box.append(scrolled)

        self.buffer = self.textview.get_buffer()

        self.action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.action_box.set_halign(Gtk.Align.CENTER)
        main_box.append(self.action_box)

        self.win.present()

        import threading
        threading.Thread(target=self._run_update_process, daemon=True).start()

    def append_log(self, text):
        def _ui_append():
            iter_end = self.buffer.get_end_iter()
            self.buffer.insert(iter_end, text + "\n")
            mark = self.buffer.create_mark(None, iter_end, False)
            self.textview.scroll_to_mark(mark, 0.0, True, 0.0, 1.0)
            return False
        GLib.idle_add(_ui_append)

    def _run_update_process(self):
        try:
            if self.old_pid > 0:
                self.append_log(f"Terminating old process {self.old_pid}...")
                try:
                    os.kill(self.old_pid, signal.SIGTERM)
                    time.sleep(1)
                    try:
                        os.kill(self.old_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                except Exception as e:
                    self.append_log(f"Warning: Failed to kill process: {e}")

            if self.pre_user_cmd:
                 self.append_log("Running pre-install user tasks...")
                 self._run_cmd(["/bin/bash", "-c", 'eval "$PRE_USER_CMD"'])

            self.append_log(f"Cleaning/Creating work dir: {self.work_dir}")
            if os.path.exists(self.work_dir):
                shutil.rmtree(self.work_dir)
            os.makedirs(self.source_dir, exist_ok=True)

            self.append_log(f"Cloning {self.repo_url}...")
            self._run_cmd(["git", "clone", "--depth", "1", self.repo_url, self.source_dir])

            if self.pre_build_cmd:
                self.append_log("Preparing sources...")
                self._run_cmd(["/bin/bash", "-c",
                               f'cd "{self.source_dir}" && eval "$PRE_BUILD_CMD"'])

            self.append_log(f"Starting build and install process...")
            self.append_log(f"Installing dependencies: {self.build_deps}")

            build_cmd = f"cd \"{self.source_dir}\" && dpkg-buildpackage -us -uc"

            # set -e: each phase must fail loudly with its real error instead
            # of falling through to the next one. The old package is never
            # removed first — `apt install --reinstall ./pkg.deb` replaces it
            # in place even at the same version (this project has no version
            # bumps), so a failed install can no longer leave the app
            # uninstalled.
            full_cmd = (
                "set -e\n"
                "apt update\n"
                f"apt install -y {self.build_deps}\n"
                f"apt build-dep -y \"{self.source_dir}\" || true\n"
                f"runuser -u {self.user} -- bash -c '{build_cmd}'\n"
                f"apt install -y --reinstall \"{self.work_dir}\"/*.deb\n"
            )

            if self.post_root_cmd:
                self.append_log(f"Adding post-install root commands...")
                full_cmd += f"{self.post_root_cmd}\n"

            self._run_cmd(["pkexec", "/bin/bash", "-c", full_cmd])

            if self.post_user_cmd:
                 self.append_log("Running post-install user tasks...")
                 self._run_cmd(["/bin/bash", "-c", 'eval "$POST_USER_CMD"'])

            self.append_log("Cleaning up...")
            shutil.rmtree(self.work_dir, ignore_errors=True)

            self.append_log("Update/Install Complete!")
            GLib.idle_add(self._on_success)

        except Exception as e:
            self.append_log(f"\nError during update: {e}")
            GLib.idle_add(self._on_failure)

    def _run_cmd(self, cmd):
        cmd_str = " ".join(cmd)
        self.append_log(f"> {cmd_str}")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )

        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if line:
                self.append_log(line.strip())

        rc = process.poll()
        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)

    def _on_success(self):
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.status_label.set_label("Operation Successful")
        self.status_label.add_css_class("success")

        if self.package_name == "tweak-flx1s":
            btn_restart = Gtk.Button(label="Restart App")
            btn_restart.add_css_class("suggested-action")
            btn_restart.add_css_class("pill")
            btn_restart.connect("clicked", lambda x: self._restart_app())
            self.action_box.append(btn_restart)

        btn_close = Gtk.Button(label="Close")
        btn_close.add_css_class("pill")
        btn_close.connect("clicked", lambda x: self.quit())
        self.action_box.append(btn_close)

    def _on_failure(self):
        self.spinner.stop()
        self.spinner.set_visible(False)
        self.status_label.set_label("Operation Failed")
        self.status_label.add_css_class("error")

        btn_retry = Gtk.Button(label="Try Again")
        btn_retry.add_css_class("pill")
        btn_retry.connect("clicked", lambda x: self._restart_update_step())
        self.action_box.append(btn_retry)

        btn_close = Gtk.Button(label="Close")
        btn_close.add_css_class("destructive-action")
        btn_close.add_css_class("pill")
        btn_close.connect("clicked", lambda x: self.quit())
        self.action_box.append(btn_close)

    def _restart_update_step(self):
        child = self.action_box.get_first_child()
        while child:
            self.action_box.remove(child)
            child = self.action_box.get_first_child()

        self.status_label.set_label("Retrying operation...")
        self.status_label.remove_css_class("error")
        self.spinner.start()
        self.spinner.set_visible(True)

        import threading
        threading.Thread(target=self._run_update_process, daemon=True).start()

    def _restart_app(self):
        try:
            subprocess.Popen(["tweak-flx1s"], start_new_session=True)
            self.quit()
        except Exception as e:
            self.append_log(f"Failed to restart app: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: updater.py <old_pid> <package_name> <repo_url> <build_deps>")
        sys.exit(1)

    old_pid = sys.argv[1]
    package_name = sys.argv[2]
    repo_url = sys.argv[3]
    build_deps = sys.argv[4]

    app = UpdaterApp(old_pid, package_name, repo_url, build_deps)
    app.run(None)

    # Self-delete the temp script (launch_updater writes one per run).
    try:
        os.unlink(os.path.abspath(__file__))
    except OSError:
        pass
"""

class UpdateManager:
    """Manages updates for supported packages by checking GitLab repositories."""

    def get_local_install_time(self, package_name):
        """
        Returns the modification time of the package list file.
        Returns 0 if not installed.
        """
        list_file = f"/var/lib/dpkg/info/{package_name}.list"
        if os.path.exists(list_file):
            return os.path.getmtime(list_file)
        return 0

    def get_latest_commit_info(self, repo_url):
        """
        Queries GitLab API for the latest commit on the main branch.
        Returns (commit_sha, commit_timestamp) or (None, None).
        repo_url example: https://gitlab.com/Alaraajavamma/telephony
        """
        try:
            logger.debug(f"Checking for updates in {repo_url}")
            if "gitlab.com/" not in repo_url:
                logger.warning(f"Invalid GitLab URL: {repo_url}")
                return None, None

            parts = repo_url.split("gitlab.com/")
            if len(parts) < 2:
                return None, None

            project_path = parts[1].strip("/")
            encoded_path = project_path.replace("/", "%2F")

            commits_url = f"https://gitlab.com/api/v4/projects/{encoded_path}/repository/commits"
            params = {
                "per_page": 1
            }
            response = requests.get(commits_url, params=params, timeout=10)
            response.raise_for_status()
            commits = response.json()

            if not commits:
                logger.info(f"No commits found for {repo_url}")
                return None, None

            commit = commits[0]
            committed_date_str = commit["committed_date"]
            commit_id = commit["id"]

            try:
                dt = datetime.datetime.fromisoformat(committed_date_str)
                return commit_id, dt.timestamp()
            except ValueError as e:
                logger.error(f"Failed to parse date {committed_date_str}: {e}")
                return None, None

        except requests.RequestException as e:
            logger.error(f"Network error checking updates for {repo_url}: {e}")
            return None, None
        except Exception as e:
            logger.error(f"Failed to check updates for {repo_url}: {e}")
            return None, None

    def check_for_updates(self, package_name, repo_url):
        """
        Checks if an update is available.
        Returns (has_update, local_ts, remote_ts, commit_id)
        """
        local_ts = self.get_local_install_time(package_name)
        if local_ts == 0:
             return False, 0, 0, None

        commit_id, remote_ts = self.get_latest_commit_info(repo_url)

        if remote_ts and remote_ts > local_ts:
            return True, local_ts, remote_ts, commit_id

        return False, local_ts, remote_ts, commit_id

    def launch_updater(self, package_name, repo_url, build_deps, is_self_update=False, post_root_cmd="", post_user_cmd="", pre_user_cmd="", pre_build_cmd=""):
        """
        Launches the standalone updater script.
        If is_self_update is True, it will pass the current PID to be killed.
        Otherwise it passes 0 as PID.
        """
        import tempfile
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(UPDATER_SCRIPT_TEMPLATE)
                script_path = f.name

            os.chmod(script_path, 0o755)

            current_pid = os.getpid() if is_self_update else 0

            logger.info(f"Launching updater for {package_name} (PID: {current_pid})")

            env = os.environ.copy()
            env["POST_ROOT_CMD"] = post_root_cmd
            env["POST_USER_CMD"] = post_user_cmd
            env["PRE_USER_CMD"] = pre_user_cmd
            env["PRE_BUILD_CMD"] = pre_build_cmd

            subprocess.Popen(
                [sys.executable, script_path, str(current_pid), package_name, repo_url, build_deps],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env
            )

            return True

        except Exception as e:
            logger.error(f"Failed to launch updater: {e}")
            return False