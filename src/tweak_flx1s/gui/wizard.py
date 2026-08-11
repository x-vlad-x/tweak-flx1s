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

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
from tweak_flx1s.utils import logger


class GestureWizard(Adw.Window):
    """Wizard to guide the user through creating a gesture spec."""
    def __init__(self, parent, on_complete, used_specs=None, current_spec=None):
        super().__init__(transient_for=parent, modal=True, title="New Gesture Wizard")
        self.set_default_size(500, 600)
        self.on_complete = on_complete
        self.used_specs = used_specs or []
        self.current_spec = current_spec

        self.selected_fingers = "1"
        self.selected_direction = "LR"
        self.selected_edge = "*"
        self.selected_distance = "*"
        self.selected_mode = "R"

        self.mode_group = None

        content = Adw.ToolbarView()
        self.set_content(content)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)
        content.add_top_bar(header)

        self.cancel_btn = Gtk.Button(label="Cancel")
        self.cancel_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self.close() or False))
        header.pack_start(self.cancel_btn)

        self.back_btn = Gtk.Button(label="Go Back")
        self.back_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_back() or False))
        self.back_btn.set_visible(False)
        header.pack_start(self.back_btn)

        self.next_btn = Gtk.Button(label="Proceed")
        self.next_btn.add_css_class("suggested-action")
        self.next_btn.connect("clicked", lambda b: GLib.idle_add(lambda: self._on_next() or False))
        header.pack_end(self.next_btn)

        self.carousel = Adw.Carousel()
        self.carousel.set_interactive(False)
        self.carousel.set_allow_scroll_wheel(False)
        self.carousel.set_allow_mouse_drag(False)
        self.carousel.set_allow_long_swipes(False)

        self.carousel.set_vexpand(True)
        self.carousel.set_hexpand(True)

        self.carousel.connect("page-changed", lambda c, i: GLib.idle_add(lambda: self._on_page_changed(c, i) or False))
        content.set_content(self.carousel)

        self.step_fingers = self._create_step_fingers()
        self.carousel.append(self.step_fingers)

        self.step_direction = self._create_step_direction()
        self.carousel.append(self.step_direction)

        self.step_edge = self._create_step_edge()
        self.carousel.append(self.step_edge)

        self.step_distance = self._create_step_distance()
        self.carousel.append(self.step_distance)

        self.step_mode = self._create_step_mode()
        self.carousel.append(self.step_mode)

        self._on_page_changed(self.carousel, 0)

    def _create_page(self, title, subtitle):
        """Creates a standard wizard page with a title and subtitle."""
        page = Adw.PreferencesPage()
        page.set_hexpand(True)
        page.set_vexpand(True)
        group = Adw.PreferencesGroup()
        group.set_title(title)
        group.set_description(subtitle)
        page.add(group)
        return page, group

    def _create_step_fingers(self):
        """Creates the fingers selection step."""
        page, group = self._create_page("Step 1: Fingers", "How many fingers used for the gesture?")

        for val in ["1", "2", "3"]:
            row = Adw.ActionRow(title=val)
            row.set_title_lines(0)
            chk = Gtk.CheckButton()
            chk.set_group(self.fingers_group if val != "1" else None)
            if val == "1": self.fingers_group = chk

            chk.set_active(val == "1")
            chk.connect("toggled", self._make_radio_callback(val, "selected_fingers"))
            row.add_prefix(chk)
            row.set_activatable_widget(chk)
            group.add(row)

        return page

    def _create_step_direction(self):
        page, group = self._create_page("Step 2: Direction", "Which direction is the swipe?")

        options = [
            ("LR", "From Left to Right"),
            ("RL", "From Right to Left"),
            ("UD", "From Top to Bottom"),
            ("DU", "From Bottom to Top"),
            ("DLUR", "From Bottom-Left to Top-Right"),
            ("URDL", "From Top-Right to Bottom-Left"),
            ("ULDR", "From Top-Left to Bottom-Right"),
            ("DRUL", "From Bottom-Right to Top-Left")
        ]

        first_chk = None
        for code, label in options:
            row = Adw.ActionRow(title=label)
            row.set_title_lines(0)
            chk = Gtk.CheckButton()
            chk.set_group(first_chk)
            if not first_chk: first_chk = chk

            chk.set_active(code == "LR")
            chk.connect("toggled", self._make_radio_callback(code, "selected_direction"))
            row.add_prefix(chk)
            row.set_activatable_widget(chk)
            group.add(row)

        return page

    def _create_step_edge(self):
        page, group = self._create_page("Step 3: Edge", "Where should the gesture start?")

        options = [
            ("*", "Anywhere"),
            ("L", "Left Edge"),
            ("R", "Right Edge"),
            ("T", "Top Edge"),
            ("B", "Bottom Edge"),
            ("TL", "Top-Left Corner"),
            ("TR", "Top-Right Corner"),
            ("BL", "Bottom-Left Corner"),
            ("BR", "Bottom-Right Corner"),
            ("N", "None (Middle of screen only)")
        ]

        first_chk = None
        for code, label in options:
            row = Adw.ActionRow(title=label)
            row.set_title_lines(0)
            chk = Gtk.CheckButton()
            chk.set_group(first_chk)
            if not first_chk: first_chk = chk

            chk.set_active(code == "*")
            chk.connect("toggled", self._make_radio_callback(code, "selected_edge"))
            row.add_prefix(chk)
            row.set_activatable_widget(chk)
            group.add(row)

        return page

    def _create_step_distance(self):
        page, group = self._create_page("Step 4: Distance", "How long should the swipe be?")

        options = [
            ("*", "Any Distance"),
            ("S", "Short"),
            ("M", "Medium"),
            ("L", "Large")
        ]

        first_chk = None
        for code, label in options:
            row = Adw.ActionRow(title=label)
            row.set_title_lines(0)
            chk = Gtk.CheckButton()
            chk.set_group(first_chk)
            if not first_chk: first_chk = chk

            chk.set_active(code == "*")
            chk.connect("toggled", self._make_radio_callback(code, "selected_distance"))
            row.add_prefix(chk)
            row.set_activatable_widget(chk)
            group.add(row)

        return page

    def _create_step_mode(self):
        """Creates the mode selection step (initially empty)."""
        page = Adw.PreferencesPage()
        page.set_hexpand(True)
        page.set_vexpand(True)
        self.mode_group = None
        return page

    def _refresh_mode_step(self):
        """Rebuilds the mode step to check for duplicates."""
        if self.mode_group:
            self.step_mode.remove(self.mode_group)
            self.mode_group = None

        self.mode_group = Adw.PreferencesGroup()
        self.mode_group.set_title("Step 5: Mode")
        self.mode_group.set_description("Trigger on press or release?")
        self.step_mode.add(self.mode_group)

        options = [
            ("R", "On Release (Recommended)"),
            ("P", "On Press")
        ]

        first_chk = None
        valid_selection_exists = False

        for code, label in options:
            test_spec = f"{self.selected_fingers},{self.selected_direction},{self.selected_edge},{self.selected_distance},{code}"
            is_dup = test_spec in self.used_specs

            row = Adw.ActionRow(title=label)
            row.set_title_lines(0)

            if is_dup:
                row.set_subtitle("Already configured")
                row.set_sensitive(False)

            chk = Gtk.CheckButton()
            chk.set_group(first_chk)
            if not first_chk: first_chk = chk

            if not is_dup and not valid_selection_exists:
                chk.set_active(True)
                self.selected_mode = code
                valid_selection_exists = True
            elif not is_dup:
                chk.set_active(False)
            else:
                chk.set_active(False)
                chk.set_sensitive(False)

            if not is_dup:
                chk.connect("toggled", self._make_radio_callback(code, "selected_mode"))

            row.add_prefix(chk)
            row.set_activatable_widget(chk)
            self.mode_group.add(row)

        if not valid_selection_exists:
             self.next_btn.set_sensitive(False)
             msg = "Error: Both Release and Press modes are already configured for this gesture combination."
             logger.warning(msg)
             lbl = Gtk.Label(label=msg)
             lbl.add_css_class("error")
             self.mode_group.add(lbl)
        else:
             self.next_btn.set_sensitive(True)

    def _make_radio_callback(self, value, attr_name):
        def _callback(btn):
            def _inner():
                if btn.get_active():
                    setattr(self, attr_name, value)
                    logger.debug(f"Wizard {attr_name} set to {value}")
                return False
            GLib.idle_add(_inner)
        return _callback

    def _on_page_changed(self, carousel, index):
        count = carousel.get_n_pages()

        if index == 0:
            self.cancel_btn.set_visible(True)
            self.back_btn.set_visible(False)
            self.next_btn.set_label("Proceed")
            self.next_btn.remove_css_class("destructive-action")
            self.next_btn.add_css_class("suggested-action")

        elif index < count - 1:
            self.cancel_btn.set_visible(False)
            self.back_btn.set_visible(True)
            self.next_btn.set_label("Proceed")
            self.next_btn.add_css_class("suggested-action")

        else:
            self.cancel_btn.set_visible(False)
            self.back_btn.set_visible(True)
            self.next_btn.set_label("Save")
            self.next_btn.add_css_class("suggested-action")

            self._refresh_mode_step()

    def _on_next(self):
        idx = self.carousel.get_position()
        count = self.carousel.get_n_pages()

        if idx < count - 1:
            next_idx = idx + 1
            self.carousel.scroll_to(self.carousel.get_nth_page(next_idx), True)
        else:
            self._on_finish()

    def _on_back(self):
        idx = self.carousel.get_position()
        if idx > 0:
            self.carousel.scroll_to(self.carousel.get_nth_page(idx - 1), True)

    def _on_finish(self):
        spec = f"{self.selected_fingers},{self.selected_direction},{self.selected_edge},{self.selected_distance},{self.selected_mode}"
        logger.info(f"Wizard completed. Spec: {spec}")
        if self.on_complete:
            self.on_complete(spec)
        GLib.idle_add(lambda: self.close() or False)
