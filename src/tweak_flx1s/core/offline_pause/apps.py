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
"""Find running user apps that can be put on offline pause, with friendly names."""
import os
import re

from . import procs


def _unescape(unit):
    return re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), unit)


def _desktop_index():
    """{executable basename (15 chars) or flatpak id: (name, icon name)}."""
    from gi.repository import Gio
    index = {}
    for info in Gio.AppInfo.get_all():
        try:
            app_id = (info.get_id() or "")[:-len(".desktop")] if (info.get_id() or "").endswith(".desktop") \
                else (info.get_id() or "")
            icon = info.get_icon().to_string() if info.get_icon() else None
            entry = (info.get_name(), icon)
            index.setdefault(app_id, entry)
            exe = info.get_executable()
            if exe:
                index.setdefault(os.path.basename(exe)[:15], entry)
                index.setdefault(os.path.basename(exe), entry)
        except (TypeError, AttributeError):
            continue
    return index


def _scope_app_id(cgroup):
    """sm.puri.Chatty-daemon from .../app-gnome-sm.puri.Chatty\\x2ddaemon-4067.scope."""
    unit = _unescape(cgroup.rsplit("/", 1)[-1])
    m = re.match(r"app-(?:gnome|phosh|[a-z]+)-(.+)-\d+\.scope$", unit)
    return m.group(1) if m else None


def describe(key, index=None):
    """(name, icon) for a target key."""
    if key in procs.BUILTINS:
        return procs.BUILTINS[key], None
    index = index if index is not None else _desktop_index()
    kind, _, value = key.partition(":")
    if kind == "flatpak":
        return index.get(value, (value.rsplit(".", 1)[-1], None))
    base = value.rsplit(".", 1)[0] if kind == "script" else value
    return index.get(value) or index.get(base) or (base, None)


def candidates(exclude_keys=()):
    """Running apps a user may pause: [{"key", "name", "icon", "pids", "detail"}].

    Only processes in the user manager's app.slice (apps and user services) are
    considered, never the shell, audio, telephony or D-Bus infrastructure.
    """
    index = _desktop_index()
    all_procs = procs.own_processes(with_cgroup=True)
    builtin = procs.assign(all_procs)
    managers = {pid for pid, i in all_procs.items() if i["comm"] == "systemd" and "--user" in i["cmdline"]}
    groups = {}
    for pid, info in all_procs.items():
        if pid in builtin or "/app.slice/" not in info.get("cgroup", ""):
            continue
        key = procs.app_key(info)
        if not key or procs.is_denied(info, key) or key in exclude_keys:
            continue
        # Only top-level processes: a helper started by another app or service
        # (pactl under ringtone-volume, dbus-monitor under tweak-flx1s) belongs
        # to its parent and must not be frozen on its own. Flatpak apps are
        # grouped by their scope instead.
        if not key.startswith("flatpak:") and info["ppid"] not in managers:
            continue
        g = groups.setdefault(key, {"key": key, "pids": [], "detail": ""})
        g["pids"].append(pid)
        if not g["detail"]:
            g["detail"] = " ".join(info["cmdline"])[:120]
        scope_id = _scope_app_id(info.get("cgroup", ""))
        if scope_id and "name" not in g and scope_id in index:
            g["name"], g["icon"] = index[scope_id]
    out = []
    for key, g in groups.items():
        if "name" not in g:
            g["name"], g["icon"] = describe(key, index)
        out.append(g)
    out.sort(key=lambda g: g["name"].lower())
    return out
