# Independent ringtone volume

Enable **Tweaks → Audio → Independent Ringtone Volume**, then open **Ringtone
Volume → Configure**. Set a level from 0–100% and use **Test Ringtone** for a
six-second feedbackd preview. The package does not enable the service by default.

The monitor recognizes feedbackd's `phone-incoming-call` audio streams. If the
ringtone level exceeds media volume, it temporarily raises that sink and reduces
other streams to preserve their previous effective level. The ringtone itself
is never amplified above 100% at stream level. It also works when media volume
is zero. Master mute and the active feedback profile are respected: this is not
a silent-mode bypass.

The monitor also recognizes contact ringtones set in Telephony, which feedbackd
plays from a file without the `phone-incoming-call` label.

## Working with Telephony

Calls Telephony forces to full volume are left alone completely: always-ring
contacts and Repeated Calls Bypass ring at 100% even when this setting is lower,
and Telephony alone restores volume and mute afterwards. They are recognized by
the sink jumping to 100% just before the ringtone, or by the caller being on
Telephony's always-ring list. A repeated call while media volume is already at
100% causes no such jump and rings at this setting.

Telephony saves media volume when a call is answered, so it can save the raised
level. The monitor keeps the original level until the card leaves its voice-call
profile, then corrects the volume after Telephony's own restore. The daemon never
writes the sink during a call.

feedbackd opens a new stream on every ringtone repeat. A ring therefore ends one
second after its last stream, which also gives an answered call time to start.

The original sink and stream levels are restored after the ring or call ends, on
service shutdown, and after a daemon crash using `ExecStopPost` and a private
recovery file. If a call is still active, the file is kept and the restarted
service finishes the job. Reused stream IDs are checked against their original
identity. The daemon waits for PulseAudio events and signals without periodic
polling. It requires `pactl` with JSON support and a PulseAudio-compatible server;
the preview requires `fbcli` and feedbackd.

Settings are stored in `~/.config/tweak-flx1s/ringtone.json`, for example:

```json
{"volume": 80}
```

Volume changes are applied to an ongoing ringtone. New media streams are
compensated as they appear. Changing output routes or media volume while ringing
is not tracked as a new baseline; the service restores the pre-ring levels.
Only feedbackd incoming-call events are managed, not arbitrary Android app audio.

## Moving from the standalone ringtone-volume app

Stop and disable the old service before enabling the integrated one:

```sh
systemctl --user disable --now ringtone-volume.service
```

If the old daemon was interrupted while ringing, let its original daemon recover
the saved levels before disabling it. The integrated daemon uses its own
configuration and recovery files and refuses to start while the old service is
active. Set the desired volume in the new dialog; the old app and configuration
can be kept until the integrated version has been verified.

## Development checks

The test suite includes an in-memory audio server model. It covers raising and
lowering ringtone volume, preserving media levels, zero media volume, master mute,
multiple overlapping ringtone streams, ringtone repeats, newly created streams,
live changes, forced Telephony calls, answered calls, custom contact tones and
crash recovery with stream ID reuse. Run the suite as described in
[power-tools.md](power-tools.md#development-checks).
