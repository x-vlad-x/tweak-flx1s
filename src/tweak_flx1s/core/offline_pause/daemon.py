# Copyright (C) 2026 Vladislav Shadiuk xvladx@protonmail.com
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
"""Pause Proton Bridge, Geary and other chosen apps while the phone has no internet.

When the home router keeps Wi-Fi up but has no uplink (a Starlink sleep
schedule, for example), NetworkManager still reports full connectivity, so
Proton Bridge and Geary retry all night and keep the CPU awake. FuriOS never
suspends; batman only offlines cores, so every wakeup costs battery.

This daemon probes real connectivity (DNS + TCP to the mail servers), and after
FAIL_THRESHOLD failed probes in a row stops the selected apps with SIGSTOP.
Besides the built-ins, any user app can be chosen (see procs.py
for how apps are identified and which system processes are never touched).
They get SIGCONT as soon as a probe succeeds, the screen turns on (when
only_screen_off is set), the user asks to resume, or the daemon exits.

The settings app talks to it over the session bus (io.FuriOS.TweakFLX1s.OfflinePause).
"""
import json
import os
import signal
import sys
import time
import subprocess

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib
from . import procs

BUS_NAME = "io.FuriOS.TweakFLX1s.OfflinePause"
OBJECT_PATH = "/io/FuriOS/TweakFLX1s/OfflinePause"
INTERFACE = BUS_NAME

CONFIG_DIR = os.path.expanduser("~/.config/tweak-flx1s/offline-pause")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
STATE_DIR = os.path.expanduser("~/.local/state/tweak-flx1s/offline-pause")
EVENTS_PATH = os.path.join(STATE_DIR, "events.json")
PAUSED_PATH = os.path.join(STATE_DIR, "paused.json")

BATMAN_SCREEN = os.environ.get("OFFLINE_PAUSE_SCREEN_FILE", "/var/lib/batman/screen")
BACKLIGHT = "/sys/class/leds/lcd-backlight/brightness"
BATTERY = "/sys/class/power_supply/battery/capacity"

# Tests only: consider just descendants of this pid, never the user's real apps.
TEST_SCOPE = int(os.environ.get("OFFLINE_PAUSE_TEST_SCOPE", "0"))

FAIL_THRESHOLD = 2
RETRY_SECONDS = 30
PROBE_TIMEOUT = 10
MAX_EVENTS = 200

TARGETS = procs.BUILTINS
MAX_CUSTOM = 40
# Seconds, seconds, minutes. One day keeps every value within GLib's timers.
LIMITS = {"online_interval": (30, 86400), "offline_interval": (30, 86400),
          "hold_minutes": (1, 1440)}

DEFAULTS = {
    "enabled": True,
    "only_screen_off": True,
    "targets": {"bridge": True, "geary": True},
    # [{"key": "flatpak:org.example.App", "name": "Example"}, ...]
    "custom_targets": [],
    "online_interval": 300,
    "offline_interval": 120,
    "hold_minutes": 30,
    "probe_hosts": ["mail-api.proton.me:443", "imap.gmail.com:993"],
}

INTRO = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="GetStatus"><arg type="s" direction="out"/></method>
    <method name="GetEvents"><arg type="s" direction="out"/></method>
    <method name="SetConfig"><arg type="s" direction="in"/></method>
    <method name="CheckNow"/>
    <method name="ResumeNow"/>
    <method name="ClearEvents"/>
    <signal name="StatusChanged"><arg type="s"/></signal>
  </interface>
</node>
"""


def clean_custom(value):
    out, seen = [], set()
    if not isinstance(value, list):
        raise ValueError("custom_targets must be a list")
    for item in value or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        if key in TARGETS or key in seen or not procs.valid_key(key):
            continue
        name = str(item.get("name") or key.split(":", 1)[-1]).strip()[:80]
        out.append({"key": key, "name": name})
        seen.add(key)
    return out[:MAX_CUSTOM]


def merge_config(base, update):
    """Return base with known keys from update; targets are merged per app."""
    conf = json.loads(json.dumps(base))
    conf.setdefault("custom_targets", [])
    # Custom targets first, so a single update can add an app and enable it.
    if "custom_targets" in update:
        conf["custom_targets"] = clean_custom(update["custom_targets"])
        keys = {c["key"] for c in conf["custom_targets"]}
        conf["targets"] = {k: v for k, v in conf["targets"].items() if k in TARGETS or k in keys}
    allowed = set(TARGETS) | {c["key"] for c in conf["custom_targets"]}
    for key, value in update.items():
        if key not in DEFAULTS or key == "custom_targets":
            continue
        if key == "targets":
            conf["targets"].update({k: bool(v) for k, v in value.items() if k in allowed})
        elif key in LIMITS:
            low, high = LIMITS[key]
            conf[key] = min(high, max(low, int(value)))
        elif key == "probe_hosts":
            hosts = [str(h).strip() for h in value if str(h).strip()]
            conf[key] = hosts or DEFAULTS["probe_hosts"]
        else:
            conf[key] = bool(value)
    return conf


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return merge_config(DEFAULTS, json.load(f))
    except (OSError, ValueError, TypeError, AttributeError, OverflowError):
        return merge_config(DEFAULTS, {})


def write_json(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def should_pause(conf, online, screen_on, hold_until, now):
    """The whole policy: pause only on a confirmed outage, never while held."""
    if not conf["enabled"] or online is not False:
        return False
    if conf["only_screen_off"] and screen_on:
        return False
    return now >= hold_until


read_proc = procs.read_proc


def classify(cmdline, comm):
    """Map a process to a built-in target key, or None. cmdline is a list of args."""
    return procs.builtin_key({"cmdline": cmdline, "comm": comm})


def custom_keys(conf):
    return [c["key"] for c in conf.get("custom_targets", [])]


def target_names(conf):
    return {**TARGETS, **{c["key"]: c["name"] for c in conf.get("custom_targets", [])}}


def scan_processes(conf=None):
    """Return {target: [proc, ...]} for this user's processes, children included."""
    keys = custom_keys(conf) if conf else []
    return procs.scan(keys, scope=TEST_SCOPE, exclude_pid=os.getpid())


def read_screen_on():
    try:
        with open(BATMAN_SCREEN) as f:
            return f.read().strip() == "yes"
    except OSError:
        pass
    try:
        with open(BACKLIGHT) as f:
            return int(f.read().strip() or 0) > 0
    except (OSError, ValueError):
        return True


def read_battery():
    try:
        with open(BATTERY) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def send(pid, sig):
    try:
        os.kill(pid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def log_recovery():
    """Close the pause a dead daemon left open in the history."""
    events = read_json(EVENTS_PATH, [])
    event = {"time": time.time(), "kind": "resume", "text": 'Resumed: service stopped unexpectedly',
             "battery": read_battery()}
    for earlier in reversed(events):
        if earlier.get("kind") == "resume":
            break
        if earlier.get("kind") == "pause":
            event.update(duration=event["time"] - earlier["time"],
                         battery_start=earlier.get("battery"))
    write_json(EVENTS_PATH, (events + [event])[-MAX_EVENTS:])


def resume_recorded():
    """SIGCONT everything a previous run left stopped (crash, kill -9, reboot-safe)."""
    resumed = 0
    entries = read_json(PAUSED_PATH, [])
    for entry in entries:
        info = read_proc(entry.get("pid", -1))
        if (info and info["start"] == entry.get("start")
                and entry.get("boot_id") == boot_id()
                and send(info["pid"], signal.SIGCONT)):
            resumed += 1
    try:
        os.unlink(PAUSED_PATH)
    except FileNotFoundError:
        pass
    if entries:
        log_recovery()
    return resumed


def boot_id():
    with open("/proc/sys/kernel/random/boot_id") as f:
        return f.read().strip()


class Daemon:
    def __init__(self):
        self.conf = load_config()
        self.online = None          # None = not checked yet
        self.failures = 0
        self.last_check = 0.0
        self.last_error = ""
        self.checking = False
        self.screen_on = read_screen_on()
        self.hold_until = 0.0
        self.paused = {}            # target -> [{"pid", "start"}]
        self.pause_started = None   # (time, battery) of the current pause
        self.events = read_json(EVENTS_PATH, [])
        self.probe_source = 0
        self.hold_source = 0
        self.conn = None
        self.cancellable = Gio.Cancellable()
        self.client = Gio.SocketClient(timeout=PROBE_TIMEOUT)

    # ---------- events and status ----------

    def log(self, kind, text, **extra):
        event = {"time": time.time(), "kind": kind, "text": text,
                 "battery": read_battery(), **extra}
        self.events.append(event)
        del self.events[:-MAX_EVENTS]
        write_json(EVENTS_PATH, self.events)
        print(f"{kind}: {text}", flush=True)

    def status(self):
        return {
            "config": self.conf,
            "online": self.online,
            "failures": self.failures,
            "last_check": self.last_check,
            "last_error": self.last_error,
            "checking": self.checking,
            "screen_on": self.screen_on,
            "hold_until": self.hold_until if self.hold_until > time.time() else 0,
            "paused": sorted(self.paused),
            "pause_started": self.pause_started[0] if self.pause_started else None,
            "running": {k: len(v) for k, v in scan_processes(self.conf).items()},
            "names": target_names(self.conf),
        }

    def emit(self):
        if self.conn:
            self.conn.emit_signal(None, OBJECT_PATH, INTERFACE, "StatusChanged",
                                  GLib.Variant("(s)", (json.dumps(self.status()),)))

    # ---------- pausing ----------

    def apply(self):
        want = should_pause(self.conf, self.online, self.screen_on,
                            self.hold_until, time.time())
        wanted = {k for k, on in self.conf["targets"].items() if on} if want else set()
        found = scan_processes(self.conf) if wanted else None
        names = target_names(self.conf)
        newly = []
        # Removed targets that are still paused get resumed too.
        for key in list(names) + [k for k in self.paused if k not in names]:
            if key in wanted:
                # Also stops processes started while paused (e.g. Geary relaunched).
                known = {(e["pid"], e["start"]) for e in self.paused.get(key, [])}
                for p in found.get(key, []):
                    if (p["pid"], p["start"]) in known or p["state"] in ("T", "t"):
                        continue
                    # Persist before SIGSTOP, so even a crash at the next line
                    # leaves a record for ExecStopPost to recover.
                    if key not in self.paused:
                        newly.append(key)
                    entry = {"pid": p["pid"], "start": p["start"], "boot_id": boot_id()}
                    self.paused.setdefault(key, []).append(entry)
                    write_json(PAUSED_PATH, [e for v in self.paused.values() for e in v])
                    if not send(p["pid"], signal.SIGSTOP):
                        self.paused[key].remove(entry)
                        if not self.paused[key]:
                            del self.paused[key]
            elif key in self.paused:
                for e in reversed(self.paused.pop(key)):
                    info = read_proc(e["pid"])
                    if info and info["start"] == e["start"]:
                        send(e["pid"], signal.SIGCONT)
        write_json(PAUSED_PATH, [e for v in self.paused.values() for e in v])
        # A target whose SIGSTOP failed was not paused after all.
        newly = [k for k in dict.fromkeys(newly) if k in self.paused]
        if newly:
            if not self.pause_started:
                self.pause_started = (time.time(), read_battery())
            self.log("pause", 'Offline — paused: ' +
                     ", ".join(names.get(k, k) for k in newly), targets=newly)
        if not self.paused and self.pause_started:
            started, battery = self.pause_started
            self.pause_started = None
            self.log("resume", 'Resumed: ' + self.resume_reason(),
                     duration=time.time() - started, battery_start=battery)

    def resume_reason(self):
        if not self.conf["enabled"]:
            return 'service disabled'
        if self.online:
            return 'internet restored'
        if self.conf["only_screen_off"] and self.screen_on:
            return 'screen turned on'
        if self.hold_until > time.time():
            return 'requested by user'
        return 'app disabled in settings'

    def resume_all(self):
        for entries in self.paused.values():
            for e in reversed(entries):
                info = read_proc(e["pid"])
                if info and info["start"] == e["start"]:
                    send(e["pid"], signal.SIGCONT)
        self.paused.clear()
        try:
            os.unlink(PAUSED_PATH)
        except FileNotFoundError:
            pass

    # ---------- probing ----------

    def schedule_probe(self, seconds):
        if self.probe_source:
            GLib.source_remove(self.probe_source)
        self.probe_source = GLib.timeout_add_seconds(max(1, int(seconds)), self.on_probe_timer)

    def next_interval(self):
        if self.online is False:
            return self.conf["offline_interval"]
        if self.failures:
            return RETRY_SECONDS
        return self.conf["online_interval"]

    def on_probe_timer(self):
        self.probe_source = 0
        self.probe()
        return GLib.SOURCE_REMOVE

    def probe(self, force=False):
        if self.checking:
            return
        if not self.conf["enabled"] and not force:
            # Nothing to pause; status stays as last seen until the user asks.
            return
        self.checking = True
        self.emit()
        self.try_host(list(self.conf["probe_hosts"]), "")

    def try_host(self, hosts, error):
        if not hosts:
            self.finish_probe(False, error)
            return
        host = hosts.pop(0)
        self.client.connect_to_host_async(host, 443, self.cancellable,
                                          self.on_connected, (hosts, host))

    def on_connected(self, client, result, data):
        hosts, host = data
        try:
            conn = client.connect_to_host_finish(result)
        except GLib.Error as exc:
            if exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                return
            self.try_host(hosts, f"{host}: {exc.message}")
            return
        conn.close(None)
        self.finish_probe(True, "")

    def finish_probe(self, ok, error):
        self.checking = False
        self.last_check = time.time()
        self.last_error = error
        was = self.online
        if ok:
            self.failures = 0
            self.online = True
            if was is False:
                self.log("online", 'Internet available again')
        else:
            self.failures += 1
            if self.failures >= FAIL_THRESHOLD and was is not False:
                self.online = False
                self.log("offline", 'Internet unavailable', error=error)
        self.apply()
        self.schedule_probe(self.next_interval())
        self.emit()

    # ---------- inputs ----------

    def on_screen_changed(self, *args):
        on = read_screen_on()
        if on == self.screen_on:
            return
        self.screen_on = on
        self.apply()
        if not on:
            # Catch an outage that began while the screen was on.
            self.schedule_probe(5)
        self.emit()

    def on_nm_state(self, *args):
        # Wi-Fi roamed or dropped: re-check soon instead of waiting minutes.
        self.schedule_probe(5)

    def on_hold_expired(self):
        self.hold_source = 0
        self.hold_until = 0
        self.apply()
        self.emit()
        return GLib.SOURCE_REMOVE

    # ---------- D-Bus ----------

    def on_method(self, conn, sender, path, iface, method, params, invocation):
        if method == "GetStatus":
            invocation.return_value(GLib.Variant("(s)", (json.dumps(self.status()),)))
            return
        if method == "GetEvents":
            invocation.return_value(GLib.Variant("(s)", (json.dumps(self.events),)))
            return
        if method == "SetConfig":
            try:
                update = json.loads(params.unpack()[0])
                self.conf = merge_config(self.conf, update)
            except (ValueError, TypeError, AttributeError, OverflowError) as exc:
                invocation.return_dbus_error(f"{INTERFACE}.Error.InvalidArgs", str(exc))
                return
            write_json(CONFIG_PATH, self.conf)
            if self.conf["enabled"] and not self.probe_source and not self.checking:
                self.schedule_probe(1)
            self.apply()
            self.emit()
        elif method == "CheckNow":
            if self.probe_source:
                GLib.source_remove(self.probe_source)
                self.probe_source = 0
            self.probe(force=True)
        elif method == "ResumeNow":
            self.hold_until = time.time() + self.conf["hold_minutes"] * 60
            if self.hold_source:
                GLib.source_remove(self.hold_source)
            self.hold_source = GLib.timeout_add_seconds(
                self.conf["hold_minutes"] * 60 + 1, self.on_hold_expired)
            self.apply()
            self.emit()
        elif method == "ClearEvents":
            self.events = []
            write_json(EVENTS_PATH, self.events)
            self.emit()
        invocation.return_value(None)

    def on_bus_acquired(self, conn, name):
        self.conn = conn
        node = Gio.DBusNodeInfo.new_for_xml(INTRO)
        # register_object is deprecated since GLib 2.84, which added the closures2 call.
        register = getattr(conn, "register_object_with_closures2", conn.register_object)
        register(OBJECT_PATH, node.interfaces[0], self.on_method, None, None)

    def run(self):
        loop = GLib.MainLoop()
        resume_recorded()
        self.events = read_json(EVENTS_PATH, [])

        # batman rewrites /var/lib/batman/screen ("yes"/"no") on every change;
        # watching the directory catches both in-place writes and renames.
        screen = Gio.File.new_for_path(BATMAN_SCREEN)
        self.monitor = None
        try:
            if screen.query_exists(None):
                self.monitor = screen.get_parent().monitor_directory(Gio.FileMonitorFlags.NONE, None)
                self.monitor.connect("changed", lambda m, f, o, e: f.get_basename() == "screen"
                                     and self.on_screen_changed())
        except GLib.Error:
            self.monitor = None
        if not self.monitor:
            GLib.timeout_add_seconds(60, lambda: (self.on_screen_changed(), True)[1])

        system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        system.signal_subscribe("org.freedesktop.NetworkManager",
                                "org.freedesktop.NetworkManager", "StateChanged",
                                "/org/freedesktop/NetworkManager", None,
                                Gio.DBusSignalFlags.NONE, self.on_nm_state)

        Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                         self.on_bus_acquired, None, lambda *a: loop.quit())

        def quit_handler():
            loop.quit()
            return GLib.SOURCE_REMOVE
        try:
            gi.require_version("GLibUnix", "2.0")
            from gi.repository import GLibUnix
            signal_add = GLibUnix.signal_add
        except (ValueError, ImportError, AttributeError):
            signal_add = GLib.unix_signal_add
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal_add(GLib.PRIORITY_HIGH, sig, quit_handler)

        self.schedule_probe(3)
        try:
            loop.run()
        finally:
            self.cancellable.cancel()
            if self.paused:
                self.resume_all()
                if self.pause_started:
                    started, battery = self.pause_started
                    self.pause_started = None
                    self.log("resume", 'Resumed: service stopped',
                             duration=time.time() - started, battery_start=battery)


def main(argv=None):
    argv = argv or []
    if "--resume-all" in argv:
        # ExecStopPost safety net: resume only processes recorded by this service.
        resume_recorded()
        return 0
    legacy = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "offline-pause.service"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    if legacy.returncode == 0:
        print("Stop the standalone offline-pause service before enabling Tweak-FLX1s offline pause.", file=sys.stderr)
        raise SystemExit(78)
    Daemon().run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
