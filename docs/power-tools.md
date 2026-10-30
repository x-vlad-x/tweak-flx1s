# Offline pause and power tools

Open **Tweaks → Offline Pause and Power Tools → Configure**. The three pages
cover offline pause, system power tools, and online account synchronization.
All controls run as the logged-in user and use the existing FuriOS permissions.

## Offline pause

Enable the **Offline Pause** service in Tweaks or use **Start** in its dialog.
The package does not enable it automatically. Choose the apps to pause; Proton
Bridge and Geary are supported directly, and **Add Application** lists running
user apps. Shell, audio, telephony, keyring and session infrastructure are excluded.

The service checks DNS and TCP connectivity to the configured hosts. Defaults
are `mail-api.proton.me:443` and `imap.gmail.com:993`; it sends no application
data. After two failed checks it sends SIGSTOP to selected apps and their
children. One successful check, waking the screen, or **Resume** releases them.
The screen-off restriction is enabled by default. Disable it only if you accept
that paused windows will stop responding while offline.

Configuration is in `~/.config/tweak-flx1s/offline-pause/config.json`; events and
the recovery ledger are in `~/.local/state/tweak-flx1s/offline-pause/`.
`probe_hosts` in the configuration file can be changed while the service is
stopped. Online and offline intervals are configurable in the dialog. Network
and screen changes also trigger checks, without fast polling.

Before each SIGSTOP, a recovery record is written with the PID, process start
time and boot ID. Normal shutdown, `ExecStopPost`, and startup recover recorded
processes, and a recovery after a crash is added to the history. Recovery never
resumes unrelated stopped processes.

## System and accounts

The System page can stop/start Andromeda, inspect idle inhibitors and orphaned
processes, configure batman's screen-off cores, select Phosh notification wake
presets, and manage optional background services when installed. Termination and
inhibitor release happen only when requested.

Some FuriOS versions cannot restart Android's sensors after the container has
been stopped. The page checks the container's `Wants` and `LimitMEMLOCK` and shows
a notice when restart support is missing. If needed, an administrator can add
`/etc/systemd/system/andromeda-container.service.d/restart.conf`:

```ini
[Unit]
Wants=andromeda-sensors.service

[Service]
LimitMEMLOCK=infinity
```

Run `sudo systemctl daemon-reload` after adding the file. This package does not
install the override or change system-wide PID limits. Device-specific Android
fixes should be discussed separately with FuriOS upstream.

Starting an Android session also ends `xdg-document-portal`, with or without this
page. Flatpak apps start it again on demand; apps already running may need a
restart to reach their shared documents.

The Accounts page uses GNOME Online Accounts and Evolution Data Server's D-Bus
APIs to enable features and set per-source synchronization intervals. Missing
services are reported in the dialog; no account credentials are read or bundled.

## Moving from the standalone offline-pause app

Do not run both daemons. Stop and disable the standalone service first:

```sh
systemctl --user disable --now offline-pause.service
```

Its `ExecStopPost` resumes its paused apps. If that unit has been removed after
a crash, run its original `offline-pause --resume-all` before continuing.
The integrated daemon refuses to start while the standalone unit is active.

To keep existing settings, copy `~/.config/offline-pause/config.json` into
`~/.config/tweak-flx1s/offline-pause/` before enabling the new service. Keep the
old app and files until you have verified the integrated version. The old
service can be re-enabled after stopping the integrated one.

## Development checks

```sh
PYTHONPATH=src GIO_USE_VFS=local dbus-run-session -- python3 -m unittest discover -v tests
```

Offline integration tests use a private D-Bus session, temporary HOME, local TCP
listener and fake processes restricted by `OFFLINE_PAUSE_TEST_SCOPE`. They cover
outage confirmation, process trees, screen wake, configuration changes, SIGTERM
cleanup and SIGKILL recovery without pausing real user apps.
