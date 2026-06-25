#!/usr/bin/env python3
# Owner: cs-dongqi@zepp.com
# Organization: Active.Bu
"""Curses-based menuconfig-like TUI framework for active-build-script.

Zero external dependencies — uses Python stdlib `curses` only.

Design principles:
- Single-page form with sections, items, actions, and a help bar.
- Dropdown popups for CHOICE fields; bottom-prompt input for TEXT fields.
- Space toggles TOGGLE fields.
- visible_when / enabled_when lambdas receive the current state dict.
- options_provider lambdas receive the current state dict and return option list.
- refreshes list triggers cascading option recalculation.
"""

from __future__ import annotations

import curses
import unicodedata
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, List, Optional, Dict


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class FieldType(Enum):
    CHOICE = auto()    # dropdown selection (Enter → popup)
    TOGGLE = auto()    # boolean on/off      (Space → flip)
    TEXT   = auto()    # free text input     (Enter → bottom prompt)
    ACTION = auto()    # action button
    SEPARATOR = auto() # section header line


@dataclass
class MenuItem:
    label: str
    key: str
    ftype: FieldType
    value: Any = None
    options: list[str] = field(default_factory=list)
    # Dynamic options: lambda(state: dict) -> list[str]
    options_provider: Optional[Callable] = None
    # Condition: lambda(state: dict) -> bool
    visible_when: Optional[Callable] = None
    enabled_when: Optional[Callable] = None
    # List of field keys this item's options depend on.
    # When any key in refreshes changes, this item's options are recalculated
    # and value is reset to the first option if no longer valid.
    refreshes: list[str] = field(default_factory=list)
    # Called after value change: lambda(state: dict, page: TuiPage) -> None
    on_change: Optional[Callable] = None
    # ACTION only: lambda(state: dict) -> message string when action should be blocked.
    action_guard: Optional[Callable] = None


@dataclass
class MenuSection:
    title: Optional[str] = None
    items: list[MenuItem] = field(default_factory=list)
    visible_when: Optional[Callable] = None


@dataclass
class StatusItem:
    label: str
    value: str
    ok: bool = True


# ---------------------------------------------------------------------------
# Internal row representation
# ---------------------------------------------------------------------------

class _Row:
    """One renderable row in the form body."""
    __slots__ = ("item", "section_title", "is_separator")

    def __init__(self, item=None, section_title=None, is_separator=False):
        self.item = item
        self.section_title = section_title
        self.is_separator = is_separator


# ---------------------------------------------------------------------------
# TUI Page
# ---------------------------------------------------------------------------

class TuiPage:
    def __init__(self, title, sections=None, actions=None, status_items=None, max_width=88):
        self.title = title
        self.sections = sections or []
        self.actions = actions or []
        self.status_items = status_items or []
        self.max_width = max_width
        self._stdscr = None
        self._gray_highlight_enabled = False

    # -- public API ---------------------------------------------------------

    def run(self, stdscr) -> Optional[dict]:
        """Run the curses event loop.  Returns state dict or None on cancel."""
        self._stdscr = stdscr
        try:
            return self._run_loop()
        finally:
            self._stdscr = None

    # -- internal: init & helpers --------------------------------------------

    def _init_curses(self):
        curses.curs_set(0)                     # hide hardware cursor
        self._stdscr.keypad(True)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)   # highlighted row
            curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLUE)   # title bar
            curses.init_pair(3, curses.COLOR_YELLOW, -1)                 # section header
            curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_WHITE)  # popup highlight
            curses.init_pair(5, curses.COLOR_GREEN, -1)                  # OK status
            curses.init_pair(6, curses.COLOR_RED, -1)                    # error status
            self._gray_highlight_enabled = False
            if getattr(curses, "COLORS", 0) >= 256:
                try:
                    curses.init_pair(7, -1, 240)                         # focused row gray bg
                    self._gray_highlight_enabled = True
                except curses.error:
                    self._gray_highlight_enabled = False

    def _attr(self, pair):
        return curses.color_pair(pair) if curses.has_colors() else curses.A_NORMAL

    def _highlight_attr(self):
        if self._gray_highlight_enabled:
            return self._attr(7) | curses.A_BOLD
        return curses.A_BOLD

    def _collect_state(self) -> dict:
        """Walk all items across all sections and collect {key: value}."""
        state = {}
        for section in self.sections:
            for item in section.items:
                if item.key:
                    state[item.key] = item.value
        return state

    def _effective_options(self, item, state) -> list[str]:
        if item.options_provider:
            try:
                return item.options_provider(state)
            except Exception:
                return item.options or []
        return item.options or []

    def _check_visible(self, section, item, state) -> bool:
        if section.visible_when:
            try:
                if not section.visible_when(state):
                    return False
            except Exception:
                return False
        if item.visible_when:
            try:
                if not item.visible_when(state):
                    return False
            except Exception:
                return False
        return True

    def _check_enabled(self, item, state) -> bool:
        if item.enabled_when:
            try:
                return bool(item.enabled_when(state))
            except Exception:
                return False
        return True

    def _flatten(self) -> tuple[list[_Row], dict]:
        """Flatten sections into list of (_Row, is_enabled)."""
        state = self._collect_state()
        rows = []
        enabled_map = {}

        for section in self.sections:
            if section.visible_when:
                try:
                    if not section.visible_when(state):
                        continue
                except Exception:
                    continue

            if section.title:
                rows.append(_Row(section_title=section.title, is_separator=True))
                enabled_map[len(rows) - 1] = False

            for item in section.items:
                if not self._check_visible(section, item, state):
                    continue
                rows.append(_Row(item=item))
                enabled_map[len(rows) - 1] = self._check_enabled(item, state)

        return rows, enabled_map

    def _refresh_after_change(self, changed_key):
        """Recalculate options for dependent items."""
        state = self._collect_state()
        for section in self.sections:
            for item in section.items:
                if changed_key in item.refreshes:
                    opts = self._effective_options(item, state)
                    if item.value not in opts and opts:
                        item.value = opts[0]

    def _after_value_change(self, item):
        """Run refreshes + on_change after a value change."""
        self._refresh_after_change(item.key)
        if item.on_change:
            try:
                item.on_change(self._collect_state(), self)
            except Exception:
                pass

    # -- run loop -----------------------------------------------------------

    def _run_loop(self) -> Optional[dict]:
        self._init_curses()

        # Pre-populate only TOGGLE fields — CHOICE/TEXT stay as set by page builder
        for section in self.sections:
            for item in section.items:
                if item.ftype == FieldType.TOGGLE and item.value is None:
                    item.value = True

        focused = 0          # index into navigable indices
        action_mode = False  # True when focus is on action buttons
        action_focused = 0

        while True:
            rows, enabled_map = self._flatten()

            # Build list of navigable indices (enabled item rows)
            navigable = []
            render_rows = []   # (y_offset, row_data) for rendering
            y = 1  # below title bar
            for idx, row in enumerate(rows):
                render_rows.append((y, idx, row, enabled_map[idx]))
                y += 1
                if not row.is_separator and enabled_map[idx]:
                    navigable.append(idx)

            total_rows = len(render_rows)
            height, full_w = self._stdscr.getmaxyx()
            action_area_rows = 2 if self.actions else 0
            status_area_rows = len(self.status_items)
            help_bar_rows = 1
            body_height = height - 1 - action_area_rows - status_area_rows - help_bar_rows  # -1 for title

            # Content area: capped at max_width, centered on terminal
            safe_w = full_w - 1
            content_w = min(safe_w, max(40, self.max_width))
            margin_x = max(0, (full_w - content_w) // 2)

            # Clamp focused
            if navigable:
                focused = max(0, min(focused, len(navigable) - 1))
            else:
                focused = 0

            # Compute scroll offset
            focused_render_y = 0
            for ry, ridx, _, _ in render_rows:
                if ridx == (navigable[focused] if navigable else 0):
                    focused_render_y = ry
                    break
            scroll = 0
            if focused_render_y >= body_height:
                scroll = focused_render_y - body_height + 1

            # -- render -----------------------------------------------------
            self._stdscr.clear()

            # Title bar (full width)
            title_text = f" {self.title} "
            title_x = max(0, (safe_w - len(title_text)) // 2)
            try:
                self._stdscr.addstr(0, 0, " " * safe_w, self._attr(2))
                self._stdscr.addstr(0, title_x, title_text[:safe_w - title_x],
                                    self._attr(2) | curses.A_BOLD)
            except curses.error:
                pass

            # Body (within content area)
            for ry, ridx, row, enabled in render_rows:
                draw_y = ry - scroll
                if draw_y < 1 or draw_y >= body_height:
                    continue
                is_focused = (not action_mode and navigable and ridx == navigable[focused])
                self._draw_row(draw_y, content_w, margin_x, row, is_focused, enabled)

            # Action bar (within content area)
            action_base_y = height - action_area_rows - help_bar_rows
            status_base_y = action_base_y - status_area_rows
            self._draw_status(status_base_y, content_w, margin_x)
            self._draw_actions(action_base_y, content_w, margin_x, action_mode, action_focused)

            # Help bar (full width, status-bar style)
            help_y = height - 1
            help_text = " ↑↓ Nav  Enter Select  Space Toggle  Esc Back  Tab Actions "
            try:
                self._stdscr.addstr(help_y, 0, " " * safe_w, self._attr(2))
                self._stdscr.addstr(help_y, 0, help_text[:safe_w],
                                    self._attr(2) | curses.A_BOLD)
            except curses.error:
                pass

            self._stdscr.refresh()

            # -- input ------------------------------------------------------
            key = self._stdscr.getch()

            if action_mode:
                if key == curses.KEY_LEFT:
                    if self.actions:
                        action_focused = (action_focused - 1) % len(self.actions)
                elif key == curses.KEY_RIGHT or key == ord('\t'):
                    if self.actions:
                        action_focused = (action_focused + 1) % len(self.actions)
                elif key in (curses.KEY_ENTER, 10, 13):
                    if 0 <= action_focused < len(self.actions):
                        action = self.actions[action_focused]
                        if action.action_guard:
                            message = action.action_guard(self._collect_state())
                            if message:
                                self._popup_message(action.label, message, ok=False)
                                continue
                        result = self._collect_state()
                        result["_action"] = action.key
                        return result
                elif key == 27:  # Esc
                    action_mode = False
                elif key == ord('\t'):
                    action_mode = False
                    if navigable:
                        focused = 0
                continue

            if key == curses.KEY_UP:
                if navigable:
                    focused = (focused - 1) % len(navigable)
            elif key == curses.KEY_DOWN:
                if navigable:
                    focused = (focused + 1) % len(navigable)
            elif key == ord('\t'):
                if self.actions:
                    action_mode = True
                    action_focused = 0
                elif navigable:
                    focused = (focused + 1) % len(navigable)
            elif key == 27:  # Esc — cancel form
                return None
            elif key in (curses.KEY_ENTER, 10, 13):
                if navigable:
                    item = rows[navigable[focused]].item
                    if item is None:
                        continue
                    self._handle_enter(item)
                    self._after_value_change(item)
            elif key == ord(' '):  # Space
                if navigable:
                    item = rows[navigable[focused]].item
                    if item and item.ftype == FieldType.TOGGLE:
                        item.value = not item.value
                        self._after_value_change(item)

        # unreachable
        return None

    # -- drawing helpers ----------------------------------------------------

    @staticmethod
    def _format_value(item, disabled=False):
        """Format a value display string.  Disabled items show [  -  ]."""
        if disabled:
            return "[  -  ]"
        if item.ftype == FieldType.CHOICE:
            raw = str(item.value) if item.value not in (None, "") else ""
            return f"[ {raw} ]" if raw else "[  -  ]"
        elif item.ftype == FieldType.TOGGLE:
            return "[ ✓ ]" if item.value else "[   ]"
        elif item.ftype == FieldType.TEXT:
            display = str(item.value) if item.value else ""
            if len(display) > 40:
                display = display[:37] + "..."
            return f"[ {display} ]" if display else "[  -  ]"
        elif item.ftype == FieldType.ACTION:
            return ""
        else:
            return ""

    @staticmethod
    def _display_width(s):
        """Visual display width of a string (CJK chars occupy 2 cells)."""
        w = 0
        for ch in s:
            if unicodedata.east_asian_width(ch) in ('W', 'F'):
                w += 2
            else:
                w += 1
        return w

    @staticmethod
    def _truncate_by_dw(text, max_dw):
        """Return text truncated to max_dw display cells."""
        result = []
        w = 0
        for ch in text:
            cw = 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
            if w + cw > max_dw:
                break
            result.append(ch)
            w += cw
        return ''.join(result)

    def _draw_row(self, y, content_w, margin_x, row, is_focused, is_enabled):
        """Render one row at (y, margin_x) within content_w, label<space>value layout."""
        stdscr = self._stdscr
        attr = curses.A_NORMAL
        safe_cw = content_w - 1  # never write to last column

        if is_focused:
            attr |= self._highlight_attr()
        if not is_enabled:
            attr |= curses.A_DIM

        if row.is_separator:
            text = f"── {row.section_title} " if row.section_title else ""
            text += "─" * max(0, safe_cw - len(text) - 1)
            try:
                stdscr.addstr(y, margin_x + 1, text[:safe_cw], self._attr(3) | curses.A_BOLD)
            except curses.error:
                pass
            return

        item = row.item
        if item is None:
            return

        val_str = self._format_value(item, disabled=not is_enabled)
        val_dw = self._display_width(val_str)

        # label area in display cells
        label_area_dw = max(1, safe_cw - val_dw - 1)
        label_visible = self._truncate_by_dw(f" {item.label} ", label_area_dw)
        label_dw = self._display_width(label_visible)

        # gap fills remaining display space
        gap = max(1, safe_cw - label_dw - val_dw)
        line = label_visible + " " * gap + val_str
        if is_focused and line:
            line = ">" + line[1:]
        # No char-level truncation needed — display-width math guarantees exact fit

        try:
            if is_focused and self._gray_highlight_enabled:
                stdscr.addstr(y, margin_x, " " * safe_cw, attr)
            stdscr.addstr(y, margin_x, line, attr)
        except curses.error:
            pass

    def _draw_actions(self, base_y, content_w, margin_x, is_active, focused_idx):
        if not self.actions:
            return
        stdscr = self._stdscr
        safe_w = content_w - 1

        parts = []
        for i, act in enumerate(self.actions):
            if is_active and i == focused_idx:
                parts.append((f"[ > {act.label} ]", self._highlight_attr()))
            else:
                parts.append((f"[   {act.label} ]", curses.A_NORMAL | (curses.A_DIM if not is_active else 0)))

        total_len = sum(len(p[0]) for p in parts) + len(parts) - 1
        x = margin_x + max(0, (content_w - total_len) // 2)
        try:
            for text, attr in parts:
                end_x = margin_x + content_w
                if x >= end_x:
                    break
                stdscr.addstr(base_y, x, text[:end_x - x], attr)
                x += len(text) + 1
        except curses.error:
            pass

    def _draw_status(self, base_y, content_w, margin_x):
        if not self.status_items:
            return
        stdscr = self._stdscr
        safe_w = content_w - 1

        for offset, item in enumerate(self.status_items):
            y = base_y + offset
            status_text = f" {item.label}: {item.value} "
            status_text = self._truncate_by_dw(status_text, safe_w)
            attr = self._attr(5 if item.ok else 6) | curses.A_BOLD
            status_dw = self._display_width(status_text)
            x = margin_x + max(0, (safe_w - status_dw) // 2)
            try:
                stdscr.addstr(y, margin_x, " " * content_w, curses.A_NORMAL)
                stdscr.addstr(y, x, status_text, attr)
            except curses.error:
                pass

    # -- field interaction --------------------------------------------------

    def _handle_enter(self, item):
        if item.ftype == FieldType.CHOICE:
            self._popup_choice(item)
        elif item.ftype == FieldType.TEXT:
            self._prompt_text(item)
        elif item.ftype == FieldType.TOGGLE:
            item.value = not item.value
        # ACTION is handled at the action bar level

    def _popup_choice(self, item):
        state = self._collect_state()
        options = self._effective_options(item, state)
        if not options:
            return

        try:
            current_idx = options.index(item.value) if item.value in options else 0
        except (ValueError, AttributeError):
            current_idx = 0

        # Calculate popup width — accommodate both title and widest option
        max_opt_dw = max(self._display_width(o) for o in options)
        title_dw = self._display_width(f" {item.label} ")
        # inner = max(title, option + 2-space indent)
        inner_w = max(title_dw, max_opt_dw + 2)
        popup_width = min(inner_w + 4, self._stdscr.getmaxyx()[1] - 2)
        popup_width = max(popup_width, 12)
        popup_height = min(len(options) + 2, self._stdscr.getmaxyx()[0] - 4)
        start_y = max(0, (self._stdscr.getmaxyx()[0] - popup_height) // 2)
        start_x = max(0, (self._stdscr.getmaxyx()[1] - popup_width) // 2)

        popup = curses.newwin(popup_height, popup_width, start_y, start_x)
        popup.keypad(True)

        selected = current_idx
        scroll_off = 0

        while True:
            popup.erase()
            popup.box()

            # Title — full display, fits because popup_width >= title_dw + 4
            title_text = f" {item.label} "
            try:
                popup.addstr(0, 2, self._truncate_by_dw(title_text, popup_width - 4),
                             self._attr(2) | curses.A_BOLD)
            except curses.error:
                pass

            # Options
            content_h = popup_height - 2
            if selected < scroll_off:
                scroll_off = selected
            if selected >= scroll_off + content_h:
                scroll_off = selected - content_h + 1

            for i in range(content_h):
                opt_idx = scroll_off + i
                if opt_idx >= len(options):
                    break
                opt = options[opt_idx]
                display = f"> {opt}" if opt_idx == selected else f"  {opt}"
                display = self._truncate_by_dw(display, popup_width - 4)
                pad = popup_width - 4 - self._display_width(display)
                display += " " * max(0, pad)
                if opt_idx == selected:
                    try:
                        popup.addstr(i + 1, 1, display, self._highlight_attr())
                    except curses.error:
                        pass
                else:
                    try:
                        popup.addstr(i + 1, 1, display, curses.A_NORMAL)
                    except curses.error:
                        pass

            popup.refresh()

            key = popup.getch()
            if key == curses.KEY_UP:
                selected = (selected - 1) % len(options)
            elif key == curses.KEY_DOWN:
                selected = (selected + 1) % len(options)
            elif key in (curses.KEY_ENTER, 10, 13):
                item.value = options[selected]
                return
            elif key == 27:  # Esc
                return

    def _popup_message(self, title, message, ok=True):
        lines = [line for line in str(message).splitlines() if line]
        if not lines:
            lines = [""]

        max_line_dw = max(self._display_width(line) for line in lines)
        title_text = f" {title} "
        title_dw = self._display_width(title_text)
        popup_width = min(max(max_line_dw, title_dw, 24) + 6, self._stdscr.getmaxyx()[1] - 2)
        popup_width = max(popup_width, 24)
        popup_height = min(len(lines) + 4, self._stdscr.getmaxyx()[0] - 2)
        start_y = max(0, (self._stdscr.getmaxyx()[0] - popup_height) // 2)
        start_x = max(0, (self._stdscr.getmaxyx()[1] - popup_width) // 2)

        popup = curses.newwin(popup_height, popup_width, start_y, start_x)
        popup.keypad(True)
        attr = self._attr(5 if ok else 6) | curses.A_BOLD

        while True:
            popup.erase()
            popup.box()
            try:
                popup.addstr(0, 2, self._truncate_by_dw(title_text, popup_width - 4), attr)
                for index, line in enumerate(lines[: popup_height - 3], start=1):
                    popup.addstr(index + 1, 2, self._truncate_by_dw(line, popup_width - 4), attr)
                hint = " Press Enter/Esc "
                hint_x = max(1, (popup_width - len(hint)) // 2)
                popup.addstr(popup_height - 2, hint_x, hint[: popup_width - hint_x - 1], curses.A_DIM)
            except curses.error:
                pass
            popup.refresh()

            key = popup.getch()
            if key in (curses.KEY_ENTER, 10, 13, 27):
                return

    def _prompt_text(self, item):
        """Centered popup text-input dialog, like CHOICE popup but for typing."""
        current = str(item.value) if item.value not in (None, "") else ""
        label = f" {item.label} "
        title_dw = self._display_width(label)
        input_dw = max(self._display_width(current) + 4, title_dw, 30)
        pw = min(input_dw + 4, self._stdscr.getmaxyx()[1] - 2)
        pw = max(pw, 24)
        ph = 5
        sy = max(0, (self._stdscr.getmaxyx()[0] - ph) // 2)
        sx = max(0, (self._stdscr.getmaxyx()[1] - pw) // 2)

        win = curses.newwin(ph, pw, sy, sx)
        win.keypad(True)

        buffer = list(current)
        cursor = len(buffer)
        finished = False

        try:
            while not finished:
                win.erase()
                win.box()

                # Title
                try:
                    win.addstr(0, 2, self._truncate_by_dw(label, pw - 4),
                               self._attr(2) | curses.A_BOLD)
                except curses.error:
                    pass

                # Input line
                max_visible = pw - 4  # available for typed text display
                max_cursor_col = max(0, max_visible - 1)
                start = 0
                while self._display_width("".join(buffer[start:cursor])) > max_cursor_col:
                    start += 1
                end = start
                while end < len(buffer):
                    candidate = "".join(buffer[start:end + 1])
                    if self._display_width(candidate) > max_visible:
                        break
                    end += 1
                display_text = "".join(buffer[start:end])
                cursor_index = cursor - start

                # Draw input background (highlight area)
                try:
                    cursor_index = max(0, min(cursor_index, len(display_text)))
                    display_with_cursor = display_text[:cursor_index] + "|" + display_text[cursor_index:]
                    display_with_cursor = self._truncate_by_dw(display_with_cursor, max_visible)
                    input_attr = self._highlight_attr()
                    win.addstr(2, 2, " " * max_visible, input_attr)
                    win.addstr(2, 2, display_with_cursor, input_attr)
                except curses.error:
                    pass

                # Help line
                try:
                    help_line = " Enter: confirm  Esc: cancel "
                    win.addstr(ph - 2, max(1, (pw - len(help_line)) // 2), help_line,
                               curses.A_DIM)
                except curses.error:
                    pass

                win.refresh()

                ch = win.getch()
                if ch in (curses.KEY_ENTER, 10, 13):
                    finished = True
                elif ch == 27:  # Esc
                    return
                elif ch in (curses.KEY_BACKSPACE, 127, 8):
                    if buffer and cursor > 0:
                        buffer.pop(cursor - 1)
                        cursor -= 1
                elif ch == curses.KEY_LEFT:
                    if cursor > 0:
                        cursor -= 1
                elif ch == curses.KEY_RIGHT:
                    if cursor < len(buffer):
                        cursor += 1
                elif ch in (curses.KEY_HOME, 1):  # Ctrl-A
                    cursor = 0
                elif ch == 5:  # Ctrl-E
                    cursor = len(buffer)
                elif 32 <= ch <= 126:
                    buffer.insert(cursor, chr(ch))
                    cursor += 1
        finally:
            try:
                curses.curs_set(0)
            except curses.error:
                pass

        new_value = "".join(buffer).strip()
        if new_value:
            item.value = new_value
