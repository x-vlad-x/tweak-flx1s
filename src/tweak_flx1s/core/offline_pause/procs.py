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
"""Process discovery shared by the daemon and the settings app (no GTK here).

A pausable app is identified by a key:
  flatpak:<app id>   every process in the app's app-flatpak-<id>-N.scope
  script:<name>      an interpreter (python3, perl, gjs...) running <name>
  comm:<name>        processes whose kernel name (comm, 15 chars) is <name>
plus the built-ins "bridge" (Proton Bridge GTK frontend and core) and "geary".
Children always follow their parent (Geary's WebKit processes, for example).
"""
import os
import re

BUILTINS = {"bridge": "Proton Bridge", "geary": "Geary"}

KEY_RE = re.compile(r"^(flatpak|script|comm):[^\s/]{1,200}$")
FLATPAK_RE = re.compile(r"/app-flatpak-([^/]+?)-\d+\.scope$")
INTERPRETERS = re.compile(r"^(python\d*(\.\d+)?|perl|gjs|ruby|node|lua\d*(\.\d+)?)$")

# Never paused, even if asked: freezing these hangs the shell, sound, calls,
# SMS, alarms, Android, the keyring, D-Bus services other apps call synchronously,
# or ourselves.
DENY_COMMS = {
    "phosh", "phoc", "systemd", "dbus-daemon", "dbus-broker", "pipewire", "pipewire-pulse",
    "wireplumber", "pulseaudio", "feedbackd", "callaudiod", "gnome-calls", "chatty", "mmsd",
    "ofono-toned", "cellbroadcastd", "dconf-service", "squeekboard", "offline-pause",
    "ringtone-volume", "tweak-flx1s", "pactl", "fbcli", "dbus-monitor",
    "sh", "bash", "dash", "zsh", "ssh", "sshd", "sleep", "tail", "grep",
    "busctl", "gdbus", "systemctl", "wl-paste", "flatpak-session", "bwrap", "xdg-dbus-proxy",
    "mpris-proxy", "obexd", "biomd-session", "gesture-sensors", "assistant-butto",
    "android-vibrato", "pqdbus", "flashlightd", "kded", "wpa_supplicant", "batman",
    "ssh-agent", "gcr-ssh-agent", "gpg-agent", "p11-kit-server", "lmkd-user-serve", "lmkd",
    "gnome-clocks", "andromeda", "lxc-start", "lxc-attach",
}
DENY_PREFIXES = ("gsd-", "gvfs", "phosh-", "at-spi", "xdg-", "evolution-", "goa-",
                 "gnome-session", "gnome-keyring", "gnome-shell", "tracker", "localsearch",
                 "polkit", "sensor")
DENY_SCRIPTS = {"offline-pause", "offline-pause-settings", "ringtone-volume",
                "ringtone-volume-settings", "flx1s-bat-mon-logger", "tweak-flx1s",
                "tweak_flx1s", "tweak_flx1s.core.offline_pause.daemon"}


DENY_FLATPAKS = {"org.gnome.clocks", "org.gnome.Calls", "sm.puri.Chatty"}


def is_denied_comm(comm):
    return comm in DENY_COMMS or comm.startswith(DENY_PREFIXES)


def valid_key(key):
    return key in BUILTINS or bool(KEY_RE.match(key))


def read_proc(pid, with_cgroup=False):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
        with open(f"/proc/{pid}/stat") as f:
            stat = f.read()
    except OSError:
        return None
    comm = stat[stat.index("(") + 1:stat.rindex(")")]
    fields = stat[stat.rindex(")") + 2:].split()
    # fields[0] is state, [1] ppid, [19] starttime (stat fields 3, 4, 22)
    info = {"pid": pid, "cmdline": cmdline, "comm": comm,
            "state": fields[0], "ppid": int(fields[1]), "start": fields[19]}
    if with_cgroup:
        try:
            with open(f"/proc/{pid}/cgroup") as f:
                info["cgroup"] = next((l[3:].strip() for l in f if l.startswith("0::")), "")
        except OSError:
            info["cgroup"] = ""
    return info


def builtin_key(info):
    """Map a process to "bridge"/"geary", or None."""
    cmdline, comm = info["cmdline"], info["comm"]
    if comm == "geary":
        return "geary"
    if not cmdline:
        return None
    exe = os.path.basename(cmdline[0])
    if exe == "bridge" and "--grpc" in cmdline and "proton-bridge" in cmdline[0]:
        return "bridge"
    if exe.startswith("python") and "proton_bridge_gtk" in cmdline[1:3] and "-m" in cmdline[1:3]:
        return "bridge"
    return None


def script_name(cmdline):
    """The script or module an interpreter runs: python3 -m foo / python3 /x/foo.py."""
    args = cmdline[1:]
    for i, arg in enumerate(args):
        if arg == "-m" and i + 1 < len(args):
            return args[i + 1]
        if arg in ("-c", "-e"):
            return None
        if not arg.startswith("-"):
            return os.path.basename(arg)
    return None


def app_key(info):
    """The custom-target key describing this process (needs info["cgroup"])."""
    m = FLATPAK_RE.search(info.get("cgroup", ""))
    if m:
        return f"flatpak:{m.group(1)}"
    comm = info["comm"]
    if INTERPRETERS.match(comm) and info["cmdline"]:
        name = script_name(info["cmdline"])
        return f"script:{name}" if name else None
    return f"comm:{comm}"


def is_denied(info, key):
    """Custom targets only; flatpak apps include their own bwrap/proxy helpers."""
    if key is None:
        return True
    if key.startswith("flatpak:"):
        return key[8:] in DENY_FLATPAKS
    if key.startswith("script:"):
        return (is_denied_comm(info["comm"]) or key[7:] in DENY_SCRIPTS
                or key[7:].split(".")[0] in DENY_SCRIPTS)
    return is_denied_comm(info["comm"])


def own_processes(with_cgroup=False, scope=0):
    """This user's processes; with scope, only descendants of that pid (tests)."""
    uid = os.getuid()
    procs = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            if os.stat(f"/proc/{name}").st_uid != uid:
                continue
        except OSError:
            continue
        info = read_proc(int(name), with_cgroup)
        if info:
            procs[info["pid"]] = info
    if scope:
        inside = {scope}
        changed = True
        while changed:
            changed = False
            for pid, info in procs.items():
                if pid not in inside and info["ppid"] in inside:
                    inside.add(pid)
                    changed = True
        procs = {pid: info for pid, info in procs.items() if pid in inside}
    return procs


def assign(procs, custom_keys=()):
    """Return {pid: key} for built-ins and the given custom keys, children included."""
    custom_keys = set(custom_keys)
    owner = {}
    for pid, info in procs.items():
        key = builtin_key(info)
        if not key and custom_keys:
            k = app_key(info)
            if k in custom_keys and not is_denied(info, k):
                key = k
        if key:
            owner[pid] = key
    changed = True
    while changed:
        changed = False
        for pid, info in procs.items():
            if pid not in owner and info["ppid"] in owner:
                owner[pid] = owner[info["ppid"]]
                changed = True
    return owner


def scan(custom_keys=(), scope=0, exclude_pid=None):
    """Return {key: [proc, ...]} for built-ins and custom keys."""
    procs = own_processes(with_cgroup=bool(custom_keys), scope=scope)
    found = {key: [] for key in BUILTINS}
    found.update({key: [] for key in custom_keys})
    for pid, key in assign(procs, custom_keys).items():
        if pid != exclude_pid:
            found[key].append(procs[pid])
    return found
