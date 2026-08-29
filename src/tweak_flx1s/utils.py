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

import sys
import shutil
import subprocess
from loguru import logger
from tweak_flx1s.const import APP_ID

def setup_logging(debug=False):
    """Initializes logging configuration."""
    logger.remove()
    if debug:
        logger.add(sys.stderr, level="DEBUG")
    else:
        logger.add(sys.stderr, level="INFO")

def run_command(command, check=True):
    """
    Runs a shell command and returns the output.
    'command' can be a string (shell=True) or a list (shell=False).
    Returns None when the command fails (with check=False) so callers can
    tell a failure apart from legitimately empty output.
    """
    use_shell = isinstance(command, str)
    cmd_str = command if use_shell else " ".join(command)

    logger.debug(f"Running command: {cmd_str}")
    try:
        result = subprocess.run(
            command,
            shell=use_shell,
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        if result.returncode != 0:
            logger.error(f"Command failed: {cmd_str}")
            logger.error(f"Error output: {result.stderr}")
            return None
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {cmd_str}")
        logger.error(f"Error output: {e.stderr}")
        if check:
            raise
        return None
    except OSError as e:
        # e.g. binary not found with shell=False — same contract as above.
        logger.error(f"Command failed to start: {cmd_str} ({e})")
        if check:
            raise
        return None

def check_dependency(name):
    """Checks if a command line tool exists."""
    return shutil.which(name) is not None

_device_model = None

def get_device_model():
    """
    Returns the device model based on 'uname -n'.
    Expected values: 'FuriPhoneFLX1', 'FuriPhoneFLX1s', or 'Unknown'.
    Cached for the process lifetime — the hostname cannot change under us,
    and several pages call this repeatedly.
    """
    global _device_model
    if _device_model is not None:
        return _device_model
    try:
        model = run_command("uname -n", check=False)
        if model in ["FuriPhoneFLX1", "FuriPhoneFLX1s"]:
            _device_model = model
        else:
            _device_model = "Unknown"
    except Exception as e:
        logger.error(f"Failed to detect device model: {e}")
        return "Unknown"
    return _device_model

def send_notification(title, body="", icon_name="dialog-information", id=None, urgent=False):
    """
    Sends an official app notification via GApplication. The system
    (notification daemon + feedbackd) provides the sound/vibra feedback for
    it according to the active profile — the app does not drive audio itself.
    urgent=True raises the priority so it alerts more insistently.
    """
    try:
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio

        app = Gio.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)

        notification = Gio.Notification.new(title)
        if body:
            notification.set_body(body)
        if urgent:
            notification.set_priority(Gio.NotificationPriority.URGENT)

        # Enforce unified application icon
        icon = Gio.ThemedIcon.new(APP_ID)
        notification.set_icon(icon)

        app.send_notification(id, notification)
        logger.info(f"Notification sent: {title}")
    except Exception as e:
        logger.error(f"Failed to send notification: {e}")


def clear_notification(notification_id):
    """
    Withdraws a notification by ID.
    """
    try:
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio

        app = Gio.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)
        app.withdraw_notification(notification_id)
        logger.info(f"Notification withdrawn: {notification_id}")
    except Exception as e:
        logger.error(f"Failed to withdraw notification: {e}")


# Package-status cache: one dpkg-query per package per app run instead of a
# fresh subprocess for every row refresh / page switch. Invalidated whenever
# an ExecutionDialog finishes (the GUI's install/remove choke point).
_pkg_status_cache = {}

def invalidate_package_cache():
    """Clears cached package states after installs/removals."""
    _pkg_status_cache.clear()

def is_package_installed(package_name):
    """
    Checks if a package is strictly installed (status 'install ok installed').
    Returns True only if the package is fully installed, False otherwise.
    """
    if package_name in _pkg_status_cache:
        return _pkg_status_cache[package_name]
    try:
        cmd = f"dpkg-query -W -f='${{Status}}' {package_name}"
        result = run_command(cmd, check=False)
        installed = bool(result) and result.strip() == "install ok installed"
        _pkg_status_cache[package_name] = installed
        return installed
    except Exception:
        return False


def get_default_sink_volume():
    """Reads the default PulseAudio sink via pulsectl.
    Returns (sink_name, volume, muted) where volume is a float (1.0 == 100%),
    or (None, None, None) if it can't be read."""
    try:
        import pulsectl
        with pulsectl.Pulse('tweak-flx1s') as pulse:
            info = pulse.server_info()
            sink = pulse.get_sink_by_name(info.default_sink_name)
            if sink:
                return sink.name, max(sink.volume.values), bool(sink.mute)
    except Exception as e:
        logger.error(f"Failed to read volume via pulsectl: {e}")
    return None, None, None


def set_sink_volume(sink_name, volume, muted):
    """Sets a PulseAudio sink's volume (float, 1.0 == 100%) and mute state."""
    if not sink_name:
        return
    try:
        import pulsectl
        with pulsectl.Pulse('tweak-flx1s') as pulse:
            sink = pulse.get_sink_by_name(sink_name)
            if sink:
                pulse.sink_mute(sink.index, bool(muted))
                pulse.volume_set_all_chans(sink, float(volume))
    except Exception as e:
        logger.error(f"Failed to set volume via pulsectl: {e}")