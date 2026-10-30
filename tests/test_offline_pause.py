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
"""Tests for offline-pause. Run: dbus-run-session -- python3 -m unittest discover tests

The integration test drives a real daemon on a private session bus against fake
"geary" and "bridge" processes (OFFLINE_PAUSE_TEST_SCOPE keeps it away from any
real ones) and a local TCP listener that stands in for the internet.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from tweak_flx1s.core.offline_pause import daemon as op
DAEMON = [sys.executable, "-m", "tweak_flx1s.core.offline_pause.daemon"]


class PolicyTest(unittest.TestCase):
    def conf(self, **kw):
        return op.merge_config(op.DEFAULTS, kw)

    def test_pauses_only_on_confirmed_outage(self):
        c = self.conf()
        self.assertTrue(op.should_pause(c, False, False, 0, 100))
        self.assertFalse(op.should_pause(c, None, False, 0, 100))
        self.assertFalse(op.should_pause(c, True, False, 0, 100))

    def test_screen_and_hold(self):
        c = self.conf()
        self.assertFalse(op.should_pause(c, False, True, 0, 100))
        self.assertTrue(op.should_pause(self.conf(only_screen_off=False), False, True, 0, 100))
        self.assertFalse(op.should_pause(c, False, False, 200, 100))
        self.assertFalse(op.should_pause(self.conf(enabled=False), False, False, 0, 100))

    def test_recovery_checks_boot_and_process_identity(self):
        with patch.object(op, "read_json", return_value=[
                {"pid": 10, "start": "1", "boot_id": "old"},
                {"pid": 11, "start": "2", "boot_id": "current"},
                {"pid": 12, "start": "3", "boot_id": "current"}]), \
                patch.object(op, "boot_id", return_value="current"), \
                patch.object(op, "read_proc", side_effect=lambda p: {"pid": p, "start": "3"}), \
                patch.object(op, "send", return_value=True) as send, \
                patch.object(op.os, "unlink"):
            self.assertEqual(op.resume_recorded(), 1)
            send.assert_called_once_with(12, signal.SIGCONT)

    def test_pause_is_recorded_before_signal(self):
        daemon = op.Daemon()
        daemon.online = False
        daemon.screen_on = False
        seen = []
        proc = {"pid": 42, "start": "1", "state": "S"}
        with patch.object(op, "scan_processes", return_value={"geary": [proc]}), \
                patch.object(op, "write_json", side_effect=lambda p, d: seen.append(d)), \
                patch.object(op, "send", side_effect=lambda p, s: bool(seen and seen[-1][0]["pid"] == p)), \
                patch.object(daemon, "log"):
            daemon.apply()
        self.assertEqual(daemon.paused["geary"][0]["pid"], 42)

    def test_merge_config_validates(self):
        c = op.merge_config(op.DEFAULTS, {"online_interval": 3, "hold_minutes": 0,
                                          "targets": {"geary": False, "evil": True},
                                          "probe_hosts": [" "], "unknown": 1})
        self.assertEqual(c["online_interval"], 30)
        self.assertEqual(c["hold_minutes"], 1)
        self.assertEqual(c["targets"], {"bridge": True, "geary": False})
        self.assertEqual(c["probe_hosts"], op.DEFAULTS["probe_hosts"])
        self.assertNotIn("unknown", c)
        self.assertTrue(op.DEFAULTS["targets"]["geary"], "defaults must not be mutated")

    def test_merge_config_bounds_intervals(self):
        c = op.merge_config(op.DEFAULTS, {"online_interval": 10 ** 12, "hold_minutes": 10 ** 12})
        self.assertEqual(c["online_interval"], 86400)
        self.assertEqual(c["hold_minutes"], 1440)
        with self.assertRaises(OverflowError):
            op.merge_config(op.DEFAULTS, {"online_interval": float("inf")})

    def test_unusable_config_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "config.json")
            with open(path, "w") as f:
                f.write('{"online_interval": 1e999}')
            with patch.object(op, "CONFIG_PATH", path):
                self.assertEqual(op.load_config()["online_interval"], op.DEFAULTS["online_interval"])

    def test_failed_sigstop_is_not_logged_as_pause(self):
        daemon = op.Daemon()
        daemon.online = False
        daemon.screen_on = False
        proc = {"pid": 42, "start": "1", "state": "S"}
        with patch.object(op, "scan_processes", return_value={"geary": [proc]}), \
                patch.object(op, "write_json"), \
                patch.object(op, "send", return_value=False), \
                patch.object(daemon, "log") as log:
            daemon.apply()
        log.assert_not_called()
        self.assertEqual(daemon.paused, {})

    def test_custom_targets_merge(self):
        c = op.merge_config(op.DEFAULTS, {
            "custom_targets": [{"key": "flatpak:org.example.App", "name": "Example"},
                               {"key": "comm:bad key"}, {"key": "geary"},
                               {"key": "flatpak:org.example.App", "name": "dup"}],
            "targets": {"flatpak:org.example.App": True, "comm:unknown": True}})
        self.assertEqual(c["custom_targets"], [{"key": "flatpak:org.example.App", "name": "Example"}])
        self.assertTrue(c["targets"]["flatpak:org.example.App"])
        self.assertNotIn("comm:unknown", c["targets"])
        # Removing the app drops its switch too.
        c = op.merge_config(c, {"custom_targets": []})
        self.assertNotIn("flatpak:org.example.App", c["targets"])
        self.assertEqual(op.target_names(c), op.TARGETS)

    def test_classify_real_command_lines(self):
        bridge = ["/home/user/.local/share/proton-bridge-gtk/bin/bridge", "--grpc",
                  "--no-window", "--parent-pid", "4014"]
        self.assertEqual(op.classify(bridge, "bridge"), "bridge")
        self.assertEqual(op.classify(["/usr/bin/python3", "-m", "proton_bridge_gtk", "--no-window"],
                                     "python3"), "bridge")
        self.assertEqual(op.classify(["geary", "--gapplication-service"], "geary"), "geary")
        self.assertIsNone(op.classify(["/usr/bin/bridge"], "bridge"))
        self.assertIsNone(op.classify(["/usr/bin/python3", "/tmp/proton_bridge_gtk.py"], "python3"))
        self.assertIsNone(op.classify([], "kworker/0:1"))


def proc_state(pid):
    with open(f"/proc/{pid}/stat") as f:
        stat = f.read()
    return stat[stat.rindex(")") + 2]


@unittest.skipUnless(os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "needs a session bus")
class IntegrationTest(unittest.TestCase):
    def setUp(self):
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.tmp = tempfile.mkdtemp(prefix="offline-pause-test-")
        self.parent = self.daemon = self.server = None
        self.geary_pid = self.bridge_pid = self.geary_child = self.fake_pid = None
        # Unlike tearDown, this also runs when setUp fails halfway.
        self.addCleanup(self.cleanup)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.screen = os.path.join(self.tmp, "batman", "screen")
        os.makedirs(os.path.dirname(self.screen))
        self.set_screen(False)

        self.port = self.open_internet()
        os.makedirs(os.path.join(self.home, ".config", "tweak-flx1s", "offline-pause"))
        with open(os.path.join(self.home, ".config", "tweak-flx1s", "offline-pause", "config.json"), "w") as f:
            json.dump({"probe_hosts": [f"127.0.0.1:{self.port}"],
                       "custom_targets": [{"key": "comm:fakeapp", "name": "Fake app"}],
                       "targets": {"comm:fakeapp": True}}, f)

        # Fake targets live under a scope process so the daemon ignores real apps.
        bindir = os.path.join(self.tmp, "proton-bridge-gtk", "bin")
        os.makedirs(bindir)
        geary = os.path.join(self.tmp, "geary")
        os.symlink(sys.executable, geary)
        fakeapp = os.path.join(self.tmp, "fakeapp")
        os.symlink(sys.executable, fakeapp)
        script = f"""
import os, subprocess, sys, time
procs = [
    subprocess.Popen(["{geary}", "-c", "import subprocess, time; subprocess.Popen(['sleep', '600']); time.sleep(600)"]),
    subprocess.Popen(["/bin/bash", "-c", "exec -a {bindir}/bridge {sys.executable} -c 'import time; time.sleep(600)' --grpc"]),
    subprocess.Popen(["{fakeapp}", "-c", "import time; time.sleep(600)"]),
]
print(" ".join(str(p.pid) for p in procs), flush=True)
time.sleep(600)
"""
        self.parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
        self.geary_pid, self.bridge_pid, self.fake_pid = map(int, self.parent.stdout.readline().split())
        # A WebKit-like child of Geary must be stopped with it.
        self.geary_child = None
        deadline = time.monotonic() + 5
        while not self.geary_child and time.monotonic() < deadline:
            try:
                with open(f"/proc/{self.geary_pid}/task/{self.geary_pid}/children") as f:
                    kids = f.read().split()
                self.geary_child = int(kids[0]) if kids else None
            except OSError:
                pass
            time.sleep(0.05)
        self.assertTrue(self.geary_child, "fake Geary did not start its child")
        self.start_daemon(scope=self.parent.pid)
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def cleanup(self):
        pids = [p for p in (self.geary_pid, self.bridge_pid, self.geary_child, self.fake_pid) if p]
        for pid in pids:
            try:
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        if self.daemon and self.daemon.poll() is None:
            self.daemon.terminate()
            self.daemon.wait(10)
        if self.parent:
            self.parent.kill()
            self.parent.wait()
            self.parent.stdout.close()
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.server:
            self.server.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- helpers ----------

    def env(self, scope):
        # The private runtime dir hides the user's systemd, so a real standalone
        # service on this machine cannot trip the daemon's startup check.
        return {**os.environ, "HOME": self.home, "XDG_RUNTIME_DIR": self.tmp,
                "OFFLINE_PAUSE_SCREEN_FILE": self.screen,
                "DBUS_SYSTEM_BUS_ADDRESS": os.environ["DBUS_SESSION_BUS_ADDRESS"],
                "OFFLINE_PAUSE_TEST_SCOPE": str(scope), "PYTHONDONTWRITEBYTECODE": "1"}

    def start_daemon(self, scope):
        self.daemon = subprocess.Popen(DAEMON, env=self.env(scope),
                                       stdout=subprocess.DEVNULL)
        self.wait_for(lambda: self.status() is not None, "daemon on the bus")

    def open_internet(self, port=0):
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", port))
        self.server.listen(16)
        return self.server.getsockname()[1]

    def close_internet(self):
        self.server.close()
        self.server = None

    def set_screen(self, on):
        with open(self.screen, "w") as f:
            f.write("yes" if on else "no")

    def call(self, method, arg=None):
        params = self.GLib.Variant("(s)", (arg,)) if arg is not None else None
        reply = self.bus.call_sync(op.BUS_NAME, op.OBJECT_PATH, op.INTERFACE, method, params,
                                   None, self.Gio.DBusCallFlags.NONE, 5000, None)
        return reply.unpack()[0] if reply.n_children() else None

    def status(self):
        try:
            bus = self.Gio.bus_get_sync(self.Gio.BusType.SESSION, None)
            reply = bus.call_sync(op.BUS_NAME, op.OBJECT_PATH, op.INTERFACE, "GetStatus", None,
                                  None, self.Gio.DBusCallFlags.NONE, 2000, None)
            return json.loads(reply.unpack()[0])
        except self.GLib.Error:
            return None

    def wait_for(self, predicate, what, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.1)
        self.fail(f"timed out waiting for {what}; status={self.status()}")

    def check(self):
        self.call("CheckNow")
        self.wait_for(lambda: not self.status()["checking"], "probe to finish")

    def states(self):
        return {proc_state(p) for p in (self.geary_pid, self.bridge_pid, self.geary_child, self.fake_pid)}

    def confirm_outage(self):
        self.close_internet()
        self.check()
        self.check()
        self.wait_for(lambda: self.states() == {"T"}, "pause after outage")

    # ---------- the scenario ----------

    def test_outage_cycle(self):
        self.wait_for(lambda: self.status()["online"] is True, "first probe")
        self.assertEqual(self.status()["running"], {"bridge": 1, "geary": 2, "comm:fakeapp": 1})

        # One failed probe is not an outage.
        self.close_internet()
        self.check()
        self.assertIsNot(self.status()["online"], False)
        self.assertEqual(self.states(), {"S"})
        self.check()
        st = self.status()
        self.assertIs(st["online"], False)
        self.assertEqual(st["paused"], ["bridge", "comm:fakeapp", "geary"])
        self.assertEqual(self.states(), {"T"})

        # Screen on resumes, screen off pauses again without a new probe.
        self.set_screen(True)
        self.wait_for(lambda: self.states() == {"S"}, "resume on screen on")
        self.set_screen(False)
        self.wait_for(lambda: self.states() == {"T"}, "pause on screen off")

        # Unticking an app resumes just that app.
        self.call("SetConfig", json.dumps({"targets": {"geary": False}}))
        self.wait_for(lambda: proc_state(self.geary_pid) == "S", "geary resumed")
        self.assertEqual(proc_state(self.bridge_pid), "T")
        self.call("SetConfig", json.dumps({"targets": {"geary": True}}))
        self.wait_for(lambda: self.states() == {"T"}, "geary paused again")

        # A killed daemon leaves processes stopped; --resume-all recovers them.
        self.daemon.kill()
        self.daemon.wait()
        self.assertEqual(self.states(), {"T"})
        subprocess.run([*DAEMON, "--resume-all"], env=self.env(self.parent.pid), check=True)
        self.assertEqual(self.states(), {"S"})

        # Restarted daemon: confirm outage, then the user resumes for a while.
        self.start_daemon(scope=self.parent.pid)
        self.check()
        self.check()
        self.wait_for(lambda: self.states() == {"T"}, "paused after restart")
        self.call("ResumeNow")
        self.wait_for(lambda: self.states() == {"S"}, "resumed by user")
        self.assertGreater(self.status()["hold_until"], time.time())

        # Internet returns: stays running and the log tells the story.
        self.open_internet(self.port)
        self.check()
        self.assertIs(self.status()["online"], True)
        events = json.loads(self.call("GetEvents"))
        kinds = [e["kind"] for e in events]
        self.assertIn("offline", kinds)
        self.assertIn("pause", kinds)
        self.assertIn("online", kinds)
        resumes = [e for e in events if e["kind"] == "resume" and e.get("duration") is not None]
        self.assertTrue(resumes)

        self.call("ClearEvents")
        self.assertEqual(json.loads(self.call("GetEvents")), [])

    def test_sigterm_resumes(self):
        self.wait_for(lambda: self.status()["online"] is True, "first probe")
        self.confirm_outage()
        self.daemon.terminate()
        self.daemon.wait(10)
        self.assertEqual(self.states(), {"S"})
        with open(os.path.join(self.home, ".local/state/tweak-flx1s/offline-pause/events.json")) as f:
            events = json.load(f)
        self.assertEqual(events[-1]["text"], 'Resumed: service stopped')

    def test_sigkill_recovery_closes_the_history(self):
        self.wait_for(lambda: self.status()["online"] is True, "first probe")
        self.confirm_outage()
        self.daemon.kill()
        self.daemon.wait(10)
        self.assertEqual(self.states(), {"T"})
        # What ExecStopPost runs.
        subprocess.run(DAEMON + ["--resume-all"], env=self.env(self.parent.pid),
                       stdout=subprocess.DEVNULL, check=True)
        self.assertEqual(self.states(), {"S"})
        self.start_daemon(scope=self.parent.pid)
        events = json.loads(self.call("GetEvents"))
        recovered = [e for e in events if e["kind"] == "resume"]
        self.assertEqual([e["text"] for e in recovered], ['Resumed: service stopped unexpectedly'])
        self.assertGreater(recovered[0]["duration"], 0)

    def test_disabled_never_pauses(self):
        self.wait_for(lambda: self.status()["online"] is True, "first probe")
        self.call("SetConfig", json.dumps({"enabled": False}))
        self.close_internet()
        self.check()
        self.check()
        self.assertIs(self.status()["online"], False)
        self.assertEqual(self.states(), {"S"})
        self.call("SetConfig", json.dumps({"enabled": True}))
        self.wait_for(lambda: self.states() == {"T"}, "pause once enabled")


if __name__ == "__main__":
    unittest.main()
