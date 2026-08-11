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

import requests
import urllib.parse
from gi.repository import Gio, GLib
from tweak_flx1s.utils import logger, run_command

class WeatherManager:
    """Manages GNOME Weather locations."""
    def __init__(self):
        self.schema_id = "org.gnome.Weather"
        self.settings = None
        try:
            self.settings = Gio.Settings.new(self.schema_id)
        except Exception as e:
            logger.warning(f"Could not load GSettings for {self.schema_id}: {e}")

    def search_location(self, query):
        """Searches for a location using OSM Nominatim."""
        if not query:
            return []
        encoded = urllib.parse.quote(query.replace(" ", "+"))
        url = f"https://nominatim.openstreetmap.org/search?q={encoded}&format=json&limit=5"
        try:
            # timeout: a stalled request on flaky mobile data must not hang
            # the search thread (and with it the dialog) forever.
            resp = requests.get(url, headers={"User-Agent": "FastFLX1/1.0"}, timeout=15)
            if resp.status_code != 200:
                logger.error(f"Weather API error: {resp.status_code}")
                return []
            data = resp.json()
            if not data:
                return []
            return data
        except Exception as e:
            logger.error(f"Error fetching weather location: {e}")
            return []

    def add_location(self, location_data):
        """Adds a location to GNOME Weather settings."""
        if not self.settings:
            logger.error("GSettings not initialized")
            return False

        try:
            name = location_data.get('display_name', '').split(',')[0]
            lat = float(location_data.get('lat'))
            lon = float(location_data.get('lon'))

            lat_rad = lat / (180 / 3.141592654)
            lon_rad = lon / (180 / 3.141592654)

            # Escape for the GVariant text format: a bare ' in the name
            # (e.g. N'Djamena) would break the parse below.
            safe_name = name.replace("\\", "\\\\").replace("'", "\\'")
            variant_str = f"<(uint32 2, <('{safe_name}', '', false, [({lat_rad}, {lon_rad})], @a(dd) [])>)>"

            logger.info(f"Parsing variant string: {variant_str}")

            new_location_variant = GLib.Variant.parse(GLib.VariantType("v"), variant_str, None)

            current_value = self.settings.get_value("locations")

            builder = GLib.VariantBuilder(GLib.VariantType("av"))

            if current_value:
                for i in range(current_value.n_children()):
                    child = current_value.get_child_value(i)
                    builder.add_value(child)

            builder.add_value(new_location_variant)

            new_array = builder.end()
            self.settings.set_value("locations", new_array)
            logger.info(f"Added location: {name}")
            return True

        except Exception as e:
            logger.error(f"Failed to add location: {e}")
            return False
