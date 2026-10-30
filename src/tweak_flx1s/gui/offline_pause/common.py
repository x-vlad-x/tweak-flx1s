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
"""Small helpers shared by the settings pages."""
import threading
import time

from gi.repository import GLib


def ago(timestamp):
    if not timestamp:
        return 'not checked yet'
    seconds = max(0, int(time.time() - timestamp))
    if seconds < 60:
        return 'just now'
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min ago"


def duration(seconds):
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def clock(timestamp, with_day=False):
    t = time.localtime(timestamp)
    if with_day and time.strftime("%Y%m%d", t) != time.strftime("%Y%m%d"):
        return time.strftime("%d.%m %H:%M", t)
    return time.strftime("%H:%M", t)


def short_error(error):
    """Turn a GLib probe error into a short error hint."""
    host, _, message = error.partition(": ")
    lower = message.lower()
    if "resolv" in lower or "name or service" in lower or "temporary failure" in lower:
        reason = 'DNS lookup failed'
    elif "timed out" in lower or "timeout" in lower:
        reason = 'server timed out'
    elif "refused" in lower:
        reason = 'connection refused'
    elif "unreachable" in lower:
        reason = 'network unreachable'
    else:
        reason = message or error
    return f"{host.rsplit(':', 1)[0]}: {reason}"


def in_thread(work, done=None):
    """Run work() off the UI thread; call done(result_or_exception) on it."""
    def runner():
        try:
            result = work()
        except Exception as exc:      # reported to the UI, never swallowed
            result = exc
        if done:
            GLib.idle_add(lambda: (done(result), False)[1])
    threading.Thread(target=runner, daemon=True).start()


def cpu_list(cpus):
    """[5, 6, 7] -> "cpu5–7", [7] -> "cpu7"."""
    if not cpus:
        return "—"
    if len(cpus) == 1:
        return f"cpu{cpus[0]}"
    if cpus == list(range(cpus[0], cpus[-1] + 1)):
        return f"cpu{cpus[0]}–{cpus[-1]}"
    return ", ".join(f"cpu{c}" for c in cpus)
