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
"User-level power tools. System services use systemd's D-Bus API and the existing FuriOS polkit rules. No additional privileges are installed."
import json
import os
import shutil
import signal
import stat
import subprocess
import time

from tweak_flx1s.const import CACHE_DIR
from . import procs

# ---------------------------------------------------------------- helpers


def run(args, timeout=30):
    """Run a command; return (ok, combined output)."""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def unit_state(unit, user=False):
    ok, out = run(["systemctl"] + (["--user"] if user else []) + ["is-active", unit], 10)
    return out.splitlines()[-1] if out else "unknown"


def unit_enabled(unit, user=False):
    ok, out = run(["systemctl"] + (["--user"] if user else []) + ["is-enabled", unit], 10)
    return out.splitlines()[-1] if out else "unknown"


# ---------------------------------------------------------------- Andromeda

LXC_PATH = "/var/lib/andromeda/lxc"
LXC_CGROUP = "/sys/fs/cgroup/lxc.payload.andromeda"
CONTAINER_UNIT = "andromeda-container.service"
SESSION_UNIT = "andromeda-session.service"
# Serves Android's sensors HAL. It is PartOf the container, so stopping the
# container stops it, but starting the container does not start it again; without
# it system_server waits for sensorservice forever and Android never boots.
SENSORS_UNIT = "andromeda-sensors.service"


def andromeda_state():
    """{"installed", "service": systemd state, "session": RUNNING/STOPPED/FROZEN/None,
    "restartable": False when Android can't start again after a stop without a reboot}.

    The session state comes from lxc-info directly: asking the container
    manager (GetSession) makes it log an error every time Android is stopped.
    """
    if not shutil.which("andromeda"):
        return {"installed": False, "service": "absent", "session": None, "restartable": True}
    return {"installed": True, "service": unit_state(CONTAINER_UNIT), "session": lxc_session(),
            "restartable": restart_ready()}


def lxc_session():
    """lxc-info's state. While Android runs, lxc-info refuses non-root users
    ("Insufficent privileges"), so fall back to the container's cgroup."""
    if shutil.which("lxc-info"):
        ok, out = run(["lxc-info", "-P", LXC_PATH, "-n", "andromeda", "-sH"], 10)
        if ok and out:
            return out.split()[0]
    try:
        with open(os.path.join(LXC_CGROUP, "cgroup.freeze")) as f:
            frozen = f.read().strip() == "1"
    except OSError:
        return "STOPPED" if not os.path.exists(LXC_CGROUP) else None
    return "FROZEN" if frozen else "RUNNING"


def restart_ready():
    """True if the container starts its sensors service and has no memlock limit.
    See docs/power-tools.md for the optional FuriOS compatibility setup."""
    ok, out = run(["systemctl", "show", "-p", "Wants", "-p", "LimitMEMLOCK", CONTAINER_UNIT], 10)
    if not ok:
        return True
    props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return SENSORS_UNIT in props.get("Wants", "").split() and props.get("LimitMEMLOCK") == "infinity"


def andromeda_stop():
    """Stop the Android session, then the container service. Returns errors."""
    errors = []
    state = andromeda_state()
    if state["session"] in ("RUNNING", "FROZEN"):
        ok, out = run(["andromeda", "session", "stop"], 90)
        if not ok:
            errors.append(f"andromeda session stop: {out[-300:]}")
    run(["systemctl", "--user", "stop", SESSION_UNIT], 30)
    ok, out = run(["systemctl", "stop", CONTAINER_UNIT], 60)
    if not ok:
        errors.append(out[-300:] or 'could not stop container service')
    return errors


def andromeda_start():
    """Start the container service and the Android session (boots in the background)."""
    errors = []
    ok, out = run(["systemctl", "start", CONTAINER_UNIT], 60)
    if not ok:
        errors.append(out[-300:] or 'could not start container service')
        return errors
    if unit_state(SENSORS_UNIT) != "active":
        errors.append('Android sensors are unavailable. See /usr/share/doc/tweak-flx1s/ for restart support.')
    ok, out = run(["systemctl", "--user", "--no-block", "restart", SESSION_UNIT], 15)
    if not ok:
        errors.append(out[-300:])
    return errors


# ---------------------------------------------------------------- inhibitors

INHIBIT_FLAGS = [(1, 'logout'), (2, 'user switching'), (4, 'suspend'), (8, 'screen blanking'),
                 (16, 'automount')]
SCREENSAVER_PROXY = "org.gnome.SettingsDaemon.ScreensaverProxy.service"
# logind inhibitors every FuriOS session has; not worth showing.
NORMAL_LOGIND = {"phosh", "gsd-media-keys", "gsd-power", "NetworkManager", "UPower",
                 "ModemManager", "gnome-session-b", "gnome-session-s"}
# Desktop files may start an app through one of these.
LAUNCHERS = {"env", "flatpak", "sh", "bash", "dash", "zsh"}


def flags_text(flags):
    parts = [label for bit, label in INHIBIT_FLAGS if flags & bit]
    return ", ".join(parts) if parts else str(flags)


def gnome_inhibitors(bus):
    """[{"path", "app_id", "reason", "flags"}] from gnome-session."""
    from gi.repository import Gio, GLib
    out = []
    reply = bus.call_sync("org.gnome.SessionManager", "/org/gnome/SessionManager",
                          "org.gnome.SessionManager", "GetInhibitors", None, None,
                          Gio.DBusCallFlags.NONE, 5000, None)
    for path in reply.unpack()[0]:
        item = {"path": path}
        for method, key in (("GetAppId", "app_id"), ("GetReason", "reason"), ("GetFlags", "flags")):
            try:
                item[key] = bus.call_sync("org.gnome.SessionManager", path,
                                          "org.gnome.SessionManager.Inhibitor", method, None, None,
                                          Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
            except GLib.Error:
                item[key] = "" if key != "flags" else 0
        out.append(item)
    return out


def logind_inhibitors():
    """Unusual blocking logind inhibitors: [{"what", "who", "why", "mode", "uid", "pid"}]."""
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        reply = bus.call_sync("org.freedesktop.login1", "/org/freedesktop/login1",
                              "org.freedesktop.login1.Manager", "ListInhibitors", None, None,
                              Gio.DBusCallFlags.NONE, 5000, None)
    except GLib.Error:
        return []
    out = []
    for what, who, why, mode, uid, pid in reply.unpack()[0]:
        info = procs.read_proc(pid)
        comm = info["comm"] if info else who
        if mode == "block" and ("sleep" in what or "idle" in what) and comm not in NORMAL_LOGIND \
                and who not in NORMAL_LOGIND:
            out.append({"what": what, "who": who, "why": why, "mode": mode, "uid": uid, "pid": pid})
    return out


def restart_screensaver_proxy(bus, timeout=5):
    """Drop every inhibitor held through org.freedesktop.ScreenSaver.

    Wayland idle inhibitors (phoc) and many players reach gnome-session through
    gsd-screensaver-proxy, so gnome-session lists them under the proxy's own
    connection; they disappear when that connection closes. The unit refuses a
    manual restart, but systemd restarts it after a crash (Restart=on-failure).
    """
    from gi.repository import Gio, GLib
    ok, out = run(["systemctl", "--user", "kill", "-s", "SIGKILL", SCREENSAVER_PROXY], 10)
    if not ok:
        return out or 'could not restart gsd-screensaver-proxy'
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.3)
        try:
            bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "GetNameOwner", GLib.Variant("(s)", ("org.freedesktop.ScreenSaver",)), None,
                          Gio.DBusCallFlags.NONE, 1000, None)
            return None
        except GLib.Error:
            continue
    return 'gsd-screensaver-proxy did not return; screen inhibitors are temporarily unavailable'


def _desktop_info(app_id):
    try:
        from gi.repository import Gio
        return Gio.DesktopAppInfo.new(app_id + ".desktop")
    except (TypeError, ImportError):
        return None


def _app_infos(app_id):
    """Process records of the app that registered an inhibitor (best effort)."""
    if not app_id or app_id.startswith("android."):
        return []          # Android apps live in the container; see stale_inhibitor()
    want = {app_id, app_id.rsplit(".", 1)[-1].lower()}
    desktop = _desktop_info(app_id)
    exe = os.path.basename(desktop.get_executable() or "") if desktop else ""
    # A wrapper's name says nothing about the app: "python3" would match every script.
    if exe and exe not in LAUNCHERS and not procs.INTERPRETERS.match(exe):
        want.add(exe[:15])
    return [info for info in procs.own_processes(with_cgroup=True).values()
            if procs.app_key(info) == f"flatpak:{app_id}" or info["comm"] in want]


def app_processes(app_id):
    """Running processes of the app that registered an inhibitor (best effort)."""
    return [info["pid"] for info in _app_infos(app_id)]


def closable_processes(app_id):
    """The app's processes that may be terminated from the UI. An inhibitor can
    belong to the shell or to a call, so the pause deny list applies here too."""
    return [info["pid"] for info in _app_infos(app_id)
            if not procs.is_denied(info, procs.app_key(info))]


def stale_inhibitor(item, andromeda_session):
    """True when the app behind an inhibitor is clearly gone.

    An id that names no installed app ("unknown", for example) proves nothing:
    its owner may well be running under another name.
    """
    app = item.get("app_id", "")
    if app.startswith("android."):
        return andromeda_session != "RUNNING"
    return bool(app) and bool(_desktop_info(app)) and not app_processes(app)


def alive(pid):
    """True while the process exists and is not a zombie waiting to be reaped."""
    info = procs.read_proc(pid)
    return bool(info) and info["state"] not in ("Z", "X")


def terminate(pids, grace=3.0):
    """SIGTERM, then SIGKILL whatever is still alive after `grace` seconds."""
    identities = {p: procs.read_proc(p) for p in pids if p != os.getpid()}
    identities = {p: info["start"] for p, info in identities.items() if info}
    pids = list(identities)
    def same_process(pid):
        info = procs.read_proc(pid)
        return info and info["start"] == identities[pid] and info["state"] != "Z"
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.monotonic() + grace
    left = list(pids)
    while left and time.monotonic() < deadline:
        time.sleep(0.2)
        left = [p for p in left if same_process(p)]
    for pid in left:
        try:
            if same_process(pid):
                os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    time.sleep(0.2)
    return [p for p in pids if not alive(p)]


# ---------------------------------------------------------------- orphans


def user_manager_pid():
    for pid, info in procs.own_processes().items():
        if info["comm"] == "systemd" and "--user" in info["cmdline"]:
            return pid
    return None


def _pipe_fds(pid):
    """{fd: (inode, mode)} for pipes; mode 0 read, 1 write, 2 read-write."""
    out = {}
    try:
        fds = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return out
    for fd in fds:
        try:
            target = os.readlink(f"/proc/{pid}/fd/{fd}")
        except OSError:
            continue
        if not target.startswith("pipe:["):
            continue
        try:
            with open(f"/proc/{pid}/fdinfo/{fd}") as f:
                flags = next(int(l.split()[1], 8) for l in f if l.startswith("flags:"))
        except (OSError, StopIteration, ValueError):
            continue
        out[int(fd)] = (target[6:-1], flags & 3)
    return out


def service_states():
    ok, out = run(["systemctl", "--user", "list-units", "--all", "--plain", "--no-legend",
                   "--type=service"], 15)
    states = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            states[parts[0]] = parts[2]
    return states


def find_orphans(require_reparented=True, scope=0):
    """Processes whose parent is gone and that can no longer do their job.

    Two signs, both requiring that the process was re-parented to the user's
    systemd (or init):
    - its stdout/stderr is a pipe nobody reads any more: every write fails, so
      whatever it produces is lost (e.g. a forgotten `dbus-monitor`);
    - it lingers in the cgroup of a user service that is stopped or failed.
    """
    all_procs = procs.own_processes(with_cgroup=True, scope=scope)
    manager = user_manager_pid()
    parents = {1, manager} if manager else {1}
    pipes = {pid: _pipe_fds(pid) for pid in all_procs}
    readers = {}
    for pid, fds in pipes.items():
        for inode, mode in fds.values():
            if mode in (0, 2):
                readers.setdefault(inode, set()).add(pid)
    states = None
    out = []
    for pid, info in all_procs.items():
        if pid == os.getpid() or not info["cmdline"]:
            continue
        if require_reparented and info["ppid"] not in parents:
            continue
        reasons = []
        for fd in (1, 2):
            entry = pipes[pid].get(fd)
            if entry and entry[1] == 1 and not (readers.get(entry[0], set()) - {pid}):
                reasons.append('writes to a pipe with no reader')
                break
        unit = info.get("cgroup", "").rsplit("/", 1)[-1]
        if unit.endswith(".service"):
            if states is None:
                states = service_states()
            if states.get(unit) in ("inactive", "failed"):
                reasons.append(f"left by stopped service {unit}")
        if reasons:
            out.append({"pid": pid, "comm": info["comm"], "cmdline": " ".join(info["cmdline"])[:200],
                        "reason": "; ".join(reasons), "start": info["start"]})
    return out


# ---------------------------------------------------------------- batman cores

BATMAN_DIR = os.environ.get("OFFLINE_PAUSE_BATMAN_DIR", "/var/lib/batman")
CPU_DIR = "/sys/devices/system/cpu"
CPU_CACHE = os.path.join(CACHE_DIR, "cpus.json")
PART_NAMES = {
    0xd03: "Cortex-A53", 0xd04: "Cortex-A35", 0xd05: "Cortex-A55", 0xd07: "Cortex-A57",
    0xd08: "Cortex-A72", 0xd09: "Cortex-A73", 0xd0a: "Cortex-A75", 0xd0b: "Cortex-A76",
    0xd0d: "Cortex-A77", 0xd41: "Cortex-A78", 0xd44: "Cortex-X1", 0xd46: "Cortex-A510",
    0xd47: "Cortex-A710", 0xd48: "Cortex-X2", 0xd4d: "Cortex-A715", 0xd4e: "Cortex-X3",
    0xd80: "Cortex-A520", 0xd81: "Cortex-A720", 0xd82: "Cortex-X4",
}


def parse_range(text):
    """CPU lists as the kernel writes them: "0-5,7" or "6 7"."""
    cpus = []
    for part in text.replace(" ", ",").strip().split(","):
        if not part:
            continue
        a, _, b = part.partition("-")
        cpus.extend(range(int(a), int(b or a) + 1))
    return cpus


def _read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def cpu_topology():
    """[{"cpu", "core", "max_khz", "policy", "online"}], cached for offline CPUs."""
    cache = {}
    try:
        with open(CPU_CACHE) as f:
            cache = {int(k): v for k, v in json.load(f).items()}
    except (OSError, ValueError):
        pass
    parts, current = {}, None
    for line in _read("/proc/cpuinfo").splitlines():
        if line.startswith("processor"):
            current = int(line.split(":")[1])
        elif line.startswith("CPU part") and current is not None:
            parts[current] = PART_NAMES.get(int(line.split(":")[1], 16), line.split(":")[1].strip())
    online = set(parse_range(_read(f"{CPU_DIR}/online", "0")))
    cpus = parse_range(_read(f"{CPU_DIR}/possible", "0"))
    policies = {}
    try:
        for pol in os.listdir(f"{CPU_DIR}/cpufreq"):
            if pol.startswith("policy"):
                for cpu in parse_range(_read(f"{CPU_DIR}/cpufreq/{pol}/related_cpus")):
                    policies[cpu] = (pol, int(_read(f"{CPU_DIR}/cpufreq/{pol}/cpuinfo_max_freq", "0")))
    except OSError:
        pass
    out = []
    for cpu in cpus:
        old = cache.get(cpu, {})
        pol, khz = policies.get(cpu, (old.get("policy"), old.get("max_khz", 0)))
        out.append({"cpu": cpu, "core": parts.get(cpu) or old.get("core") or "?",
                    "max_khz": khz, "policy": pol, "online": cpu in online})
    # Offline cores are missing from /proc/cpuinfo: borrow the name from a core
    # of the same cluster, else say whether the cluster is the slow or fast one.
    by_policy = {c["policy"]: c["core"] for c in out if c["core"] != "?" and c["policy"]}
    speeds = sorted({c["max_khz"] for c in out if c["max_khz"]})
    for c in out:
        if c["core"] == "?":
            c["core"] = by_policy.get(c["policy"]) or (
                'efficiency core' if len(speeds) > 1 and c["max_khz"] == speeds[0] else
                'performance core' if len(speeds) > 1 and c["max_khz"] == speeds[-1] else 'core')
    try:
        os.makedirs(os.path.dirname(CPU_CACHE), exist_ok=True)
        with open(CPU_CACHE, "w") as f:
            json.dump({c["cpu"]: c for c in out if c["core"].startswith("Cortex") or
                       c["core"].startswith("Neoverse")}, f)
    except OSError:
        pass
    return out


def batman_settings():
    """{"available", "offline": bool, "last": int or None, "writable": bool}."""
    config = os.path.join(BATMAN_DIR, "config")
    if not os.path.exists(config):
        return {"available": False}
    offline = True
    for line in _read(config).splitlines():
        if line.strip().startswith("OFFLINE="):
            offline = line.split("=", 1)[1].strip().lower() == "true"
    last = _read(os.path.join(BATMAN_DIR, "CUSTOM_LASTPOLCORE"))
    return {"available": True, "offline": offline,
            "last": int(last) if last.lstrip("-").isdigit() else None,
            "writable": os.access(BATMAN_DIR, os.W_OK) and os.access(config, os.W_OK)}


def kept_cores(last, ncpus):
    """CPUs batman leaves online with the screen off: it disables cpu0..last."""
    if last is None:
        return None
    last = max(0, min(last, ncpus - 1))
    return list(range(last + 1, ncpus))


def choices(ncpus):
    """[(last, kept cpus)] that keep at least one core: cpu0..last go offline."""
    return [(last, list(range(last + 1, ncpus))) for last in range(ncpus - 2, -1, -1)]


def _write_atomic(path, text):
    """Replace a file in one step, so a crash leaves the old or the new content."""
    tmp = f"{path}.tmp"
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        mode = 0o644
    try:
        with open(tmp, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_batman(offline, last):
    """Write batman's OFFLINE switch and CUSTOM_LASTPOLCORE (no restart)."""
    config = os.path.join(BATMAN_DIR, "config")
    # No _read() here: an unreadable file must not be replaced by an empty one.
    with open(config) as f:
        lines = f.read().splitlines()
    seen = False
    for i, line in enumerate(lines):
        if line.strip().startswith("OFFLINE="):
            lines[i] = f"OFFLINE={'true' if offline else 'false'}"
            seen = True
    if not seen:
        lines.insert(1 if lines and lines[0].startswith("[") else 0,
                     f"OFFLINE={'true' if offline else 'false'}")
    _write_atomic(config, "\n".join(lines) + "\n")
    if last is not None:
        _write_atomic(os.path.join(BATMAN_DIR, "CUSTOM_LASTPOLCORE"), f"{int(last)}\n")


def apply_batman(offline, last):
    """Write the settings and restart batman so its cpu_init re-reads them.

    Only call this with the screen on (all cores online): batman re-enables its
    *current* range when the screen turns on, so changing the range while cores
    are off could leave one of them disabled until the next screen cycle.
    """
    write_batman(offline, last)
    ok, out = run(["systemctl", "restart", "batman.service"], 30)
    return None if ok else (out or 'could not restart batman')


# ---------------------------------------------------------------- notification wake-ups

WAKE_SCHEMA = "sm.puri.phosh.notifications"
# (id, label, subtitle, triggers, urgency, categories)
WAKE_PRESETS = [
    ("all", 'Any Notification',
     'Every email and message wakes the screen',
     ["urgency", "category"], "low",
     ["call", "device", "email", "im", "network", "presence", "transfer"]),
    ("calls", 'Calls and Urgent Notifications',
     'Wake the screen for calls and urgent notifications',
     ["urgency", "category"], "critical", ["call"]),
    ("critical", 'Urgent Notifications Only',
     'Phosh default: wake only for urgent notifications',
     ["urgency"], "critical", []),
]


def wake_settings():
    """Gio.Settings for Phosh notifications, or None when Phosh is not installed."""
    from gi.repository import Gio
    source = Gio.SettingsSchemaSource.get_default()
    if not source or not source.lookup(WAKE_SCHEMA, True):
        return None
    return Gio.Settings.new(WAKE_SCHEMA)


def wake_preset(settings):
    """The id of the preset matching the current settings, or "custom"."""
    triggers = sorted(settings.get_strv("wakeup-screen-triggers"))
    urgency = settings.get_string("wakeup-screen-urgency")
    categories = sorted(settings.get_strv("wakeup-screen-categories"))
    for pid, _, _, t, u, c in WAKE_PRESETS:
        if sorted(t) == triggers and u == urgency and sorted(c) == categories:
            return pid
    return "custom"


def set_wake_preset(settings, preset_id):
    for pid, _, _, triggers, urgency, categories in WAKE_PRESETS:
        if pid == preset_id:
            settings.set_strv("wakeup-screen-triggers", triggers)
            settings.set_string("wakeup-screen-urgency", urgency)
            settings.set_strv("wakeup-screen-categories", categories)
            return True
    return False


# ---------------------------------------------------------------- user services

SERVICES = [
    ("flx1s-bat-mon.service", 'FuriOS Battery Log',
     'Logs battery level and processes every minute.'),
]


def service_info(unit):
    enabled = unit_enabled(unit, user=True)
    active = unit_state(unit, user=True)
    return {"unit": unit, "enabled": enabled, "active": active,
            "on": active == "active" or enabled in ("enabled", "enabled-runtime")}


def set_service(unit, on):
    """Enable+start or stop+disable a user unit; mask if it is enabled globally."""
    if on:
        run(["systemctl", "--user", "unmask", unit], 15)
        ok, out = run(["systemctl", "--user", "enable", "--now", unit], 30)
        if not ok:
            ok, out = run(["systemctl", "--user", "start", unit], 30)
        return None if ok else out
    run(["systemctl", "--user", "disable", "--now", unit], 30)
    if unit_enabled(unit, user=True) in ("enabled", "static", "enabled-runtime"):
        # Enabled from /etc/systemd/user (root-owned): masking is the user-level override.
        run(["systemctl", "--user", "stop", unit], 30)
        ok, out = run(["systemctl", "--user", "mask", unit], 15)
        return None if ok else out
    return None
