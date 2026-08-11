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

class PackageManager:
    """Helper class for package management commands."""

    def check_is_staging(self):
        """Checks if the system is on staging environment."""
        base_pkgs = ["furios-apt-config-staging", "furios-apt-config-debian-staging"]
        for pkg in base_pkgs:
            if not self.check_package_installed(pkg):
                return False

        has_krypton = self.check_package_installed("furios-apt-config-krypton-staging")
        has_radon = self.check_package_installed("furios-apt-config-radon-staging")

        return has_krypton or has_radon

    def switch_to_staging(self):
        """Returns command to switch to staging repositories."""
        model = get_device_model()
        extra_pkg = "furios-apt-config-krypton-staging"

        if model == "FuriPhoneFLX1s":
             extra_pkg = "furios-apt-config-radon-staging"

        cmd = (
            "sudo apt install furios-apt-config-staging furios-apt-config-debian-staging -y && "
            "sudo apt update && "
            f"sudo apt install {extra_pkg} -y && "
            "sudo furios-update-system"
        )
        return self._run_in_terminal(cmd)

    def switch_to_production(self):
        """Returns command to switch to production repositories."""
        model = get_device_model()
        extra_pkg = "furios-apt-config-krypton-staging"

        if model == "FuriPhoneFLX1s":
             extra_pkg = "furios-apt-config-radon-staging"

        cmd = (
            "sudo apt remove furios-apt-config-staging furios-apt-config-debian-staging "
            f"{extra_pkg} -y && "
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