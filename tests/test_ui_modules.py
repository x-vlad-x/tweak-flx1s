#!/usr/bin/python3
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
"""Unit tests for offline_pause_ui helpers (no phone, no session services needed)."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tweak_flx1s.core.offline_pause import procs, system  # noqa: E402


def info(comm, cmdline, cgroup="/user.slice/user-1.slice/user@1.service/app.slice/x.service", pid=10, ppid=1):
    return {"pid": pid, "ppid": ppid, "comm": comm, "cmdline": cmdline, "cgroup": cgroup,
            "state": "S", "start": "1"}


class ProcsTest(unittest.TestCase):
    def test_app_keys(self):
        flatpak = info("example", ["/app/bin/example"],
                       "/user.slice/u/app.slice/app-flatpak-org.example.App-1127259461.scope")
        self.assertEqual(procs.app_key(flatpak), "flatpak:org.example.App")
        self.assertEqual(procs.app_key(info("python3", ["/usr/bin/python3", "/x/example-helper.py"])),
                         "script:example-helper.py")
        self.assertEqual(procs.app_key(info("python3", ["python3", "-u", "-m", "proton_bridge_gtk"])),
                         "script:proton_bridge_gtk")
        self.assertIsNone(procs.app_key(info("python3", ["python3", "-c", "print(1)"])))
        self.assertEqual(procs.app_key(info("gnome-clocks", ["/usr/bin/gnome-clocks", "--hidden"])),
                         "comm:gnome-clocks")

    def test_deny(self):
        self.assertTrue(procs.is_denied(info("phosh", ["/usr/libexec/phosh"]), "comm:phosh"))
        self.assertTrue(procs.is_denied(info("gsd-power", ["x"]), "comm:gsd-power"))
        self.assertTrue(procs.is_denied(info("chatty", ["chatty"]), "comm:chatty"))
        self.assertTrue(procs.is_denied(info("tweak-flx1s", ["tweak-flx1s"]), "comm:tweak-flx1s"))
        self.assertTrue(procs.is_denied(info("python3", ["python3", "/x/offline-pause"]), "script:offline-pause"))
        # Flatpak apps include their own sandbox helpers.
        self.assertFalse(procs.is_denied(info("bwrap", ["bwrap"]), "flatpak:org.example.App"))
        self.assertFalse(procs.is_denied(info("lisgd", ["lisgd"]), "comm:lisgd"))

    def test_deny_alarms_and_android(self):
        self.assertTrue(procs.is_denied(info("gnome-clocks", ["gnome-clocks"]), "comm:gnome-clocks"))
        self.assertTrue(procs.is_denied(info("andromeda", ["python3", "/usr/bin/andromeda"]), "comm:andromeda"))
        self.assertTrue(procs.is_denied(info("gnome-clocks", ["gnome-clocks"]), "flatpak:org.gnome.clocks"))

    def test_valid_keys(self):
        for key in ("bridge", "geary", "flatpak:a.b.C", "comm:lisgd", "script:x.py"):
            self.assertTrue(procs.valid_key(key), key)
        for key in ("", "comm:", "rm -rf", "comm:a b", "flatpak:../x", "other:x"):
            self.assertFalse(procs.valid_key(key), key)

    def test_assign_children_follow_parent(self):
        table = {
            1: info("systemd", ["systemd", "--user"], pid=1, ppid=0),
            2: info("clocks", ["clocks"], pid=2, ppid=1),
            3: info("helper", ["helper"], pid=3, ppid=2),
            4: info("geary", ["geary"], pid=4, ppid=1),
            5: info("WebKitWebProce", ["webkit"], pid=5, ppid=4),
            6: info("phosh", ["phosh"], pid=6, ppid=1),
        }
        owner = procs.assign(table, ["comm:clocks", "comm:phosh"])
        self.assertEqual(owner, {2: "comm:clocks", 3: "comm:clocks", 4: "geary", 5: "geary"})


class FakeDesktop:
    def __init__(self, executable):
        self.executable = executable

    def get_executable(self):
        return self.executable


class InhibitorTest(unittest.TestCase):
    def setUp(self):
        self.table = {
            1: info("phosh", ["/usr/libexec/phosh"], pid=1),
            2: info("gnome-calls", ["gnome-calls"], pid=2),
            3: info("celluloid", ["celluloid"], pid=3),
            4: info("python3", ["python3", "/usr/bin/some-daemon"], pid=4),
        }
        self.desktop = {}
        for patcher in (patch.object(system.procs, "own_processes", return_value=self.table),
                        patch.object(system, "_desktop_info", side_effect=self.desktop.get)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_session_processes_are_found_but_never_closable(self):
        self.assertEqual(system.app_processes("sm.puri.Phosh"), [1])
        self.assertEqual(system.closable_processes("sm.puri.Phosh"), [])
        self.desktop["org.gnome.Calls"] = FakeDesktop("/usr/bin/gnome-calls")
        self.assertEqual(system.closable_processes("org.gnome.Calls"), [])
        self.assertEqual(system.closable_processes("io.github.celluloid_player.Celluloid"), [3])

    def test_interpreter_in_exec_line_matches_nothing(self):
        self.desktop["org.example.App"] = FakeDesktop("/usr/bin/python3")
        self.assertEqual(system.app_processes("org.example.App"), [])

    def test_stale_needs_an_installed_app_without_processes(self):
        self.assertFalse(system.stale_inhibitor({"app_id": "unknown"}, None))
        self.assertFalse(system.stale_inhibitor({"app_id": ""}, None))
        self.desktop["org.example.Gone"] = FakeDesktop("/usr/bin/gone")
        self.assertTrue(system.stale_inhibitor({"app_id": "org.example.Gone"}, None))
        self.desktop["sm.puri.Phosh"] = FakeDesktop("/usr/libexec/phosh")
        self.assertFalse(system.stale_inhibitor({"app_id": "sm.puri.Phosh"}, None))
        self.assertTrue(system.stale_inhibitor({"app_id": "android.com.example"}, "STOPPED"))
        self.assertFalse(system.stale_inhibitor({"app_id": "android.com.example"}, "RUNNING"))


class CoresTest(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(system.parse_range("0-5,7"), [0, 1, 2, 3, 4, 5, 7])
        self.assertEqual(system.parse_range("6 7"), [6, 7])
        self.assertEqual(system.kept_cores(6, 8), [7])
        self.assertEqual(system.kept_cores(None, 8), None)
        self.assertEqual(system.choices(4), [(2, [3]), (1, [2, 3]), (0, [1, 2, 3])])

    def test_failed_batman_write_keeps_the_old_file(self):
        with tempfile.TemporaryDirectory() as d, patch.object(system, "BATMAN_DIR", d):
            config = os.path.join(d, "config")
            with open(config, "w") as f:
                f.write("OFFLINE=true\nGOVERNOR=x\n")
            os.chmod(config, 0o640)
            with patch.object(system.os, "replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    system.write_batman(False, None)
            with open(config) as f:
                self.assertEqual(f.read(), "OFFLINE=true\nGOVERNOR=x\n")
            self.assertEqual(os.listdir(d), ["config"])
            system.write_batman(False, None)
            self.assertEqual(os.stat(config).st_mode & 0o777, 0o640)

    def test_unreadable_batman_config_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d, patch.object(system, "BATMAN_DIR", d):
            with self.assertRaises(OSError):
                system.write_batman(False, 4)
            self.assertEqual(os.listdir(d), [])

    def test_write_batman(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "config"), "w") as f:
                f.write("[Settings]\nOFFLINE=true\nPOWERSAVE=true\n")
            old, system.BATMAN_DIR = system.BATMAN_DIR, d
            try:
                system.write_batman(False, 4)
                with open(os.path.join(d, "config")) as f:
                    self.assertEqual(f.read(), "[Settings]\nOFFLINE=false\nPOWERSAVE=true\n")
                with open(os.path.join(d, "CUSTOM_LASTPOLCORE")) as f:
                    self.assertEqual(f.read(), "4\n")
                s = system.batman_settings()
                self.assertEqual((s["offline"], s["last"], s["writable"]), (False, 4, True))
            finally:
                system.BATMAN_DIR = old


class AndromedaTest(unittest.TestCase):
    def test_restart_ready(self):
        old = system.run
        try:
            for reply, expected in [
                    ((True, "Wants=andromeda-sensors.service\nLimitMEMLOCK=infinity"), True),
                    ((True, "Wants=\nLimitMEMLOCK=infinity"), False),
                    ((True, "Wants=andromeda-sensors.service\nLimitMEMLOCK=8388608"), False),
                    ((False, "no systemd"), True)]:
                system.run = lambda args, timeout=30, reply=reply: reply
                self.assertEqual(system.restart_ready(), expected, reply)
        finally:
            system.run = old

    def test_session_from_cgroup(self):
        old_run, old_cg = system.run, system.LXC_CGROUP
        system.run = lambda args, timeout=30: (False, "Insufficent privileges to control andromeda")
        try:
            with tempfile.TemporaryDirectory() as d:
                system.LXC_CGROUP = os.path.join(d, "lxc.payload.andromeda")
                self.assertEqual(system.lxc_session(), "STOPPED")
                os.mkdir(system.LXC_CGROUP)
                for value, state in (("0", "RUNNING"), ("1", "FROZEN")):
                    with open(os.path.join(system.LXC_CGROUP, "cgroup.freeze"), "w") as f:
                        f.write(value + "\n")
                    self.assertEqual(system.lxc_session(), state)
        finally:
            system.run, system.LXC_CGROUP = old_run, old_cg


class FakeSettings:
    def __init__(self, **values):
        self.values = values

    def get_strv(self, key):
        return list(self.values[key])

    def get_string(self, key):
        return self.values[key]

    def set_strv(self, key, value):
        self.values[key] = list(value)

    def set_string(self, key, value):
        self.values[key] = value


class WakeTest(unittest.TestCase):
    def test_presets(self):
        furios = FakeSettings(**{
            "wakeup-screen-triggers": ["category", "urgency"], "wakeup-screen-urgency": "low",
            "wakeup-screen-categories": ["im", "call", "device", "email", "network", "presence", "transfer"]})
        self.assertEqual(system.wake_preset(furios), "all")
        self.assertTrue(system.set_wake_preset(furios, "calls"))
        self.assertEqual(furios.values["wakeup-screen-categories"], ["call"])
        self.assertEqual(furios.values["wakeup-screen-urgency"], "critical")
        self.assertEqual(system.wake_preset(furios), "calls")
        furios.set_string("wakeup-screen-urgency", "normal")
        self.assertEqual(system.wake_preset(furios), "custom")
        self.assertFalse(system.set_wake_preset(furios, "custom"))


class OrphanTest(unittest.TestCase):
    def test_dead_pipe_reader_is_found(self):
        # A child that writes to stdout while nobody reads the pipe any more
        # (stderr goes to /dev/null: the test runner's own pipe has readers
        # outside the test scope).
        r, w = os.pipe()
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdout=w,
                                 stderr=subprocess.DEVNULL)
        os.close(w)
        os.close(r)
        try:
            time.sleep(0.2)
            found = system.find_orphans(require_reparented=False, scope=os.getpid())
            self.assertIn(child.pid, [o["pid"] for o in found], found)
            self.assertIn("pipe", next(o for o in found if o["pid"] == child.pid)["reason"])
            # With a live reader it is not an orphan.
            r2, w2 = os.pipe()
            ok = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdout=w2,
                                  stderr=subprocess.DEVNULL)
            os.close(w2)
            time.sleep(0.2)
            found = system.find_orphans(require_reparented=False, scope=os.getpid())
            self.assertNotIn(ok.pid, [o["pid"] for o in found])
            ok.kill()
            ok.wait()
            os.close(r2)
            self.assertEqual(system.terminate([child.pid]), [child.pid])
            child.wait()
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


class EdsTest(unittest.TestCase):
    DATA = ("[Data Source]\nDisplayName=Work\nEnabled=true\nParent=coll\n\n"
            "[Calendar]\nBackendName=caldav\n")

    def test_source_update(self):
        from tweak_flx1s.core.offline_pause import eds
        src = eds.Source("/x", "uid", self.DATA, True)
        self.assertEqual((src.kind, src.backend, src.enabled, src.refresh_minutes), ("Calendar", "caldav", True, 60))
        self.assertTrue(src.synced)
        data = src.updated(enabled=False, refresh_minutes=240)
        again = eds.Source("/x", "uid", data, True)
        self.assertEqual((again.enabled, again.refresh_enabled, again.refresh_minutes), (False, True, 240))
        manual = eds.Source("/x", "uid", again.updated(refresh_enabled=False), True)
        self.assertEqual((manual.refresh_enabled, manual.refresh_minutes), (False, 240))
        self.assertEqual(eds.refresh_label(again), "Every 4 h")
        self.assertEqual(eds.refresh_label(manual), 'Manual only')


if __name__ == "__main__":
    unittest.main()
