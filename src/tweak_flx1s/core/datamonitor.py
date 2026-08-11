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

import os
import json
import time
from datetime import datetime
from loguru import logger
from tweak_flx1s.utils import run_command
from tweak_flx1s.const import CONFIG_DIR

CONFIG_FILE = os.path.join(CONFIG_DIR, "datamonitor.json")

class DataMonitorConfig:
    def __init__(self):
        self.config = {
            "enabled": False,
            "daily_limit_mb": 1000,
            "weekly_limit_mb": 5000,
            "monthly_limit_mb": 20000,
            "action": "notify",
            "check_interval_min": 5,
            "bypass_silent": False,
            "temporary_bypass": False,
            "default_mobile_data": True,
            "default_roaming": False,
            "default_autoconnect": True,
            # Name of the GSM connection profile the monitor controls. Empty =
            # auto-pick the first one found. Set via the APN picker in the GUI.
            "gsm_connection": ""
        }
        self.load()

    def load(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    self.config.update(data)
            except Exception as e:
                logger.error(f"Failed to load datamonitor config: {e}")

    def save(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save datamonitor config: {e}")

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self.save()


class DataMonitor:
    def __init__(self):
        self.config = DataMonitorConfig()
        self._last_rx = 0
        self._last_tx = 0
        self._last_time = 0
        self._connection_state = None

    def get_usage(self):
        """
        Returns a dict with 'daily', 'weekly', 'monthly' usage in MB summed
        across all ccmni* interfaces (vnstat only tracks ccmni*; ril_0 is
        the NetworkManager-side device and is not counted here).
        """
        output = run_command("vnstat --json", check=False)
        if not output:
            return None

        try:
            data = json.loads(output)
            interfaces = data.get("interfaces", [])
            if not interfaces:
                return None

            daily_total = 0.0
            weekly_total = 0.0
            monthly_total = 0.0
            now = datetime.now()
            current_year, current_week, _ = now.isocalendar()

            for iface_data in interfaces:
                name = iface_data.get("name", "")
                if not name.startswith("ccmni"):
                    continue

                traffic = iface_data.get("traffic", {})

                daily_rx = 0
                daily_tx = 0
                for day in traffic.get("day", []):
                    d_date = day.get("date", {})
                    if d_date.get("year") == now.year and d_date.get("month") == now.month and d_date.get("day") == now.day:
                        daily_rx = day.get("rx", 0)
                        daily_tx = day.get("tx", 0)
                        break

                monthly_rx = 0
                monthly_tx = 0
                for month in traffic.get("month", []):
                    m_date = month.get("date", {})
                    if m_date.get("year") == now.year and m_date.get("month") == now.month:
                        monthly_rx = month.get("rx", 0)
                        monthly_tx = month.get("tx", 0)
                        break

                weekly_rx = 0
                weekly_tx = 0
                for day in traffic.get("day", []):
                    d_date = day.get("date", {})
                    dt = datetime(d_date.get("year"), d_date.get("month"), d_date.get("day"))
                    y, w, _ = dt.isocalendar()
                    if y == current_year and w == current_week:
                        weekly_rx += day.get("rx", 0)
                        weekly_tx += day.get("tx", 0)

                daily_total += (daily_rx + daily_tx) / (1024 * 1024)
                weekly_total += (weekly_rx + weekly_tx) / (1024 * 1024)
                monthly_total += (monthly_rx + monthly_tx) / (1024 * 1024)

            return {
                "daily_mb": daily_total,
                "weekly_mb": weekly_total,
                "monthly_mb": monthly_total
            }

        except Exception as e:
            logger.error(f"Failed to parse vnstat output: {e}")
            return None

    def get_live_rate(self):
        """
        Returns the current rx/tx rate as a string (fallback).
        """
        return "↓ 0.0 KB/s  ↑ 0.0 KB/s"

    def get_current_total_bytes(self):
        """
        Sums rx_bytes and tx_bytes across all ccmni* interfaces (matches
        what vnstat tracks — see get_usage).
        """
        total = 0
        try:
            for name in os.listdir("/sys/class/net"):
                if name.startswith("ccmni"):
                    rx_path = f"/sys/class/net/{name}/statistics/rx_bytes"
                    tx_path = f"/sys/class/net/{name}/statistics/tx_bytes"
                    if os.path.exists(rx_path) and os.path.exists(tx_path):
                        with open(rx_path, 'r') as f:
                            total += int(f.read().strip())
                        with open(tx_path, 'r') as f:
                            total += int(f.read().strip())
            return total
        except Exception:
            return total

    def _get_connection_properties(self, conn_name):
        try:
            out = run_command(["nmcli", "-g", "connection.autoconnect,gsm.roaming", "connection", "show", conn_name], check=False)
            if out:
                lines = out.strip().splitlines()
                if len(lines) >= 2:
                    return {
                        "autoconnect": lines[0].strip(),
                        "roaming": lines[1].strip()
                    }
        except Exception as e:
            logger.error(f"Failed to get connection properties for {conn_name}: {e}")
        return None

    def _is_connection_active(self, conn_name):
        try:
            out = run_command("nmcli -t -f NAME,TYPE connection show --active", check=False)
            if out:
                for line in out.splitlines():
                    parts = line.split(":")
                    if len(parts) >= 2 and parts[1] == "gsm" and parts[0] == conn_name:
                        return True
        except Exception:
            pass
        return False

    def list_gsm_connections(self):
        """All NetworkManager connection profiles of type gsm, by name."""
        names = []
        out = run_command("nmcli -t -f NAME,TYPE connection show", check=False)
        if out:
            for line in out.splitlines():
                parts = line.split(":")
                if len(parts) >= 2 and parts[1] == "gsm":
                    names.append(parts[0])
        return names

    def _resolve_gsm_connection(self):
        """The configured GSM profile if it still exists, else the first found
        (or None). Every nmcli command in this class targets this profile."""
        names = self.list_gsm_connections()
        if not names:
            return None
        configured = self.config.get("gsm_connection", "")
        if configured and configured in names:
            return configured
        return names[0]

    def disable_connection(self):
        """
        Downs the configured gsm connection and disables its roaming/autoconnect.
        Returns True ONLY when the link was actually brought DOWN this call, so
        callers can notify "mobile data has been disabled" only when it really
        was. Returns False if the link was already down (roaming/autoconnect are
        still re-asserted, but that alone is not a disable).
        """
        logger.info("Disabling mobile connection...")
        try:
            conn_name = self._resolve_gsm_connection()
            if not conn_name:
                logger.warning("No mobile connection found to disable")
                return False

            props = self._get_connection_properties(conn_name)
            is_active = self._is_connection_active(conn_name)

            # Keep roaming/autoconnect off so the link can't silently come back
            # up. These are not "bringing the link down" on their own.
            if props:
                if props["roaming"] != "no":
                    logger.info(f"Disabling roaming for connection: {conn_name}")
                    run_command(["nmcli", "connection", "modify", conn_name, "gsm.roaming", "no"], check=False)
                if props["autoconnect"] != "no":
                    logger.info(f"Disabling autoconnect for connection: {conn_name}")
                    run_command(["nmcli", "connection", "modify", conn_name, "connection.autoconnect", "no"], check=False)

            link_went_down = False
            if is_active:
                logger.info(f"Bringing down connection: {conn_name}")
                run_command(["nmcli", "connection", "down", conn_name], check=False)
                link_went_down = True

            return link_went_down

        except Exception as e:
            logger.error(f"Failed to disable connection: {e}")
            return False

    def restore_connection(self):
        """
        Brings the configured gsm connection to its default state.

        The three 'default_*' settings are the single source of truth for what
        state mobile data returns to whenever the app restores it — the same on
        every path (limit cleared, config change, or bypass). roaming and
        autoconnect are always set to your defaults; the link is brought up
        only if default_mobile_data is on, and brought down if it is off. So
        the app never turns data on when you have it off by default — not even
        a bypass (a bypass just stops the monitor from disabling it).
        Returns True if settings/state were actually changed.
        """
        logger.info("Restoring mobile connection to defaults...")
        try:
            conn_name = self._resolve_gsm_connection()
            if not conn_name:
                logger.warning("No mobile connection found to restore")
                return False

            roaming_val = "yes" if self.config.get("default_roaming", False) else "no"
            autoconnect_val = "yes" if self.config.get("default_autoconnect", True) else "no"
            default_mobile_data = self.config.get("default_mobile_data", True)

            props = self._get_connection_properties(conn_name)
            is_active = self._is_connection_active(conn_name)

            modified = False
            # Always re-assert roaming/autoconnect to the configured defaults.
            if props:
                if props["roaming"] != roaming_val:
                    logger.info(f"Restoring roaming={roaming_val} for connection: {conn_name}")
                    run_command(["nmcli", "connection", "modify", conn_name, "gsm.roaming", roaming_val], check=False)
                    modified = True
                if props["autoconnect"] != autoconnect_val:
                    logger.info(f"Restoring autoconnect={autoconnect_val} for connection: {conn_name}")
                    run_command(["nmcli", "connection", "modify", conn_name, "connection.autoconnect", autoconnect_val], check=False)
                    modified = True

            # Link up/down strictly follows default_mobile_data.
            if default_mobile_data:
                if not is_active:
                    logger.info(f"Bringing up connection: {conn_name}")
                    run_command(["nmcli", "connection", "up", conn_name], check=False)
                    modified = True
            else:
                if is_active:
                    logger.info(f"Bringing down connection (default mobile data is OFF): {conn_name}")
                    run_command(["nmcli", "connection", "down", conn_name], check=False)
                    modified = True

            return modified
        except Exception as e:
            logger.error(f"Failed to restore connection: {e}")
            return False