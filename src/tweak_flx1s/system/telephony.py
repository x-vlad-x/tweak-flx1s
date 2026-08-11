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
from tweak_flx1s.utils import logger, run_command, is_package_installed
from tweak_flx1s.system.package_manager import PackageManager

MODE_STAMP = "/var/lib/tweak-flx1s/telephony-mode"
MODE_STAGING = "staging"
MODE_STABLE = "stable"

STOCK_APPS = "gnome-calls chatty gnome-contacts"

DIVERTED_FILES = (
    "/etc/xdg/autostart/sm.puri.Chatty-daemon.desktop",
    "/etc/xdg/autostart/org.gnome.Calls-daemon.desktop",
    "/usr/share/applications/sm.puri.Chatty.desktop",
    "/usr/share/applications/org.gnome.Calls.desktop",
    "/usr/share/applications/org.gnome.Contacts.desktop",
)

CALLS_DBUS_SERVICE = "~/.local/share/dbus-1/services/org.gnome.Calls.service"


class TelephonyManager:
    """Manages Telephony package installation and removal."""

    def __init__(self):
        self.pkg_mgr = PackageManager()

    def check_installed(self):
        """Checks if telephony is installed."""
        return is_package_installed("furios-app-telephony")

    def target_mode(self):
        """Returns the mode this system would install in right now."""
        if self.pkg_mgr.check_is_staging():
            return MODE_STAGING
        return MODE_STABLE

    def installed_mode(self):
        """Returns the mode telephony was actually installed in."""
        try:
            with open(MODE_STAMP) as stamp:
                mode = stamp.read().strip()
            if mode in (MODE_STAGING, MODE_STABLE):
                return mode
            logger.warning(f"Unknown telephony mode stamp: {mode}")
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.error(f"Failed to read telephony mode stamp: {e}")
        return self.target_mode()

    def active_mode(self):
        """Returns the mode to act in: what is installed wins over the current channel."""
        if self.check_installed():
            return self.installed_mode()
        return self.target_mode()

    def get_build_deps(self):
        """Returns build dependencies for Telephony."""
        return "git build-essential debhelper dh-python python3-all python3-setuptools pybuild-plugin-pyproject python3-pytest python3-pyflakes pyflakes3 gettext"

    def get_pre_build_cmd(self):
        """Returns source commands to run before the package is built."""
        if self.active_mode() == MODE_STAGING:
            return ""

        return (
            "python3 -c '"
            "import re; p = \"debian/control\"; s = open(p).read(); "
            "open(p, \"w\").write(re.sub(r\"^Conflicts:.*?(?=^\\S)\", \"\", s, flags=re.S | re.M))"
            "'"
        )

    def get_pre_install_user_cmd(self):
        """Returns user commands to run before installation (stops conflicting services)."""
        if self.active_mode() == MODE_STAGING:
            return ""

        return (
            "systemctl --user stop calls-daemon || true && "
            "systemctl --user mask calls-daemon || true && "
            "systemctl --user stop ofono-toned || true && "
            "systemctl --user disable ofono-toned || true && "
            "systemctl --user mask ofono-toned || true && "
            "pkill -f ofono-toned || true"
        )

    def get_post_install_root_cmd(self):
        """Returns root commands to run after installation (diversions and mode stamp)."""
        mode = self.active_mode()
        steps = []

        if mode == MODE_STABLE:
            for path in DIVERTED_FILES:
                steps.append(
                    f"dpkg-divert --add --rename --divert {path}.disabled {path} || true"
                )

        steps.append(f"install -d {os.path.dirname(MODE_STAMP)} || true")
        steps.append(f"printf '{mode}\\n' > {MODE_STAMP} || true")

        return " && ".join(steps)

    def get_post_install_user_cmd(self):
        """Returns user commands to run after installation (service config)."""
        if self.active_mode() == MODE_STAGING:
            return ""

        return (
            "mkdir -p ~/.local/share/dbus-1/services/ && "
            "echo -e '[D-BUS Service]\\nName=org.gnome.Calls\\nExec=/bin/true' > "
            f"{CALLS_DBUS_SERVICE}"
        )

    def get_uninstall_root_cmd(self):
        """Returns root script for uninstallation, undoing whatever was done at install."""
        mode = self.installed_mode()

        script = """
set -e
echo "Removing package..."
apt remove -y furios-app-telephony || true
"""

        if mode == MODE_STABLE:
            script += '\necho "Removing diversions..."\n'
            for path in DIVERTED_FILES:
                script += f"dpkg-divert --remove --rename {path} || true\n"
        else:
            script += (
                '\necho "Restoring stock apps..."\n'
                "apt update || true\n"
                f"apt install -y {STOCK_APPS} || true\n"
            )

        script += f'\nrm -f {MODE_STAMP} || true\n\necho "Root tasks complete."\n'
        return script

    def finish_uninstall(self):
        """Finalizes uninstallation as user."""
        try:
            logger.info("Finishing uninstall: restoring user services...")
            run_command("systemctl --user unmask calls-daemon", check=False)

            dbus_file = os.path.expanduser(CALLS_DBUS_SERVICE)
            if os.path.exists(dbus_file):
                os.remove(dbus_file)

            run_command("systemctl --user daemon-reload", check=False)

            run_command("systemctl --user unmask ofono-toned", check=False)
            run_command("systemctl --user enable ofono-toned", check=False)
            run_command("systemctl --user start ofono-toned", check=False)
        except Exception as e:
            logger.error(f"Error during finish_uninstall: {e}")
