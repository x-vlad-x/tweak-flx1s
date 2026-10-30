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
"""Online accounts: GNOME Online Accounts features and Evolution Data Server sources.

GOA decides which features of an account are on (mail, calendar, contacts,
files...). Evolution Data Server then creates one source per calendar, task
list, memo list and address book, and syncs each on its own [Refresh] timer.
Both are changed through their public D-Bus APIs, so the daemons persist the
change themselves.
"""
from gi.repository import Gio, GLib

GOA_NAME = "org.gnome.OnlineAccounts"
GOA_PATH = "/org/gnome/OnlineAccounts"
GOA_ACCOUNT = "org.gnome.OnlineAccounts.Account"
EDS_PREFIX = "org.gnome.evolution.dataserver.Sources"
EDS_PATH = "/org/gnome/evolution/dataserver/SourceManager"
EDS_SOURCE = "org.gnome.evolution.dataserver.Source"
EDS_WRITABLE = "org.gnome.evolution.dataserver.Source.Writable"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"

# ESourceRefresh defaults, used when a source has no [Refresh] group.
DEFAULT_REFRESH_ENABLED = True
DEFAULT_REFRESH_MINUTES = 60

# (property, D-Bus interface present while enabled, label)
FEATURES = [
    ("MailDisabled", "Mail", 'Mail'),
    ("CalendarDisabled", "Calendar", 'Calendars and Tasks'),
    ("ContactsDisabled", "Contacts", 'Contacts'),
    ("FilesDisabled", "Files", 'Files'),
    ("PhotosDisabled", "Photos", 'Photos'),
    ("MusicDisabled", "Music", 'Music'),
    ("ChatDisabled", "Chat", 'Chat'),
    ("TicketingDisabled", "Ticketing", 'Kerberos Tickets'),
]
# What each provider can offer, so a feature that is switched off (and has
# therefore lost its interface) is still listed.
PROVIDER_FEATURES = {
    "google": {"Mail", "Calendar", "Contacts", "Files", "Photos"},
    "owncloud": {"Calendar", "Contacts", "Files"},
    "webdav": {"Calendar", "Contacts", "Files"},
    "imap_smtp": {"Mail"},
    "exchange": {"Mail", "Calendar", "Contacts"},
    "ms_graph": {"Mail", "Calendar", "Contacts", "Files"},
    "kerberos": {"Ticketing"},
    "fedora": {"Ticketing"},
}

KINDS = [
    ("Calendar", 'Calendar'),
    ("Task List", 'Tasks'),
    ("Memo List", 'Notes'),
    ("Address Book", 'Contacts'),
]
SYNCED_BACKENDS = {"caldav", "carddav", "webdav", "google", "ews", "microsoft365",
                   "webdav-notes", "contacts-gtasks", "gtasks", "weather", "http", "webcal"}


def managed_objects(bus, name, path):
    reply = bus.call_sync(name, path, OBJECT_MANAGER, "GetManagedObjects", None,
                          GLib.VariantType("(a{oa{sa{sv}}})"), Gio.DBusCallFlags.NONE, 10000, None)
    return reply.unpack()[0]


def eds_service(bus):
    names = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "ListNames", None, None, Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    found = sorted(n for n in names if n.startswith(EDS_PREFIX))
    if found:
        return found[-1]
    # Not running yet: the highest activatable version.
    names = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "ListActivatableNames", None, None, Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    found = sorted(n for n in names if n.startswith(EDS_PREFIX))
    if not found:
        raise RuntimeError('Evolution Data Server not found')
    return found[-1]


def keyfile(data):
    kf = GLib.KeyFile()
    kf.load_from_data(data, len(data.encode()), GLib.KeyFileFlags.KEEP_COMMENTS)
    return kf


def kf_has(kf, group, key):
    try:
        kf.get_value(group, key)
        return True
    except GLib.Error:
        return False


def kf_get(kf, group, key, default=None, kind="string"):
    try:
        if kind == "bool":
            return kf.get_boolean(group, key)
        if kind == "int":
            return kf.get_integer(group, key)
        return kf.get_string(group, key)
    except GLib.Error:
        return default


class Source:
    def __init__(self, path, uid, data, writable):
        self.path, self.uid, self.data, self.writable = path, uid, data, writable
        kf = keyfile(data)
        self.name = kf_get(kf, "Data Source", "DisplayName", uid)
        self.enabled = kf_get(kf, "Data Source", "Enabled", True, "bool")
        self.parent = kf_get(kf, "Data Source", "Parent", "") or ""
        self.kind = next((k for k, _ in KINDS if kf.has_group(k)), None)
        if kf.has_group("Collection"):
            self.kind = "Collection"
        self.backend = kf_get(kf, self.kind, "BackendName", "") if self.kind else ""
        self.account_id = kf_get(kf, "GNOME Online Accounts", "AccountId", "")
        self.has_refresh = kf.has_group("Refresh")
        self.refresh_enabled = kf_get(kf, "Refresh", "Enabled", DEFAULT_REFRESH_ENABLED, "bool")
        self.refresh_minutes = kf_get(kf, "Refresh", "IntervalMinutes", DEFAULT_REFRESH_MINUTES, "int")

    @property
    def synced(self):
        return self.kind in dict(KINDS) and self.backend in SYNCED_BACKENDS

    def updated(self, enabled=None, refresh_enabled=None, refresh_minutes=None):
        """Return the source's key file text with the given settings changed."""
        kf = keyfile(self.data)
        if enabled is not None:
            kf.set_boolean("Data Source", "Enabled", enabled)
        if refresh_enabled is not None:
            kf.set_boolean("Refresh", "Enabled", refresh_enabled)
            if not kf_has(kf, "Refresh", "IntervalMinutes"):
                kf.set_integer("Refresh", "IntervalMinutes", self.refresh_minutes)
        if refresh_minutes is not None:
            kf.set_integer("Refresh", "IntervalMinutes", int(refresh_minutes))
            if not kf_has(kf, "Refresh", "Enabled"):
                kf.set_boolean("Refresh", "Enabled", True)
        return kf.to_data()[0]


class Account:
    def __init__(self, path, props, interfaces):
        self.path = path
        self.id = props.get("Id", path.rsplit("/", 1)[-1])
        self.provider = props.get("ProviderName", "")
        self.provider_type = props.get("ProviderType", "")
        self.identity = props.get("PresentationIdentity") or props.get("Identity", "")
        self.features = []
        supported = PROVIDER_FEATURES.get(self.provider_type, set())
        for prop, iface, label in FEATURES:
            if prop not in props:
                continue
            disabled = bool(props[prop])
            if f"org.gnome.OnlineAccounts.{iface}" in interfaces or iface in supported:
                self.features.append((prop, label, not disabled))
        self.sources = []   # filled by load()


def load(bus=None):
    """Return (accounts, other_sources, eds_service). other_sources are synced
    sources that do not belong to a GOA account (e.g. a manually added CalDAV)."""
    bus = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
    accounts = {}
    try:
        for path, ifaces in managed_objects(bus, GOA_NAME, GOA_PATH).items():
            if GOA_ACCOUNT in ifaces:
                acc = Account(path, ifaces[GOA_ACCOUNT], ifaces)
                accounts[acc.id] = acc
    except GLib.Error:
        pass
    service = eds_service(bus)
    sources = []
    for path, ifaces in managed_objects(bus, service, EDS_PATH).items():
        if EDS_SOURCE not in ifaces:
            continue
        props = ifaces[EDS_SOURCE]
        sources.append(Source(path, props.get("UID", ""), props.get("Data", ""), EDS_WRITABLE in ifaces))
    by_uid = {s.uid: s for s in sources}

    def account_of(src, depth=0):
        if src.account_id:
            return src.account_id
        parent = by_uid.get(src.parent)
        return account_of(parent, depth + 1) if parent and depth < 8 else ""

    other = []
    for src in sources:
        if not src.synced:
            continue
        acc = accounts.get(account_of(src))
        (acc.sources if acc else other).append(src)
    order = {k: i for i, (k, _) in enumerate(KINDS)}
    for acc in accounts.values():
        acc.sources.sort(key=lambda s: (order.get(s.kind, 9), s.name.lower()))
    other.sort(key=lambda s: (order.get(s.kind, 9), s.name.lower()))
    return list(accounts.values()), other, service


def write_source(source, service, data, bus=None):
    bus = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
    if not source.writable:
        raise RuntimeError(f"Source '{source.name}' is read-only")
    bus.call_sync(service, source.path, EDS_WRITABLE, "Write", GLib.Variant("(s)", (data,)),
                  None, Gio.DBusCallFlags.NONE, 10000, None)


def set_feature(account, prop, enabled, bus=None):
    bus = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
    bus.call_sync(GOA_NAME, account.path, "org.freedesktop.DBus.Properties", "Set",
                  GLib.Variant("(ssv)", (GOA_ACCOUNT, prop, GLib.Variant("b", not enabled))),
                  None, Gio.DBusCallFlags.NONE, 10000, None)


def kind_label(kind):
    return dict(KINDS).get(kind, kind or "")


def refresh_label(src):
    if not src.refresh_enabled:
        return 'Manual only'
    m = src.refresh_minutes
    if m % 60 == 0:
        return f"Every {m // 60} h" if m != 60 else 'Every hour'
    return f"Every {m} min"
