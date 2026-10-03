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

import subprocess
from tweak_flx1s.utils import logger, run_command, get_device_model, is_package_installed

STAGING_PACKAGES = [
    "furios-apt-config-staging",
    "furios-apt-config-debian-staging",
    "furios-apt-config-sync-staging",
]

DEVICE_STAGING_PACKAGES = {
    "FuriPhoneFLX1": "furios-apt-config-krypton-staging",
}


class PackageManager:
    """Helper class for package management commands."""

    def check_is_staging(self):
        """Checks if the system is on staging environment."""
        for pkg in STAGING_PACKAGES:
            if not self.check_package_installed(pkg):
                return False
        return True

    def staging_packages(self):
        """Return the staging packages this device has.

        Only krypton ships a device named apt config; radon has none, so
        asking for one leaves apt unable to locate the package and the
        switch stops half done. The sync package every device shares is
        what the krypton one pulls in anyway.
        """
        packages = list(STAGING_PACKAGES)
        device_package = DEVICE_STAGING_PACKAGES.get(get_device_model())
        if device_package:
            packages.append(device_package)
        return packages

    def switch_to_staging(self):
        """Returns command to switch to staging repositories."""
        cmd = (
            f"sudo apt install {' '.join(self.staging_packages())} -y && "
            "sudo furios-update-system"
        )
        return self._run_in_terminal(cmd)

    def switch_to_production(self):
        """Returns command to switch to production repositories."""
        cmd = (
            f"sudo apt remove {' '.join(self.staging_packages())} -y && "
            "sudo furios-update-system"
        )
        return self._run_in_terminal(cmd)

    def upgrade_system(self):
        """Returns command to upgrade system."""
        cmd = "sudo furios-update-system"
        return self._run_in_terminal(cmd)

    def install_branchy(self):
        """Returns command to install furios-app-branchy."""
        cmd = "sudo apt install furios-app-branchy -y"
        return self._run_in_terminal(cmd)

    def check_package_installed(self, package_name):
        """Checks if a package is installed."""
        return is_package_installed(package_name)

    def _run_in_terminal(self, command):
        """Helper to format command for execution."""
        return command.replace("sudo ", "")