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

from tweak_flx1s.utils import is_package_installed

class BatMonManager:
    """Manages FLX1s-Bat-Mon package installation and removal."""

    def check_installed(self):
        """Checks if flx1s-bat-mon is installed."""
        return is_package_installed("flx1s-bat-mon")

    def get_build_deps(self):
        """Returns build dependencies for FLX1s-Bat-Mon."""
        return "git build-essential debhelper dh-python python3-all python3-setuptools pybuild-plugin-pyproject"

    def get_remove_cmd(self):
        """Returns command to remove FLX1s-Bat-Mon."""
        return "apt remove -y flx1s-bat-mon || true"