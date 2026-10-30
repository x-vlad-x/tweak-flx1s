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
"""Independent ringtone volume settings."""
import json
import math
import os
import re
import subprocess

from tweak_flx1s.const import CONFIG_DIR, SERVICE_RINGTONE

CONFIG_PATH = os.path.join(CONFIG_DIR, "ringtone.json")
FEEDBACK_SCHEMA = "org.sigxcpu.feedbackd"
TELEPHONY_SCHEMA = "io.furios.Telephony"
TELEPHONY_BUS_NAME = "io.furios.Telephony.Daemon"
TELEPHONY_OBJECT = "/io/furios/Telephony/Daemon"
TELEPHONY_TIMEOUT_MS = 500


def feedback_profile():
    """The active feedbackd profile, or None when feedbackd is not installed."""
    from gi.repository import Gio
    source = Gio.SettingsSchemaSource.get_default()
    if not source or not source.lookup(FEEDBACK_SCHEMA, True):
        return None
    return Gio.Settings.new(FEEDBACK_SCHEMA).get_string("profile")


def telephony_list(key):
    """One JSON list setting of FuriOS Telephony, or [] on other systems."""
    from gi.repository import Gio
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(TELEPHONY_SCHEMA, True) if source else None
    if not schema or not schema.has_key(key):
        return []
    try:
        value = json.loads(Gio.Settings.new(TELEPHONY_SCHEMA).get_string(key))
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def custom_ringtones():
    """Sound files Telephony plays for contacts with their own ringtone."""
    return {tone.get("path") for tone in telephony_list("notification-override-call-custom-contacts")
            if isinstance(tone, dict) and tone.get("path")}


def incoming_numbers():
    """Numbers of the calls Telephony currently reports as ringing."""
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(TELEPHONY_BUS_NAME, TELEPHONY_OBJECT, TELEPHONY_BUS_NAME,
                              "GetActiveCalls", None, None, Gio.DBusCallFlags.NO_AUTO_START,
                              TELEPHONY_TIMEOUT_MS, None)
        calls = json.loads(reply.unpack()[0])
    except (GLib.Error, ValueError):
        return []
    return [call.get("number") for call in calls
            if call.get("state") in ("incoming", "waiting") and call.get("number")]


def number_key(number):
    """Compare numbers by their last digits, ignoring country-code style."""
    return re.sub(r"\D", "", str(number))[-9:]


def priority_call():
    """True when a ringing caller is on Telephony's always-ring list."""
    numbers = {number_key(number) for number in incoming_numbers()}
    numbers.discard("")
    if not numbers:
        return False
    return any(isinstance(contact, dict) and number_key(contact.get("number", "")) in numbers
               for contact in telephony_list("notification-override-dnd-bypass-contacts"))


def get_volume():
    try:
        with open(CONFIG_PATH) as f:
            value = float(json.load(f)["volume"])
        if not math.isfinite(value):
            return 100
        return int(min(100, max(0, value)))
    except (OSError, ValueError, KeyError, TypeError):
        return 100


def set_volume(value, notify=True):
    value = int(min(100, max(0, value)))
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH + ".tmp", "w") as f:
        json.dump({"volume": value}, f)
    os.replace(CONFIG_PATH + ".tmp", CONFIG_PATH)
    if notify:
        notify_daemon()


def notify_daemon():
    subprocess.run(["systemctl", "--user", "kill", "--kill-whom=main", "-s", "HUP",
                    SERVICE_RINGTONE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=10, check=False)
