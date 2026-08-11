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
import gi
from gi.repository import GLib, Gio
from tweak_flx1s.utils import logger, run_command, get_default_sink_volume, set_sink_volume

# How long after the alarm fires the original volume is put back.
VOLUME_RESTORE_DELAY_S = 120

class AlarmMonitor:
    """Monitors alarm events to ensure wake-up."""
    def __init__(self):
        self.loop = GLib.MainLoop()
        self.subprocess = None
        self.cancellable = Gio.Cancellable()
        self.data_input_stream = None
        self.lines_to_check = 0
        self.saved_volume = None  # (sink_name, volume, muted) captured before forcing max
        self.restore_id = 0

    def start(self):
        """Starts the monitoring process."""
        logger.info("Starting Alarm Monitor")

        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._on_quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._on_quit)

        self._start_monitor_subprocess()

        try:
            self.loop.run()
        except KeyboardInterrupt:
            self._on_quit()

    def _on_quit(self):
        """Handles termination signals."""
        logger.info("Stopping Alarm Monitor...")
        self.cancellable.cancel()

        if self.subprocess:
            self.subprocess.force_exit()

        if self.loop.is_running():
            self.loop.quit()
        return GLib.SOURCE_REMOVE

    def _start_monitor_subprocess(self):
        """
        Runs dbus-monitor to intercept TriggerFeedback method calls.
        This is required because we need to see method calls destined for other services.
        """
        # arg0 filter: only wake for GNOME Clocks' feedback calls — without it
        # every keyboard tap / notification haptic on the phone wakes both
        # dbus-monitor and this daemon.
        cmd = ["dbus-monitor", "type='method_call',interface='org.sigxcpu.Feedback',member='TriggerFeedback',arg0='org.gnome.clocks'"]
        logger.debug(f"Running: {' '.join(cmd)}")

        try:
            self.subprocess = Gio.Subprocess.new(
                cmd,
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
            )

            stdout_pipe = self.subprocess.get_stdout_pipe()
            self.data_input_stream = Gio.DataInputStream.new(stdout_pipe)

            self._read_line()

        except Exception as e:
            logger.error(f"Failed to start dbus-monitor: {e}")
            self._on_quit()

    def _read_line(self):
        """Reads a line asynchronously from the subprocess stdout."""
        self.data_input_stream.read_line_async(
            GLib.PRIORITY_DEFAULT,
            self.cancellable,
            self._on_line_read
        )

    def _on_line_read(self, source, result):
        """Callback when a line is read."""
        try:
            line_bytes, length = source.read_line_finish(result)
            if line_bytes is None:
                logger.warning("dbus-monitor stream ended")
                self._on_quit()
                return

            # errors="replace": a stray non-UTF-8 byte must not kill the
            # read loop (only GLib.Error is caught below).
            line = line_bytes.decode('utf-8', errors='replace').strip()
            self._process_line(line)
            self._read_line()

        except  GLib.Error as e:
            if e.code != Gio.IOErrorEnum.CANCELLED:
                 logger.error(f"Error reading line: {e}")
                 self._on_quit()

    def _process_line(self, line):
        """Processes a single line from dbus-monitor."""
        if self.lines_to_check > 0:
            self.lines_to_check -= 1
            if "alarm-clock-elapsed" in line:
                self._perform_action()
                self.lines_to_check = 0
                return

        if "org.gnome.clocks" in line:
            self.lines_to_check = 5

    def _perform_action(self):
        """Wakes up the screen and maximizes volume."""
        logger.info("Alarm clock event detected!")

        try:
            if os.path.exists("/sys/class/leds/lcd-backlight/brightness"):
                with open("/sys/class/leds/lcd-backlight/brightness", "r") as f:
                    brightness = int(f.read().strip())

                if brightness == 0:
                    logger.info("Screen is off, waking up/pressing power...")
                    run_command("wtype -P XF86PowerOff", check=False)
        except Exception as e:
            logger.error(f"Error checking brightness: {e}")

        # Save the current volume once (a second alarm before the restore
        # fires must not overwrite the original with our forced max). Only
        # cache a successful read, so a failed read is retried next alarm.
        if self.saved_volume is None:
            name, vol, muted = get_default_sink_volume()
            if name:
                self.saved_volume = (name, vol, muted)
                logger.info(f"Saved volume: sink={name} vol={vol} muted={muted}")

        sink_name = self.saved_volume[0] if self.saved_volume else None
        if sink_name:
            set_sink_volume(sink_name, 1.0, False)

            # Put the user's volume back after the alarm has had time to ring.
            if self.restore_id > 0:
                GLib.source_remove(self.restore_id)
            self.restore_id = GLib.timeout_add_seconds(VOLUME_RESTORE_DELAY_S, self._restore_volume)

    def _restore_volume(self):
        self.restore_id = 0
        if self.saved_volume and self.saved_volume[0]:
            name, vol, muted = self.saved_volume
            self.saved_volume = None
            logger.info(f"Restoring volume: sink={name} vol={vol} muted={muted}")
            set_sink_volume(name, vol, muted)
        return GLib.SOURCE_REMOVE

def run():
    monitor = AlarmMonitor()
    monitor.start()

if __name__ == "__main__":
    from tweak_flx1s.utils import setup_logging
    setup_logging()
    run()
