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
import signal
import sys
import shutil
import gi
from gi.repository import GLib, Gio
from loguru import logger
from tweak_flx1s.utils import get_device_model
from tweak_flx1s.actions.gestures import GesturesManager

class GestureMonitor:
    """Monitors gestures using lisgd."""
    def __init__(self):
        self.device = os.environ.get("LISGD_INPUT_DEVICE")
        if not self.device:
            model = get_device_model()
            if model == "FuriPhoneFLX1s":
                self.device = "/dev/input/event3"
            else:
                self.device = "/dev/input/event2"
            logger.warning(f"LISGD_INPUT_DEVICE not set, fell back to detection: {self.device}")

        self.subprocess = None
        self.manager = GesturesManager()
        self.loop = GLib.MainLoop()
        self.cancellable = Gio.Cancellable()
        self.lisgd_failed = False

    def start(self):
        """Starts the lisgd process with configured gestures.

        Exits nonzero when lisgd failed, so systemd's Restart=on-failure
        actually restarts us: at boot this service can win the race
        against the session environment import, and lisgd dies for the
        missing WAYLAND_DISPLAY; a retry five seconds later finds the
        session up. Exiting 0 on that path silently disabled gestures
        for the whole session.
        """
        if not self.manager.config.get("enabled", False):
            logger.info("Gestures are disabled in config.")
            return

        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._on_quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._on_quit)

        self._start_lisgd()

        try:
            self.loop.run()
        except KeyboardInterrupt:
            self._on_quit()

        if self.lisgd_failed:
            sys.exit(1)

    def _start_lisgd(self):
        logger.info(f"Starting lisgd on {self.device}")

        cmd = ["lisgd", "-d", self.device]

        gestures = self.manager.config.get("gestures", [])
        # Freeze the exact list lisgd gets: triggers resolve indices against
        # this snapshot even if the config is edited while lisgd runs.
        self.manager.write_snapshot(gestures)
        executable = shutil.which("tweak-flx1s") or "tweak-flx1s"

        for idx, gesture in enumerate(gestures):
            spec = gesture.get("spec")
            if not spec:
                logger.warning(f"Skipping gesture {idx} without spec")
                continue

            action_cmd = f"{executable} --trigger-gesture {idx}"
            full_arg = f"{spec},{action_cmd}"
            cmd.extend(["-g", full_arg])

        logger.debug(f"Executing: {' '.join(cmd)}")

        try:
            self.subprocess = Gio.Subprocess.new(
                cmd,
                Gio.SubprocessFlags.NONE
            )

            self.subprocess.wait_check_async(self.cancellable, self._on_subprocess_exit)

        except Exception as e:
            logger.error(f"Error running lisgd: {e}")
            self._on_quit()

    def _on_subprocess_exit(self, source, result):
        try:
            source.wait_check_finish(result)
            logger.info("lisgd exited normally.")
        except GLib.Error as e:
            if e.code != Gio.IOErrorEnum.CANCELLED:
                 logger.error(f"lisgd exited with error: {e}")
                 self.lisgd_failed = True
        finally:
            self._on_quit()

    def _on_quit(self):
        """Stops the lisgd process and quits the loop."""
        logger.info("Stopping gestures monitor...")
        self.cancellable.cancel()

        if self.subprocess:
            logger.info("Terminating lisgd...")
            self.subprocess.force_exit()

        if self.loop.is_running():
            self.loop.quit()
        return GLib.SOURCE_REMOVE

def run():
    monitor = GestureMonitor()
    monitor.start()

if __name__ == "__main__":
    from tweak_flx1s.utils import setup_logging
    setup_logging()
    run()