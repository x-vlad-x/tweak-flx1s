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

import sys
import os
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk
from tweak_flx1s.const import APP_ID, APP_NAME
from tweak_flx1s.gui.window import MainWindow
from tweak_flx1s.utils import logger

class FastFLX1App(Adw.Application):
    """Main Application class using libadwaita."""
    def __init__(self, **kwargs):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.FLAGS_NONE,
                         **kwargs)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name(APP_ID)
        self._setup_css()

    def _setup_css(self):
        """Load and apply custom CSS."""
        try:
            provider = Gtk.CssProvider()
            css_file = Gio.File.new_for_path(os.path.join(os.path.dirname(__file__), 'style.css'))
            provider.load_from_file(css_file)
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception as e:
            logger.error(f"CSS setup error: {e}")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = MainWindow(application=self)
        win.present()

def start_gui():
    """Starts the GUI application."""
    app = FastFLX1App()
    return app.run(sys.argv)
