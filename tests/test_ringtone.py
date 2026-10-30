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
"""Audio routing regressions using an in-memory PulseAudio server model."""
import copy
import os
import tempfile
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from tweak_flx1s.services import ringtone as rv
from tweak_flx1s.system import ringtone as config


def volume(value):
    values = value if isinstance(value, list) else [value]
    return {str(i): {"value": v} for i, v in enumerate(values)}


def stream(index, value=65536, ring=False, sink=1):
    return {"index": index, "sink": sink, "client": index + 100,
            "volume": volume(value), "properties": {
                "event.id": "phone-incoming-call" if ring else "media",
                "application.name": "feedbackd" if ring else "player",
                "application.process.id": str(index + 1000)}}


class RingerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sinks = [{"index": 1, "name": "speaker", "mute": False,
                       "volume": volume(rv.NORM // 4)}]
        self.inputs = [stream(1), stream(2, ring=True)]
        self.level = 80
        self.card = "default"
        self.tones = set()
        self.priority = False
        self.patches = [
            patch.object(rv, "custom_ringtones", side_effect=lambda: self.tones),
            patch.object(rv, "priority_call", side_effect=lambda: self.priority),
            patch.object(rv, "STATE_PATH", os.path.join(self.tmp.name, "state.json")),
            patch.object(rv, "get_volume", side_effect=lambda: self.level),
            patch.object(rv, "pactl_json", side_effect=self.query),
            patch.object(rv, "pactl", side_effect=self.command)]
        for item in self.patches:
            item.start()
        self.ringer = rv.Ringer()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def query(self, verb, kind):
        if kind == "cards":
            return [{"active_profile": self.card}]
        return copy.deepcopy(self.sinks if kind == "sinks" else self.inputs)

    def command(self, verb, index, *values):
        if verb == "set-sink-volume":
            target = next(s for s in self.sinks if s["name"] == index)
        else:
            target = next((s for s in self.inputs if s["index"] == int(index)), None)
            if target is None:
                raise subprocess.CalledProcessError(1, "pactl")
        target["volume"] = volume([int(v) for v in values])

    def settle(self):
        self.ringer.deadline = time.monotonic() - 1
        self.ringer.expire()

    def sink_set(self, fraction):
        """Another program, such as Telephony, writes the sink."""
        self.sinks[0]["volume"] = volume(int(rv.NORM * fraction))
        self.ringer.sink_changed()

    def test_raise_and_restore(self):
        self.ringer.add(self.inputs[1])
        sink = rv.channels(self.sinks[0])[0]
        media = rv.channels(self.inputs[0])[0]
        self.assertEqual(sink, self.level * rv.NORM // 100)
        self.assertAlmostEqual(sink * media / rv.NORM, rv.NORM // 4, delta=1)
        self.assertLessEqual(rv.channels(self.inputs[1])[0], rv.NORM)
        self.inputs.pop(1)
        self.ringer.removed(2)
        self.settle()
        self.assertEqual(rv.channels(self.sinks[0]), [rv.NORM // 4])
        self.assertEqual(rv.channels(self.inputs[0]), [rv.NORM])
        self.assertFalse(os.path.exists(rv.STATE_PATH))

    def test_stream_vanishing_during_apply_is_harmless(self):
        self.inputs.append(stream(3))
        self.ringer.add(self.inputs[1])
        self.ringer.add(self.inputs[2])
        self.inputs = [s for s in self.inputs if s["index"] != 1]
        self.level = 50
        self.ringer.apply()
        self.assertEqual(rv.channels(self.sinks[0]), [50 * rv.NORM // 100])
        self.assertEqual(rv.channels(self.inputs[1])[0], rv.NORM // 2)
        self.ringer.removed(1)
        self.assertIsNotNone(self.ringer.session)

    def test_vanished_ring_during_add_restores_on_remove(self):
        ring = self.inputs.pop(1)
        self.ringer.add(ring)
        self.ringer.removed(2)
        self.settle()
        self.assertIsNone(self.ringer.session)
        self.assertEqual(rv.channels(self.sinks[0]), [rv.NORM // 4])

    def test_lower_ring_does_not_raise_sink(self):
        self.level = 10
        self.ringer.add(self.inputs[1])
        self.assertEqual(rv.channels(self.sinks[0]), [rv.NORM // 4])
        self.assertAlmostEqual(rv.channels(self.inputs[1])[0] / rv.NORM, 0.4, delta=0.0001)

    def test_zero_media_and_master_mute(self):
        self.sinks[0]["volume"] = volume(0)
        self.ringer.add(self.inputs[1])
        self.assertEqual(rv.channels(self.sinks[0]), [self.level * rv.NORM // 100])
        self.assertEqual(rv.channels(self.inputs[0]), [0])
        self.ringer.stop()
        self.assertEqual(rv.channels(self.sinks[0]), [0])
        self.sinks[0]["mute"] = True
        self.ringer.add(self.inputs[1])
        self.assertIsNone(self.ringer.session)

    def test_multiple_rings_restore_after_last(self):
        self.ringer.add(self.inputs[1])
        self.inputs.append(stream(3, ring=True))
        self.ringer.add(self.inputs[-1])
        self.inputs = [s for s in self.inputs if s["index"] != 2]
        self.ringer.removed(2)
        self.assertIsNotNone(self.ringer.session)
        self.inputs = [s for s in self.inputs if s["index"] != 3]
        self.ringer.removed(3)
        self.settle()
        self.assertIsNone(self.ringer.session)
        self.assertEqual(rv.channels(self.sinks[0]), [rv.NORM // 4])

    def test_new_media_and_live_level_change(self):
        self.ringer.add(self.inputs[1])
        self.inputs.append(stream(3, rv.NORM // 2))
        self.ringer.add(self.inputs[-1])
        self.level = 50
        self.ringer.apply()
        self.assertEqual(rv.channels(self.inputs[-1]), [rv.NORM // 4])
        self.ringer.stop()
        self.assertEqual(rv.channels(self.inputs[-1]), [rv.NORM // 2])

    def test_recovery_skips_reused_stream_ids(self):
        self.ringer.add(self.inputs[1])
        self.inputs[0]["properties"]["application.process.id"] = "different"
        before = rv.channels(self.inputs[0])
        rv.Ringer().recover()
        self.assertEqual(rv.channels(self.sinks[0]), [rv.NORM // 4])
        self.assertEqual(rv.channels(self.inputs[0]), before)

    def test_other_sink_is_untouched(self):
        self.ringer.add(self.inputs[1])
        other = stream(9, sink=2)
        self.inputs.append(other)
        self.ringer.add(other)
        self.assertEqual(rv.channels(other), [rv.NORM])

    def test_ringtone_repeat_keeps_the_session(self):
        self.ringer.add(self.inputs[1])
        raised = rv.channels(self.sinks[0])
        self.inputs.pop(1)
        self.ringer.removed(2)
        self.assertEqual(rv.channels(self.sinks[0]), raised)
        self.inputs.append(stream(3, ring=True))
        self.ringer.add(self.inputs[-1])
        self.assertIsNone(self.ringer.deadline)
        self.assertEqual(rv.channels(self.sinks[0]), raised)

    def test_volume_boost_is_left_alone(self):
        self.ringer.sink_changed()
        self.sink_set(1)
        self.ringer.add(self.inputs[1])
        self.assertIsNone(self.ringer.session)
        self.assertEqual(rv.channels(self.inputs[1]), [rv.NORM])
        self.inputs.pop(1)
        self.ringer.removed(2)
        self.inputs.append(stream(3, ring=True))
        self.ringer.add(self.inputs[-1])
        self.assertEqual(rv.channels(self.inputs[-1]), [rv.NORM])
        self.inputs.pop()
        self.ringer.removed(3)
        self.sink_set(0.25)
        self.settle()
        self.assertEqual(rv.channels(self.sinks[0]), [rv.NORM // 4])
        self.assertIsNone(self.ringer.passive)

    def test_priority_caller_at_full_media_volume_is_left_alone(self):
        self.sinks[0]["volume"] = volume(rv.NORM)
        self.priority = True
        self.ringer.add(self.inputs[1])
        self.assertIsNone(self.ringer.session)
        self.assertEqual(rv.channels(self.inputs[1]), [rv.NORM])

    def test_ring_after_stale_boost_is_managed(self):
        self.ringer.sink_changed()
        self.sink_set(1)
        self.ringer.boosts[1] -= rv.BOOST_SECONDS + 1
        self.ringer.add(self.inputs[1])
        self.assertIsNotNone(self.ringer.session)

    def test_answered_call_restores_after_telephony(self):
        self.ringer.add(self.inputs[1])
        raised = max(rv.channels(self.sinks[0]))
        self.inputs.pop(1)
        self.ringer.removed(2)
        self.card = "voicecall"
        self.ringer.card_changed()
        self.assertEqual(rv.channels(self.inputs[0]), [rv.NORM])
        self.sink_set(0.8)
        self.assertEqual(self.ringer.session["phase"], "call")
        self.card = "default"
        self.ringer.card_changed()
        self.assertEqual(rv.channels(self.sinks[0]), [int(rv.NORM * 0.8)])
        self.sinks[0]["volume"] = volume(raised)
        self.ringer.sink_changed()
        self.assertEqual(rv.channels(self.sinks[0]), [rv.NORM // 4])
        self.assertIsNone(self.ringer.session)

    def test_call_end_times_out_without_telephony_restore(self):
        self.ringer.add(self.inputs[1])
        self.inputs.pop(1)
        self.card = "voicecall"
        self.ringer.removed(2)
        self.assertEqual(self.ringer.session["phase"], "call")
        self.card = "default"
        self.ringer.card_changed()
        self.settle()
        self.assertEqual(rv.channels(self.sinks[0]), [rv.NORM // 4])

    def test_answered_call_without_raised_sink_needs_no_restore(self):
        self.level = 10
        self.ringer.add(self.inputs[1])
        self.card = "voicecall"
        self.ringer.card_changed()
        self.assertIsNone(self.ringer.session)
        self.assertEqual(rv.channels(self.inputs[1]), [rv.NORM])

    def test_shutdown_during_call_keeps_saved_level(self):
        self.ringer.add(self.inputs[1])
        self.card = "voicecall"
        self.ringer.card_changed()
        self.sink_set(0.8)
        self.ringer.shutdown()
        self.assertEqual(rv.channels(self.sinks[0]), [int(rv.NORM * 0.8)])
        self.assertTrue(os.path.exists(rv.STATE_PATH))
        restarted = rv.Ringer()
        restarted.recover()
        self.assertEqual(restarted.session["phase"], "call")
        self.card = "default"
        restarted.card_changed()
        self.sinks[0]["volume"] = volume(self.level * rv.NORM // 100)
        restarted.sink_changed()
        self.assertEqual(rv.channels(self.sinks[0]), [rv.NORM // 4])

    def test_custom_contact_tone_is_a_ring(self):
        tone = stream(5)
        tone["properties"] = {"application.name": "feedbackd", "media.filename": "/tone.ogg"}
        self.assertFalse(rv.is_ring(tone))
        self.tones = {"/tone.ogg"}
        self.assertTrue(rv.is_ring(tone))
        self.inputs.append(tone)
        self.ringer.add(tone)
        self.assertIn("5", self.ringer.session["rings"])

    def test_ring_during_call_is_ignored(self):
        self.card = "voicecall"
        self.ringer.add(self.inputs[1])
        self.assertIsNone(self.ringer.session)
        self.assertEqual(rv.channels(self.sinks[0]), [rv.NORM // 4])


class TelephonyLookupTest(unittest.TestCase):
    def check(self, ringing, listed, expected):
        with patch.object(config, "incoming_numbers", return_value=ringing), \
                patch.object(config, "telephony_list", return_value=listed):
            self.assertEqual(config.priority_call(), expected)

    def test_priority_numbers(self):
        self.check(["+491701234567"], [{"number": "01701234567"}], True)
        self.check(["+491701234567"], [{"number": "+491709999999"}], False)
        self.check([], [{"number": "+491701234567"}], False)
        self.check(["+491701234567"], [{"number": ""}, "junk"], False)

    def test_custom_tone_paths(self):
        with patch.object(config, "telephony_list",
                          return_value=[{"number": "1", "path": "/a.ogg"}, {"number": "2"}, 3]):
            self.assertEqual(config.custom_ringtones(), {"/a.ogg"})


class ConfigTest(unittest.TestCase):
    def test_volume_validation_and_save(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(config, "CONFIG_DIR", temp), \
                patch.object(config, "CONFIG_PATH", os.path.join(temp, "ringtone.json")):
            for content in ('{"volume": "NaN"}', '{"volume": -1}', 'bad', '{}'):
                with open(config.CONFIG_PATH, "w") as f:
                    f.write(content)
                self.assertEqual(config.get_volume(), 0 if '-1' in content else 100)
            config.set_volume(145, notify=False)
            self.assertEqual(config.get_volume(), 100)


class EventLoopTest(unittest.TestCase):
    def test_hup_and_term_wake_the_blocked_subscription(self):
        code = '''
import os, signal, subprocess, threading
from types import SimpleNamespace
from unittest.mock import patch
from tweak_flx1s.services import ringtone as rv
original_popen = subprocess.Popen
def subscribe(*args, **kwargs):
    return original_popen(["sleep", "30"], stdout=subprocess.PIPE)
with patch.object(rv.subprocess, "run", return_value=SimpleNamespace(returncode=1)), \\
     patch.object(rv.subprocess, "Popen", side_effect=subscribe), \\
     patch.object(rv, "pactl_json", return_value=[]), \\
     patch.object(rv.Ringer, "recover"):
    threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGHUP)).start()
    threading.Timer(0.4, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    rv.run()
print("clean exit")
'''
        result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clean exit", result.stdout)
