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
"""Keep feedbackd incoming-call volume independent of media volume.

Raise the sink only while ringing; attenuate other streams to preserve their
effective level. Stream volumes never exceed 100%. Master mute is respected.

FuriOS Telephony also owns this sink: it forces 100% for always-ring contacts
and repeated calls, and saves and restores media volume around every answered
call. Calls it forces are left alone completely. For answered calls the original
level is written back only after Telephony has finished restoring its own copy.
feedbackd opens a new stream on each ringtone repeat, so a short gap between
streams does not end a ring.
"""
import json
import os
import re
import selectors
import signal
import subprocess
import time

from tweak_flx1s.const import CACHE_DIR
from tweak_flx1s.system.ringtone import custom_ringtones, get_volume, priority_call
from tweak_flx1s.utils import logger

NORM = 65536
RING_EVENT = "phone-incoming-call"
RING_GAP_SECONDS = 1.0  # also the time Telephony gets to start an answered call
CALL_END_SECONDS = 2.0  # upper limit for Telephony to restore its saved volume
BOOST_SECONDS = 1.5  # a jump to 100% this recent belongs to the ringing call
STATE_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR", CACHE_DIR),
                          "tweak-flx1s-ringtone.json")
ENV = dict(os.environ, LC_ALL="C")


def pactl(*args):
    subprocess.run(["pactl", *args], env=ENV, check=True, capture_output=True, timeout=10)


def pactl_json(*args):
    result = subprocess.run(["pactl", "-f", "json", *args], env=ENV,
                            check=True, capture_output=True, text=True, timeout=10)
    return json.loads(result.stdout)


def channels(item):
    return [channel["value"] for channel in item["volume"].values()]


def identity(stream):
    props = stream.get("properties", {})
    return [stream.get("client"), props.get("object.serial"),
            props.get("application.process.id"), props.get("application.name"),
            props.get("media.name"), props.get("event.id")]


def is_ring(stream):
    """Custom contact tones carry a file name instead of the event id."""
    props = stream.get("properties", {})
    if props.get("event.id") == RING_EVENT:
        return True
    return (props.get("application.name") == "feedbackd"
            and props.get("media.filename") in custom_ringtones())


def in_voice_call():
    return any(str(card.get("active_profile", "")).startswith("voicecall")
               for card in pactl_json("list", "cards"))


def set_input(index, values):
    """Set one stream's volume. Streams end at any moment, so a failure only
    costs that stream; the remove event then drops it from the session."""
    try:
        pactl("set-sink-input-volume", str(index), *(str(int(v)) for v in values))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.debug(f"Skipped sink input {index}: {exc}")


class Ringer:
    def __init__(self):
        self.session = None
        self.passive = None  # ring streams of a call Telephony forced to 100%
        self.deadline = None
        self.levels = {}
        self.boosts = {}

    def save(self):
        if self.session:
            os.makedirs(os.path.dirname(STATE_PATH), mode=0o700, exist_ok=True)
            with open(os.open(STATE_PATH + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                              0o600), "w") as f:
                json.dump(self.session, f)
            os.replace(STATE_PATH + ".tmp", STATE_PATH)
        else:
            try:
                os.unlink(STATE_PATH)
            except FileNotFoundError:
                pass

    def timeout(self):
        if self.deadline is None:
            return None
        return max(0, self.deadline - time.monotonic())

    def expire(self):
        if self.deadline is None or time.monotonic() < self.deadline:
            return
        self.deadline = None
        self.passive = None
        self.stop()

    def apply(self):
        s = self.session
        if not s or s["phase"] in ("call", "ended"):
            return
        ring = get_volume() * NORM // 100
        effective = [max(v, ring) for v in s["sink_volume"]]
        s["effective"] = effective
        # Quiet other streams before raising the hardware sink.
        for idx, item in s["others"].items():
            original = item["volume"]
            values = [v * s["sink_volume"][min(c, len(effective) - 1)]
                      // max(1, effective[min(c, len(effective) - 1)])
                      for c, v in enumerate(original)]
            set_input(idx, values)
        pactl("set-sink-volume", s["sink"], *(str(v) for v in effective))
        for idx, item in s["rings"].items():
            set_input(idx, [ring * NORM // max(1, effective[min(c, len(effective) - 1)])
                            for c in range(len(item["volume"]))])

    def sink_changed(self):
        """Note jumps to 100% and Telephony's post-call volume restore."""
        for sink in pactl_json("list", "sinks"):
            index = sink["index"]
            values = channels(sink)
            level = max(values)
            previous = self.levels.get(index)
            self.levels[index] = level
            s = self.session
            if s and s["sink_index"] == index:
                if s["phase"] == "ended" and level == max(s.get("effective") or [-1]):
                    self.stop()
            elif previous is not None and previous < NORM <= level:
                self.boosts[index] = time.monotonic()

    def forced(self, sink_index):
        boosted = self.boosts.get(sink_index)
        if boosted is not None and time.monotonic() - boosted < BOOST_SECONDS:
            return True
        return priority_call()

    def card_changed(self):
        voice = in_voice_call()
        s = self.session
        if voice:
            if self.passive is not None:
                self.passive = None
                self.deadline = None
            if s and s["phase"] in ("ringing", "settling"):
                self.call_started()
        elif s and s["phase"] == "call":
            s["phase"] = "ended"
            self.deadline = time.monotonic() + CALL_END_SECONDS
            self.save()

    def call_started(self):
        """Put streams back now; the sink waits until the call has ended."""
        s = self.session
        self.deadline = None
        alive = {str(item["index"]): item for item in pactl_json("list", "sink-inputs")}
        for idx, item in {**s["others"], **s["rings"]}.items():
            if idx in alive and identity(alive[idx]) == item["identity"]:
                set_input(idx, item["volume"])
        s["rings"] = {}
        s["others"] = {}
        s["phase"] = "call"
        if s.get("effective") == s["sink_volume"]:
            self.session = None
        self.save()

    def add(self, stream):
        ring = is_ring(stream)
        index = stream["index"]
        if self.passive is not None:
            if ring:
                self.passive.add(index)
                self.deadline = None
            return
        if self.session and self.session["phase"] in ("call", "ended"):
            return
        if not self.session:
            if not ring or in_voice_call():
                return
            if self.forced(stream["sink"]):
                logger.info("Telephony forced this call to full volume; leaving it alone")
                self.passive = {index}
                return
            sink = next((s for s in pactl_json("list", "sinks")
                         if s["index"] == stream["sink"]), None)
            if not sink or sink["mute"]:
                return
            self.session = {"sink": sink["name"], "sink_index": sink["index"],
                            "sink_volume": channels(sink), "phase": "ringing",
                            "rings": {}, "others": {}}
            for other in pactl_json("list", "sink-inputs"):
                if other["sink"] == sink["index"] and other["index"] != index:
                    target = "rings" if is_ring(other) else "others"
                    self.session[target][str(other["index"])] = {
                        "volume": channels(other), "identity": identity(other)}
        if stream["sink"] != self.session["sink_index"]:
            return
        if ring:
            self.session["phase"] = "ringing"
            self.deadline = None
        idx = str(index)
        if idx in self.session["rings"] or idx in self.session["others"]:
            return
        self.session["rings" if ring else "others"][idx] = {
            "volume": channels(stream), "identity": identity(stream)}
        self.save()
        self.apply()

    def removed(self, index):
        if self.passive is not None:
            self.passive.discard(index)
            if not self.passive:
                self.deadline = time.monotonic() + RING_GAP_SECONDS
            return
        if not self.session:
            return
        s = self.session
        s["rings"].pop(str(index), None)
        s["others"].pop(str(index), None)
        if s["rings"] or s["phase"] != "ringing":
            self.save()
        elif in_voice_call():
            self.call_started()
        else:
            s["phase"] = "settling"
            self.deadline = time.monotonic() + RING_GAP_SECONDS
            self.save()

    def stop(self):
        s = self.session
        self.deadline = None
        self.passive = None
        if not s:
            return
        sinks = pactl_json("list", "sinks")
        if any(item["name"] == s["sink"] for item in sinks):
            # Restore the sink first, avoiding a transient boost of media.
            pactl("set-sink-volume", s["sink"], *(str(v) for v in s["sink_volume"]))
        alive = {str(item["index"]): item for item in pactl_json("list", "sink-inputs")}
        for idx, item in {**s["others"], **s["rings"]}.items():
            if idx in alive and identity(alive[idx]) == item["identity"]:
                set_input(idx, item["volume"])
        self.session = None
        self.save()

    def shutdown(self):
        """Keep the saved level on disk if a call still owns the sink."""
        if self.session and self.session["phase"] == "call" and in_voice_call():
            return
        self.stop()

    def recover(self):
        try:
            with open(STATE_PATH) as f:
                self.session = json.load(f)
        except FileNotFoundError:
            return
        self.session.setdefault("phase", "ringing")
        if in_voice_call():
            self.call_started()
        else:
            self.stop()


def restore():
    Ringer().recover()


def run():
    legacy = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "ringtone-volume.service"],
                            capture_output=True, timeout=10)
    if legacy.returncode == 0:
        logger.error("Stop the standalone ringtone-volume service before enabling Independent Ringtone Volume")
        raise SystemExit(78)
    ringer = Ringer()
    wake_read, wake_write = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
    old_wakeup = signal.set_wakeup_fd(wake_write)
    handlers = {}
    stopped = False
    reapply = False
    def on_signal(sig, frame):
        nonlocal stopped, reapply
        if sig == signal.SIGHUP:
            reapply = True
        else:
            stopped = True
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        handlers[sig] = signal.signal(sig, on_signal)
    proc = None
    try:
        proc = subprocess.Popen(["pactl", "subscribe"], env=ENV, stdout=subprocess.PIPE)
        with selectors.DefaultSelector() as selector:
            selector.register(wake_read, selectors.EVENT_READ)
            selector.register(proc.stdout, selectors.EVENT_READ)
            ringer.recover()
            ringer.sink_changed()
            for stream in pactl_json("list", "sink-inputs"):
                ringer.add(stream)
            buf = b""
            pattern = re.compile(rb"Event '(new|remove|change)' on (sink-input|sink|card) #(\d+)")
            while not stopped:
                for key, _events in selector.select(ringer.timeout()):
                    if key.fileobj == wake_read:
                        os.read(wake_read, 4096)
                        if reapply:
                            reapply = False
                            ringer.apply()
                        continue
                    chunk = os.read(proc.stdout.fileno(), 4096)
                    if not chunk:
                        raise RuntimeError("PulseAudio event subscription ended")
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for line in lines:
                        match = pattern.search(line)
                        if not match:
                            continue
                        event, facility = match.group(1), match.group(2)
                        index = int(match.group(3))
                        if facility == b"card":
                            if event == b"change":
                                ringer.card_changed()
                        elif facility == b"sink":
                            if event == b"change":
                                ringer.sink_changed()
                        elif event == b"remove":
                            ringer.removed(index)
                        elif event == b"new":
                            stream = next((s for s in pactl_json("list", "sink-inputs")
                                           if s["index"] == index), None)
                            if stream:
                                ringer.add(stream)
                ringer.expire()
    finally:
        try:
            ringer.shutdown()
        finally:
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                proc.stdout.close()
            signal.set_wakeup_fd(old_wakeup)
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
            os.close(wake_read)
            os.close(wake_write)
