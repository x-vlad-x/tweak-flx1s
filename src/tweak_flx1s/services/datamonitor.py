# Copyright (C) 2026 alaraajavamma aki@urheiluaki.fi
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

import signal
import gi
from gi.repository import GLib, Gio
try:
    gi.require_version('Lfb', '0.0')
    from gi.repository import Lfb
    _HAVE_LFB = True
except (ValueError, ImportError):
    # Missing libfeedback typelib: degrade to a silent notification rather
    # than crashing the daemon on import.
    _HAVE_LFB = False
from loguru import logger
from tweak_flx1s.core.datamonitor import DataMonitor
from tweak_flx1s.const import APP_ID, APP_NAME
from tweak_flx1s.utils import send_notification, get_default_sink_volume, set_sink_volume

_lfb_ready = False
# Keep a reference so a still-playing event is not garbage-collected.
_alert_event = None
# Saved state while the alert forces full profile / max volume.
_force_state = {"profile": None, "sink": None, "vol": None, "muted": None, "active": False}


def _ensure_lfb():
    global _lfb_ready
    if not _HAVE_LFB:
        return False
    if not _lfb_ready:
        try:
            Lfb.init(APP_ID)
            _lfb_ready = True
        except Exception as e:
            logger.error(f"Lfb init failed: {e}")
    return _lfb_ready


def _trigger_feedback(event_name):
    """Play a feedbackd event directly via libfeedback. This — not the
    notification — is what produces the sound (the approach the Telephony app
    uses). trigger_feedback_async needs a running GLib loop, which the daemon
    has; call this on the main thread only."""
    global _alert_event
    if not _ensure_lfb():
        return
    try:
        _alert_event = Lfb.Event.new(event_name)
        _alert_event.trigger_feedback_async(None, None, None)
        logger.info(f"Triggered feedback event: {event_name}")
    except Exception as e:
        logger.error(f"Trigger feedback failed: {e}")


def _get_feedbackd_profile():
    try:
        return Gio.Settings(schema_id="org.sigxcpu.feedbackd").get_string("profile")
    except Exception as e:
        logger.warning(f"Could not read feedbackd profile: {e}")
        return None


def _set_feedbackd_profile(name):
    try:
        Gio.Settings(schema_id="org.sigxcpu.feedbackd").set_string("profile", name)
    except Exception as e:
        logger.warning(f"Could not set feedbackd profile: {e}")


def _force_max_feedback(restore=False):
    """Override silent/quiet so the alert is heard: feedbackd profile -> full
    and the default sink -> max volume. restore=True puts the saved state back.
    Safe to call repeatedly; only the first call saves state. Returns False so
    it can be used directly as a one-shot GLib timeout callback."""
    if restore:
        if _force_state["profile"]:
            _set_feedbackd_profile(_force_state["profile"])
        if _force_state["sink"] and _force_state["vol"] is not None:
            set_sink_volume(_force_state["sink"], _force_state["vol"], _force_state["muted"])
        _force_state.update(profile=None, sink=None, vol=None, muted=None, active=False)
        return False
    if not _force_state["active"]:
        _force_state["profile"] = _get_feedbackd_profile()
        _force_state["sink"], _force_state["vol"], _force_state["muted"] = get_default_sink_volume()
        _force_state["active"] = True
    if _get_feedbackd_profile() != "full":
        _set_feedbackd_profile("full")
    if _force_state["sink"]:
        set_sink_volume(_force_state["sink"], 1.0, False)
    return False


def _send_notification_silent(title, body):
    """Visual notification only — the sound comes from _trigger_feedback, so we
    suppress the server's own sound (matches the Telephony approach)."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        hints = {
            "urgency": GLib.Variant("y", 2),
            "desktop-entry": GLib.Variant("s", APP_ID),
            "category": GLib.Variant("s", "im.received"),
            "suppress-sound": GLib.Variant("b", True),
            "sound-name": GLib.Variant("s", "none"),
        }
        params = GLib.Variant(
            "(susssasa{sv}i)",
            (APP_NAME, 0, APP_ID, title, body or "", [], hints, -1),
        )
        bus.call_sync(
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications",
            "Notify",
            params,
            GLib.VariantType("(u)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
    except Exception as e:
        logger.error(f"Feedback notification failed, falling back to plain: {e}")
        send_notification(title, body, id="data-limit-alert", urgent=True)


def notify_critical(title, body):
    """Limit alert that is seen AND heard even when the phone is silenced.

    Mirrors the Telephony app: force the feedbackd profile to full + sink to
    max volume, trigger the message-new-sms event via libfeedback (the actual
    source of the sound), show a sound-suppressed notification, then restore.
    Must run on the daemon's main thread — the loop drives libfeedback and the
    restore timeouts; check_limits already runs there.
    """
    _force_max_feedback()
    # Re-assert shortly after in case something resets profile/volume.
    GLib.timeout_add_seconds(1, lambda: _force_max_feedback())
    _trigger_feedback("message-new-sms")
    _send_notification_silent(title, body)
    # Restore the user's profile/volume after the sound has had time to play.
    GLib.timeout_add_seconds(5, lambda: _force_max_feedback(restore=True))

class DataMonitorDaemon:
    def __init__(self):
        self.monitor = DataMonitor()
        self.loop = GLib.MainLoop()
        self.cancellable = Gio.Cancellable()
        self.timer_id = 0
        self.nm_debounce_id = 0
        self.last_interval_min = 0
        self.limit_notified = False
        # None = unknown (first check after start), True/False = last known
        # restricted state. restore_connection() only runs on a transition
        # out of the restricted state, never as a periodic re-assert.
        self.was_restricted = None

    def start(self):
        logger.info("Starting Data Monitor Daemon...")

        # Run the first check on the loop's first iteration, not here: any
        # notification/feedback it raises is event-loop-driven and would be
        # dropped if fired before loop.run() (the nmcli disable is synchronous
        # and would still happen, so you'd get enforcement with no alert).
        GLib.idle_add(self._initial_check)

        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._on_quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._on_quit)

        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, self.cancellable)
            bus.signal_subscribe(
                "org.freedesktop.NetworkManager",
                "org.freedesktop.DBus.Properties",
                "PropertiesChanged",
                "/org/freedesktop/NetworkManager",
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_nm_changed,
                None
            )
            logger.info("Subscribed to NetworkManager D-Bus events successfully.")
        except Exception as e:
            logger.warning(f"Could not subscribe to NetworkManager D-Bus events: {e}")

        self._schedule_timer()

        try:
            self.loop.run()
        except KeyboardInterrupt:
            self._on_quit()

    def _initial_check(self):
        """First check, deferred onto the running loop (see start())."""
        try:
            self.check_limits()
        except Exception as e:
            logger.error(f"Failed to run initial check: {e}")
        return GLib.SOURCE_REMOVE

    def _schedule_timer(self):
        if self.timer_id > 0:
            GLib.source_remove(self.timer_id)

        self.monitor.config.load()
        interval_min = self.monitor.config.get("check_interval_min", 5)
        if interval_min < 1:
            interval_min = 1
        self.last_interval_min = interval_min

        self.timer_id = GLib.timeout_add_seconds(interval_min * 60, self._on_timer_check)

    def _on_timer_check(self):
        self.check_limits()
        self.monitor.config.load()
        current_interval = self.monitor.config.get("check_interval_min", 5)
        if current_interval < 1:
            current_interval = 1
        if current_interval != self.last_interval_min:
            self._schedule_timer()
            return False
        return True

    def _on_nm_changed(self, connection, sender_name, object_path, interface_name, signal_name, parameters, user_data):
        # NM emits PropertiesChanged in bursts during any connection churn.
        # Coalesce a burst into a single check instead of one check per signal.
        if self.nm_debounce_id > 0:
            return
        logger.info("NetworkManager state changed. Scheduling limit verification.")
        self.nm_debounce_id = GLib.timeout_add(1000, self._on_nm_debounced)

    def _on_nm_debounced(self):
        self.nm_debounce_id = 0
        self.check_limits()
        return GLib.SOURCE_REMOVE

    def check_limits(self):
        try:
            self.monitor.config.load()
            if not self.monitor.config.get("enabled", False):
                return

            if self.monitor.config.get("temporary_bypass", False):
                bypass_limit = self.monitor.config.get("bypass_limit_mb", 0)
                bypass_start = self.monitor.config.get("bypass_start_bytes", 0)
                if bypass_limit > 0 and bypass_start > 0:
                    current_bytes = self.monitor.get_current_total_bytes()
                    used_bytes = max(0, current_bytes - bypass_start)
                    used_mb = used_bytes / (1024 * 1024)
                    if used_mb >= bypass_limit:
                        logger.info(f"Bypass allowance exceeded ({used_mb:.1f} MB / {bypass_limit} MB). Disabling bypass.")
                        self.monitor.config.set("temporary_bypass", False)
                        title = "Bypass Limit Reached"
                        # Only claim the connection gets disabled when the
                        # configured action will actually do that below.
                        if self.monitor.config.get("action", "notify") == "disable":
                            body = f"Temporary bypass limit of {bypass_limit} MB has been exceeded. Mobile data will be disabled."
                        else:
                            body = f"Temporary bypass limit of {bypass_limit} MB has been exceeded. Limits are enforced again."
                        if self.monitor.config.get("bypass_silent", False):
                            notify_critical(title, body)
                        else:
                            send_notification(title, body)

            usage = self.monitor.get_usage()
            if not usage:
                return

            daily_used = usage["daily_mb"]
            weekly_used = usage["weekly_mb"]
            monthly_used = usage["monthly_mb"]

            daily_limit = self.monitor.config.get("daily_limit_mb", 1000)
            weekly_limit = self.monitor.config.get("weekly_limit_mb", 5000)
            monthly_limit = self.monitor.config.get("monthly_limit_mb", 20000)

            limit_exceeded = False
            exceeded_msg = ""

            if daily_used >= daily_limit:
                limit_exceeded = True
                exceeded_msg = f"Daily limit exceeded ({daily_used:.1f} MB / {daily_limit} MB)"
            elif weekly_used >= weekly_limit:
                limit_exceeded = True
                exceeded_msg = f"Weekly limit exceeded ({weekly_used:.1f} MB / {weekly_limit} MB)"
            elif monthly_used >= monthly_limit:
                limit_exceeded = True
                exceeded_msg = f"Monthly limit exceeded ({monthly_used:.1f} MB / {monthly_limit} MB)"

            action = self.monitor.config.get("action", "notify")
            bypass_silent = self.monitor.config.get("bypass_silent", False)
            over_limit_active = limit_exceeded and not self.monitor.config.get("temporary_bypass", False)

            # The app holds the connection down ONLY while a limit is exceeded
            # AND the action is "disable" (and not bypassed). In every other
            # state it must not be suppressing the connection.
            suppress = over_limit_active and action == "disable"

            if suppress:
                # Notify only when the link actually went down this check (not
                # on a roaming/autoconnect-only re-assert), so the "has been
                # disabled" alert always reflects a real disconnect.
                link_went_down = self.monitor.disable_connection()
                if link_went_down:
                    title = "Mobile Data Restricted"
                    body = f"{exceeded_msg}\nMobile data has been disabled. Please open Tweak-FLX1s to activate Limit Bypass or increase your limits."
                    if bypass_silent:
                        notify_critical(title, body)
                    else:
                        send_notification(title, body)
                self.was_restricted = True
            else:
                # Not suppressing. If we WERE (a previous disable took the link
                # down), bring it back — this covers switching disable->notify
                # while still over the limit, and the normal limits-cleared
                # case. was_restricted: None on first check (restore once),
                # True if we had suppressed, False if we never did (so we don't
                # fight a user who turned mobile data off by hand).
                if self.was_restricted is not False:
                    self.monitor.restore_connection()
                self.was_restricted = False

                if not limit_exceeded:
                    # Limits reset: a lingering bypass is moot, and if left set
                    # it would suppress enforcement in the next period. Clear it
                    # so the stored state matches reality.
                    if self.monitor.config.get("temporary_bypass", False):
                        logger.info("Limits no longer exceeded; clearing stale temporary bypass.")
                        self.monitor.config.set("temporary_bypass", False)
                    self.limit_notified = False
                elif over_limit_active:
                    # Over a limit but not disabling data (notify mode): warn once.
                    if not self.limit_notified:
                        title = "Mobile Data Limit Exceeded"
                        body = exceeded_msg
                        if bypass_silent:
                            notify_critical(title, body)
                        else:
                            send_notification(title, body)
                        self.limit_notified = True

        except Exception as e:
            logger.error(f"Error checking limits: {e}")

    def _on_quit(self):
        logger.info("Stopping Data Monitor Daemon...")
        self.cancellable.cancel()
        if self.timer_id > 0:
            GLib.source_remove(self.timer_id)
        if self.nm_debounce_id > 0:
            GLib.source_remove(self.nm_debounce_id)
            self.nm_debounce_id = 0
        if self.loop.is_running():
            self.loop.quit()
        return GLib.SOURCE_REMOVE

def run():
    daemon = DataMonitorDaemon()
    daemon.start()