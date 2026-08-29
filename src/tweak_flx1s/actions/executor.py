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
import subprocess
import pwd
import threading
import time
from gi.repository import GLib, Gio
from tweak_flx1s.utils import logger, run_command
from tweak_flx1s.const import CACHE_DIR


def _login1_session_prop(bus, path, prop):
    """Reads one property of a login1 session object."""
    res = bus.call_sync(
        "org.freedesktop.login1", path,
        "org.freedesktop.DBus.Properties", "Get",
        GLib.Variant("(ss)", ("org.freedesktop.login1.Session", prop)),
        GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 2000, None,
    )
    return res.unpack()[0]


def is_locked():
    """Check session lock status via logind D-Bus (no subprocess spawns)."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        uid = os.getuid()
        sessions = bus.call_sync(
            "org.freedesktop.login1", "/org/freedesktop/login1",
            "org.freedesktop.login1.Manager", "ListSessions", None,
            GLib.VariantType("(a(susso))"), Gio.DBusCallFlags.NONE, 2000, None,
        ).unpack()[0]

        own = [(sid, path) for sid, s_uid, _user, _seat, path in sessions if s_uid == uid]
        if not own:
            return False

        target_path = None
        # Prefer the active graphical session, then any active, then the first.
        for _sid, path in own:
            if (_login1_session_prop(bus, path, "State") == "active"
                    and _login1_session_prop(bus, path, "Type") in ("wayland", "x11")):
                target_path = path
                break
        if not target_path:
            for _sid, path in own:
                if _login1_session_prop(bus, path, "State") == "active":
                    target_path = path
                    break
        if not target_path:
            target_path = own[0][1]

        return bool(_login1_session_prop(bus, target_path, "LockedHint"))
    except Exception as e:
        logger.warning(f"D-Bus lock check failed, falling back to loginctl: {e}")
        return _is_locked_loginctl()


def _is_locked_loginctl():
    """Fallback: check session lock status using loginctl subprocesses."""
    try:
        user = os.environ.get('USER')
        if not user:
            user = pwd.getpwuid(os.getuid()).pw_name

        out = run_command(f"loginctl list-sessions --no-legend", check=False)
        if not out:
             return False

        sessions = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                sid = parts[0]
                suser = parts[2]
                if suser == user:
                    sessions.append(sid)

        if not sessions:
            return False

        target_session = None

        logger.debug("Looking for active graphical session...")
        for sid in sessions:
            props = run_command(f"loginctl show-session {sid} -p Type -p State", check=False)
            prop_map = {}
            if props:
                for pline in props.splitlines():
                    if "=" in pline:
                        k, v = pline.split("=", 1)
                        prop_map[k] = v.strip()

            if prop_map.get("State") == "active" and prop_map.get("Type") in ["wayland", "x11"]:
                target_session = sid
                break

        if not target_session:
             logger.debug("No graphical session found, looking for any active session...")
             for sid in sessions:
                props = run_command(f"loginctl show-session {sid} -p State", check=False)
                if props and "State=active" in props:
                    target_session = sid
                    break

        if not target_session:
            logger.debug("No active session found, falling back to first session.")
            target_session = sessions[0]

        logger.debug(f"Checking lock state for session: {target_session}")
        out = run_command(f"loginctl show-session {target_session} -p LockedHint", check=False)
        return bool(out) and "LockedHint=yes" in out
    except Exception as e:
        logger.error(f"Error checking lock state: {e}")
        return False

def is_wofi_running():
    """Checks if wofi is currently running."""
    try:
        subprocess.check_call(["pgrep", "-x", "wofi"], stdout=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False

# Wofi is a layer-shell menu that must only ever have one instance, so all
# running-instance checks are global (any wofi, not just ours). A press that
# lands within this window of our own launch is the user re-pressing because
# the menu hadn't appeared yet — ignore it instead of closing the menu.
WOFI_LAUNCH_GRACE = 0.7
_WOFI_STAMP = os.path.join(CACHE_DIR, "wofi.launched")

def _mark_wofi_launched():
    """Stamps the moment we spawned wofi (for the grace window)."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_WOFI_STAMP, "w") as f:
            f.write(str(time.time()))
    except Exception as e:
        logger.warning(f"Failed to write wofi launch stamp: {e}")

def wofi_recently_launched():
    """True if our wofi was spawned less than WOFI_LAUNCH_GRACE ago."""
    try:
        with open(_WOFI_STAMP) as f:
            return (time.time() - float(f.read())) < WOFI_LAUNCH_GRACE
    except Exception:
        return False

_SCREEN_CACHE = os.path.join(CACHE_DIR, "wofi.screen")

def _query_screen_logical():
    """Asks GDK for the logical (scale-adjusted) screen size in pixels."""
    try:
        import gi
        gi.require_version('Gdk', '3.0')
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        if display:
            geo = display.get_monitor(0).get_geometry()
            if geo.width > 0 and geo.height > 0:
                return geo.width, geo.height
    except Exception as e:
        logger.warning(f"Failed to query screen size via GDK: {e}")
    return None

def _refresh_screen_cache():
    """Re-queries the screen size and stores it for the next launch."""
    size = _query_screen_logical()
    if size:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(_SCREEN_CACHE, "w") as f:
                f.write(f"{size[0]} {size[1]}")
        except Exception:
            pass

def _screen_logical():
    """Logical screen size for menu sizing. Reads the cached value so the
    press path stays fast; falls back to a live GDK query on first use and
    to the FLX1s portrait defaults if that fails too."""
    try:
        with open(_SCREEN_CACHE) as f:
            w, h = (int(v) for v in f.read().split())
            if w > 0 and h > 0:
                return w, h
    except Exception:
        pass
    size = _query_screen_logical()
    if size:
        _refresh_screen_cache()
        return size
    return 360, 804

def confirm_wofi_selection():
    """Presses Return in the open wofi menu to activate the selected option."""
    logger.info("Wofi is already running, simulating Return key.")
    subprocess.run(["wtype", "-k", "Return"], check=False)

def execute_command(cmd):
    """Executes a shell command in background."""
    if cmd:
        logger.info(f"Executing command: {cmd}")
        subprocess.Popen(cmd, shell=True)

_shell_present = None

def _gnome_shell_present():
    """Checks once per process whether org.gnome.Shell exists on the bus
    (it never does under Phosh — skip overview polling entirely there)."""
    global _shell_present
    if _shell_present is None:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            _shell_present = bool(bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "NameHasOwner",
                GLib.Variant("(s)", ("org.gnome.Shell",)),
                GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 2000, None,
            ).unpack()[0])
        except Exception:
            _shell_present = False
    return _shell_present

def is_overview_active():
    """Checks if GNOME Shell Overview is active (in-process D-Bus call)."""
    if not _gnome_shell_present():
        return False
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        res = bus.call_sync(
            "org.gnome.Shell", "/org/gnome/Shell",
            "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", ("org.gnome.Shell", "OverviewActive")),
            GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 2000, None,
        )
        return bool(res.unpack()[0])
    except Exception:
        return False

def show_wofi_menu(items):
    """Shows a wofi menu with given items."""
    if is_wofi_running():
        if wofi_recently_launched():
            logger.info("Wofi is still starting up, ignoring repeated press.")
        else:
            confirm_wofi_selection()
        return

    if is_overview_active():
        logger.info("GNOME Shell Overview is active. Closing it first...")
        subprocess.run(["pkill", "-x", "wofi"], check=False)
        subprocess.run(["wtype", "-M", "win", "-k", "Super_l"], check=False)
        time.sleep(0.35)

    try:
        from tweak_flx1s.system.wofi import WofiManager
        WofiManager().ensure_config_exists()
    except Exception as e:
        logger.error(f"Failed to regenerate dynamic wofi stylesheet: {e}")

    if len(items) > 7:
        logger.warning("Too many items for Wofi menu, truncating to 7.")
        items = items[:7]

    wofi_input = ""
    cmd_map = {}

    for idx, item in enumerate(items, 1):
        label = item.get("label", "Unknown")
        cmd = item.get("cmd", "")
        display_str = f"{idx}. {label}"
        wofi_input += f"{display_str}\n"
        cmd_map[display_str] = cmd

    close_idx = len(items) + 1
    display_close = f"{close_idx}. Close"
    wofi_input += display_close

    logger.info("Opening Wofi menu")
    try:
        _mark_wofi_launched()
        # Not --lines: wofi's line-height math breaks with the styled entries
        # (it requests a 64px surface no matter the count), so size the
        # surface ourselves — ~60 logical px per styled row plus panel chrome.
        # Both sizes are given in pixels, never percentages: wofi maps the
        # surface before resolving "%" against the output (the number is used
        # as raw pixels for the first frame), which flashes a mis-sized menu.
        # Content is 50px per row (42 pill + 8 gap) plus the 16px hidden
        # search input; the stylesheet strips GTK's invisible shadow margin,
        # so wofi maps at exactly this size and the compositor centers it.
        screen_w, screen_h = _screen_logical()
        menu_width = round(screen_w * 0.8)
        # +40 = 16px hidden input above the pills and a 24px tail that biases
        # the stack up to cancel Phosh's usable-area centering sitting ~8px
        # below true screen center: pills land dead center (measured -0.2px).
        menu_height = min(50 * close_idx + 40, screen_h - 60)
        process = subprocess.Popen(
            ["wofi", "-d", "--prompt", "Select an option:",
             "--width", str(menu_width), "--height", str(menu_height)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        def monitor_overview(proc):
            while proc.poll() is None:
                if is_overview_active():
                    logger.info("Overview opened while wofi was running. Terminating wofi...")
                    proc.terminate()
                    break
                time.sleep(0.2)

        # Under Phosh there is no GNOME Shell overview — don't poll at all.
        if _gnome_shell_present():
            threading.Thread(target=monitor_overview, args=(process,), daemon=True).start()

        stdout, stderr = process.communicate(input=wofi_input)

        # Off the critical path: pick up display scale changes for next time.
        _refresh_screen_cache()

        selection = stdout.strip()
        logger.info(f"User selected: {selection}")

        if selection == display_close:
            return

        if selection in cmd_map:
            cmd = cmd_map[selection]
            logger.info(f"Executing menu command: {cmd}")
            execute_command(cmd)
        else:
            logger.warning("Invalid selection")

    except Exception as e:
        logger.error(f"Error running wofi: {e}")
