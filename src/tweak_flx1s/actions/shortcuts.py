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

import datetime
import os
from gi.repository import Gio, GLib
from tweak_flx1s.utils import logger, run_command, send_notification
from tweak_flx1s.const import HOME_DIR

class ShortcutsManager:
    def __init__(self):
        pass

    def take_screenshot(self):
        """Takes a screenshot and notifies the user."""
        timestamp = datetime.datetime.now().strftime("%F-%T")
        pictures_dir = run_command("xdg-user-dir PICTURES") or f"{HOME_DIR}/Pictures"
        path = f"{pictures_dir}/Screenshot-{timestamp}.png"

        logger.info(f"Taking screenshot: {path}")

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.NONE,
            None,
            "org.gnome.Shell.Screenshot",
            "/org/gnome/Shell/Screenshot",
            "org.gnome.Shell.Screenshot",
            None
        )

        try:
            proxy.call_sync(
                "Screenshot",
                GLib.Variant("(bbs)", (True, False, path)),
                Gio.DBusCallFlags.NONE,
                -1,
                None
            )
            send_notification("Screenshot Taken", f"Saved to {path}", icon_name="camera-photo")
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")

    def toggle_flashlight(self):
        """Toggles the flashlight on or off."""
        bus_name = "io.furios.Flashlightd"
        object_path = "/io/furios/Flashlightd"
        interface = "io.furios.Flashlightd"

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        try:
            proxy = Gio.DBusProxy.new_sync(
                bus, Gio.DBusProxyFlags.NONE, None, bus_name, object_path, interface, None
            )

            props = Gio.DBusProxy.new_sync(
                bus, Gio.DBusProxyFlags.NONE, None, bus_name, object_path, "org.freedesktop.DBus.Properties", None
            )
            current = props.call_sync(
                "Get", GLib.Variant("(ss)", (interface, "Brightness")), Gio.DBusCallFlags.NONE, -1, None
            ).unpack()[0]

            if current == 0:
                logger.info("Turning flashlight ON")
                max_b = props.call_sync(
                    "Get", GLib.Variant("(ss)", (interface, "MaxBrightness")), Gio.DBusCallFlags.NONE, -1, None
                ).unpack()[0]

                proxy.call_sync("SetBrightness", GLib.Variant("(u)", (max_b,)), Gio.DBusCallFlags.NONE, -1, None)
            else:
                logger.info("Turning flashlight OFF")
                proxy.call_sync("SetBrightness", GLib.Variant("(u)", (0,)), Gio.DBusCallFlags.NONE, -1, None)

        except Exception as e:
            logger.error(f"Flashlight error: {e}")

    def kill_active_window(self):
        """Simulates Alt+F4 to close the active window."""
        logger.info("Killing active window (Alt+F4 simulation)")
        run_command("wtype -M alt -P F4 -m alt -p F4")

    def kill_ram_eaters(self):
        """Kills processes consuming high CPU or Memory."""
        import time
        import psutil

        exclude = ["systemd", "bash", "sshd", "phosh", "gnome-shell"]
        killed = []

        # cpu_percent needs two samples: the first call only primes the
        # counters and always reports 0.0, so measure over a 1 s window.
        procs = []
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if proc.info['name'] in exclude or proc.pid == os.getpid():
                    continue
                proc.cpu_percent(None)
                procs.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

        time.sleep(1.0)

        for proc in procs:
            try:
                cpu = proc.cpu_percent(None)
                mem = proc.memory_percent()
                if mem > 80 or cpu > 80:
                    name = proc.name()
                    logger.info(f"Killing {name} (PID {proc.pid}, cpu={cpu:.0f}%, mem={mem:.0f}%)")
                    proc.kill()
                    killed.append(name)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

        if killed:
             send_notification("Killed High Usage Apps", ", ".join(killed))
        else:
             send_notification("No High Usage Apps", "Nothing exceeded 80% CPU or memory.")

    def set_scale(self, scale):
        """Sets the display scale using wlr-randr."""
        logger.info(f"Setting display scale to {scale}")
        run_command(f"wlr-randr --output 'HWCOMPOSER-1' --scale {scale}")

    def take_picture(self):
         """Takes a photo using gst-launch."""
         timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
         pictures_dir = f"{HOME_DIR}/Pictures"
         filename = f"{pictures_dir}/photo_{timestamp}.jpeg"

         cmd = (
            f"gst-launch-1.0 -e droidcamsrc camera-device=0 mode=2 ! "
            f"videoconvert ! videoflip video-direction=8 ! jpegenc snapshot=true ! "
            f"filesink location='{filename}'"
         )
         try:
            run_command(cmd)
            send_notification("Picture Taken", f"Saved to {filename}")
         except Exception as e:
            logger.error(f"Failed to take picture: {e}")

    def paste_clipboard(self):
        """Pastes content from clipboard or notifies if empty."""
        content = run_command("wl-paste", check=False)
        if not content:
            send_notification("Clipboard Empty", "Nothing to paste.")
        else:
            run_command(["wtype", content], check=False)
