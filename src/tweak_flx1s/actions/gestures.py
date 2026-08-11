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
import copy
from loguru import logger
from tweak_flx1s.const import CONFIG_DIR, CACHE_DIR
from tweak_flx1s.actions.executor import is_locked, execute_command, show_wofi_menu


CONFIG_FILE = os.path.join(CONFIG_DIR, "gestures.json")
# The gesture list lisgd was actually started with. lisgd bakes plain list
# indices into its command lines, so triggers must resolve against this
# snapshot — the live config may have been edited/reordered since the
# service started, which would silently fire the wrong action.
SNAPSHOT_FILE = os.path.join(CACHE_DIR, "gestures.active.json")

DEFAULT_CONFIG = {
    "enabled": False,
    "gestures": [
        {
            "name": "Switch App Next",
            "spec": "1,RL,B,*,R",
            "locked": {"type": "command", "value": ""},
            "unlocked": {"type": "command", "value": "wtype -M alt -P tab -m alt -p tab"}
        },
        {
            "name": "Switch App Prev",
            "spec": "1,LR,B,*,R",
            "locked": {"type": "command", "value": ""},
            "unlocked": {"type": "command", "value": "wtype -M alt -M shift -P tab -m alt -m shift -p tab"}
        },
        {
            "name": "Kill App",
            "spec": "1,LR,L,L,R",
            "locked": {"type": "command", "value": ""},
            "unlocked": {"type": "command", "value": "wtype -M alt -P F4 -m alt -p F4"}
        },
        {
            "name": "Back",
            "spec": "1,LR,L,S,R",
            "locked": {"type": "command", "value": ""},
            "unlocked": {"type": "command", "value": "wtype -k XF86Back"}
        }
    ]
}

class GesturesManager:
    """Manages gesture configuration and execution."""
    def __init__(self, dedupe_save=True):
        # dedupe_save=False in the trigger path: a one-shot --trigger-gesture
        # process must not rewrite the config while the GUI or service owns it.
        self.config = self._load_config()
        if dedupe_save:
            self._remove_duplicates()

    def _load_config(self):
        """Loads gesture configuration."""
        if not os.path.exists(CONFIG_FILE):
            return copy.deepcopy(DEFAULT_CONFIG)
        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
                if "gestures" not in data:
                     return copy.deepcopy(DEFAULT_CONFIG)
                return data
        except Exception as e:
            logger.error(f"Failed to load gestures config: {e}")
            return copy.deepcopy(DEFAULT_CONFIG)

    def _remove_duplicates(self):
        """
        Removes duplicate gesture specs, keeping the LAST occurrence.
        """
        gestures = self.config.get("gestures", [])
        if not gestures:
            return

        seen = set()
        unique_gestures = []

        for g in reversed(gestures):
            spec = g.get("spec")
            if spec and spec not in seen:
                seen.add(spec)
                unique_gestures.append(g)
            elif spec:
                logger.warning(f"Removing older duplicate gesture spec: {spec}")

        unique_gestures.reverse()

        if len(unique_gestures) != len(gestures):
            self.config["gestures"] = unique_gestures
            self.save_config()

    def save_config(self, new_config=None):
        """Saves gesture configuration."""
        if new_config:
            self.config = new_config
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=4)

    def write_snapshot(self, gestures):
        """Persists the gesture list the service handed to lisgd."""
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(SNAPSHOT_FILE, "w") as f:
                json.dump(gestures, f)
        except Exception as e:
            logger.error(f"Failed to write gesture snapshot: {e}")

    def _load_snapshot(self):
        """Returns the snapshot gesture list, or None if unavailable."""
        try:
            if os.path.exists(SNAPSHOT_FILE):
                with open(SNAPSHOT_FILE, "r") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.error(f"Failed to load gesture snapshot: {e}")
        return None

    def handle_gesture(self, index):
        """Handles a triggered gesture by index."""
        try:
            index = int(index)
        except ValueError:
            logger.error(f"Invalid gesture index: {index}")
            return

        gestures = self._load_snapshot()
        if gestures is None:
            gestures = self.config.get("gestures", [])
        if index < 0 or index >= len(gestures):
            logger.error(f"Gesture index out of range: {index}")
            return

        gesture = gestures[index]
        logger.info(f"Triggering gesture: {gesture.get('name', 'Unknown')}")

        locked = is_locked()
        state_key = "locked" if locked else "unlocked"

        action_config = gesture.get(state_key)
        if not action_config:
             logger.warning(f"No action configured for gesture {index} in {state_key} state.")
             return

        action_type = action_config.get("type")
        if action_type == "command":
            cmd = action_config.get("value")
            execute_command(cmd)
        elif action_type == "wofi":
            items = action_config.get("items", [])
            show_wofi_menu(items)
