"""Interactive TUI for refine — browse specs, run comparisons, view results.

Launch with:
    python -m refine tui [start_dir]
"""

import curses
import json
import os
import threading
import textwrap
from pathlib import Path

from .types import CompareInput, CompareResult, Verdict, FunctionSig, SpecSet, Param
from .core import compare
from .interpret import interpret_result
from .backends.cbmc import CBMCBackend
from .backends.esbmc import ESBMCBackend

# ── Color pairs ──────────────────────────────────────────────────────────────

_PAIR_PROVED = 1
_PAIR_REFUTED = 2
_PAIR_VACUOUS = 3
_PAIR_ERROR = 4
_PAIR_HEADER = 5
_PAIR_SELECTED = 6
_PAIR_UNKNOWN = 7
_PAIR_ACCENT = 8
_PAIR_DIM = 9
_PAIR_STATUS = 10
_PAIR_TIMEOUT = 11

_VERDICT_PAIRS = {
    "proved": _PAIR_PROVED,
    "refuted": _PAIR_REFUTED,
    "vacuous": _PAIR_VACUOUS,
    "error": _PAIR_ERROR,
    "timeout": _PAIR_TIMEOUT,
    "unsupported": _PAIR_UNKNOWN,
    "unknown": _PAIR_UNKNOWN,
}

_VERDICT_ICONS = {
    "proved": "✔",
    "refuted": "✘",
    "vacuous": "⚠",
    "error": "⊘",
    "timeout": "⏱",
    "unsupported": "—",
    "unknown": "?",
}


def _init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(_PAIR_PROVED, curses.COLOR_GREEN, -1)
    curses.init_pair(_PAIR_REFUTED, curses.COLOR_RED, -1)
    curses.init_pair(_PAIR_VACUOUS, curses.COLOR_YELLOW, -1)
    curses.init_pair(_PAIR_ERROR, curses.COLOR_RED, -1)
    curses.init_pair(_PAIR_HEADER, curses.COLOR_CYAN, -1)
    curses.init_pair(_PAIR_SELECTED, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(_PAIR_UNKNOWN, curses.COLOR_MAGENTA, -1)
    curses.init_pair(_PAIR_ACCENT, curses.COLOR_YELLOW, -1)
    curses.init_pair(_PAIR_DIM, curses.COLOR_WHITE, -1)
    curses.init_pair(_PAIR_STATUS, curses.COLOR_BLACK, curses.COLOR_WHITE)
    curses.init_pair(_PAIR_TIMEOUT, curses.COLOR_YELLOW, -1)


def _verdict_attr(v: str) -> int:
    pair = _VERDICT_PAIRS.get(v, 0)
    attr = curses.color_pair(pair)
    if v == "refuted" or v == "error":
        attr |= curses.A_BOLD
    return attr


# ── Drawing helpers ──────────────────────────────────────────────────────────

def _safe_addstr(win, y, x, text, attr=0):
    """Write text to window, silently truncating if it overflows."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    available = w - x - 1
    if available <= 0:
        return
    win.addnstr(y, x, text, available, attr)


def _draw_header(win, title: str):
    h, w = win.getmaxyx()
    _safe_addstr(win, 0, 0, " " * w, curses.color_pair(_PAIR_STATUS))
    _safe_addstr(win, 0, 2, f" refine ", curses.color_pair(_PAIR_HEADER) | curses.A_BOLD)
    _safe_addstr(win, 0, 11, f"│ {title}", curses.color_pair(_PAIR_STATUS))


def _draw_statusbar(win, text: str):
    h, w = win.getmaxyx()
    _safe_addstr(win, h - 1, 0, " " * w, curses.color_pair(_PAIR_STATUS))
    _safe_addstr(win, h - 1, 1, text[:w - 2], curses.color_pair(_PAIR_STATUS))


def _draw_box(win, y, x, height, width, title=""):
    """Draw a Unicode box with optional title."""
    h, w = win.getmaxyx()
    if y + height > h or x + width > w:
        return
    _safe_addstr(win, y, x, "┌" + "─" * (width - 2) + "┐", curses.color_pair(_PAIR_DIM))
    if title:
        _safe_addstr(win, y, x + 2, f" {title} ", curses.color_pair(_PAIR_ACCENT))
    for row in range(1, height - 1):
        _safe_addstr(win, y + row, x, "│", curses.color_pair(_PAIR_DIM))
        _safe_addstr(win, y + row, x + width - 1, "│", curses.color_pair(_PAIR_DIM))
    _safe_addstr(win, y + height - 1, x, "└" + "─" * (width - 2) + "┘",
                 curses.color_pair(_PAIR_DIM))


# ── TUI Application ─────────────────────────────────────────────────────────

class RefineTUI:
    def __init__(self, stdscr, start_dir: str = "."):
        self.scr = stdscr
        self.start_dir = os.path.abspath(start_dir)
        self.options = {
            "backend": "esbmc",
            "timeout": 60,
            "unwind": 10,
            "vec_size": "",
            "reference": "left",
            "auto_bounds": False,
            "validate_bounds": False,
            "interpret": False,
            "emit_harness": False,
            "docker_path": "docker",
            "esbmc_image": "esbmc:latest",
        }

    # ── Main menu ────────────────────────────────────────────────────────

    def run(self):
        _init_colors()
        curses.curs_set(0)
        self.scr.keypad(True)

        while True:
            action = self._main_menu()
            if action == "quit":
                break
            elif action == "compare":
                self._flow_compare()
            elif action == "batch":
                self._flow_batch()
            elif action == "options":
                self._edit_options()

    def _main_menu(self) -> str:
        items = [
            ("compare", "Compare specs from a JSON file"),
            ("batch", "Batch compare a directory of JSON files"),
            ("options", "Configure verifier options"),
            ("quit", "Exit"),
        ]
        sel = 0
        while True:
            self.scr.clear()
            h, w = self.scr.getmaxyx()
            _draw_header(self.scr, "Main Menu")

            # Logo
            logo = [
                "╭─────────────────────────────╮",
                "│  ┏━┓┏━╸┏━╸╻┏┓╻┏━╸          │",
                "│  ┣┳┛┣╸ ┣╸ ┃┃┗┫┣╸           │",
                "│  ╹┗╸╹  ╹  ╹╹ ╹┗━╸          │",
                "│  Bounded Refinement Checker  │",
                "╰─────────────────────────────╯",
            ]
            logo_y = 2
            for i, line in enumerate(logo):
                cx = max(0, (w - len(line)) // 2)
                attr = curses.color_pair(_PAIR_HEADER) if i in (1, 2, 3) else curses.color_pair(_PAIR_DIM)
                if i == 4:
                    attr = curses.color_pair(_PAIR_ACCENT)
                _safe_addstr(self.scr, logo_y + i, cx, line, attr)

            menu_y = logo_y + len(logo) + 2
            for i, (key, desc) in enumerate(items):
                prefix = " ▸ " if i == sel else "   "
                attr = curses.color_pair(_PAIR_SELECTED) | curses.A_BOLD if i == sel else 0
                _safe_addstr(self.scr, menu_y + i, (w // 2) - 20, f"{prefix}{desc}", attr)

            opts_y = menu_y + len(items) + 2
            _safe_addstr(self.scr, opts_y, (w // 2) - 20, "Current settings:",
                         curses.color_pair(_PAIR_DIM))
            _safe_addstr(self.scr, opts_y + 1, (w // 2) - 18,
                         f"backend={self.options['backend']}  timeout={self.options['timeout']}s  "
                         f"unwind={self.options['unwind']}  ref={self.options['reference']}",
                         curses.color_pair(_PAIR_DIM))

            _draw_statusbar(self.scr, "↑/↓ Navigate  Enter Select  q Quit")
            self.scr.refresh()

            key = self.scr.getch()
            if key == curses.KEY_UP and sel > 0:
                sel -= 1
            elif key == curses.KEY_DOWN and sel < len(items) - 1:
                sel += 1
            elif key in (curses.KEY_ENTER, 10, 13):
                return items[sel][0]
            elif key == ord("q"):
                return "quit"

    # ── File picker ──────────────────────────────────────────────────────

    def _pick_file(self, start: str, filter_ext: str = ".json",
                   title: str = "Select File") -> str | None:
        """Navigate the filesystem and pick a file."""
        cwd = os.path.abspath(start)
        sel = 0
        scroll = 0

        while True:
            entries = self._list_dir(cwd, filter_ext)
            if sel >= len(entries):
                sel = max(0, len(entries) - 1)

            self.scr.clear()
            h, w = self.scr.getmaxyx()
            _draw_header(self.scr, title)
            _safe_addstr(self.scr, 2, 2, f"📂 {cwd}",
                         curses.color_pair(_PAIR_ACCENT))

            visible = h - 6
            if sel < scroll:
                scroll = sel
            if sel >= scroll + visible:
                scroll = sel - visible + 1

            for i in range(scroll, min(scroll + visible, len(entries))):
                row = 4 + (i - scroll)
                kind, name, path = entries[i]
                if kind == "dir":
                    icon = "📁 "
                    attr = curses.color_pair(_PAIR_HEADER)
                elif kind == "parent":
                    icon = "⬆  "
                    attr = curses.color_pair(_PAIR_DIM)
                else:
                    icon = "📄 "
                    attr = 0

                if i == sel:
                    attr = curses.color_pair(_PAIR_SELECTED) | curses.A_BOLD
                    _safe_addstr(self.scr, row, 2, " " * (w - 4), attr)

                _safe_addstr(self.scr, row, 3, f"{icon}{name}", attr)

            _draw_statusbar(self.scr,
                            "↑/↓ Navigate  Enter Select  Esc Cancel  "
                            f"[{sel+1}/{len(entries)}]")
            self.scr.refresh()

            key = self.scr.getch()
            if key == curses.KEY_UP and sel > 0:
                sel -= 1
            elif key == curses.KEY_DOWN and sel < len(entries) - 1:
                sel += 1
            elif key == curses.KEY_PPAGE:
                sel = max(0, sel - visible)
            elif key == curses.KEY_NPAGE:
                sel = min(len(entries) - 1, sel + visible)
            elif key in (curses.KEY_ENTER, 10, 13):
                if not entries:
                    continue
                kind, name, path = entries[sel]
                if kind == "parent":
                    cwd = os.path.dirname(cwd)
                    sel = 0
                    scroll = 0
                elif kind == "dir":
                    cwd = path
                    sel = 0
                    scroll = 0
                else:
                    return path
            elif key == 27:  # Esc
                return None

    def _pick_directory(self, start: str,
                        title: str = "Select Directory") -> str | None:
        """Navigate the filesystem and pick a directory."""
        cwd = os.path.abspath(start)
        sel = 0
        scroll = 0

        while True:
            entries = [("parent", "..", os.path.dirname(cwd))]
            try:
                for name in sorted(os.listdir(cwd)):
                    full = os.path.join(cwd, name)
                    if name.startswith("."):
                        continue
                    if os.path.isdir(full):
                        entries.append(("dir", name + "/", full))
            except PermissionError:
                pass

            if sel >= len(entries):
                sel = max(0, len(entries) - 1)

            self.scr.clear()
            h, w = self.scr.getmaxyx()
            _draw_header(self.scr, title)
            _safe_addstr(self.scr, 2, 2, f"📂 {cwd}",
                         curses.color_pair(_PAIR_ACCENT))
            _safe_addstr(self.scr, 3, 2, "Press Space to select this directory",
                         curses.color_pair(_PAIR_DIM))

            visible = h - 7
            if sel < scroll:
                scroll = sel
            if sel >= scroll + visible:
                scroll = sel - visible + 1

            for i in range(scroll, min(scroll + visible, len(entries))):
                row = 5 + (i - scroll)
                kind, name, path = entries[i]
                icon = "⬆  " if kind == "parent" else "📁 "
                attr = curses.color_pair(_PAIR_HEADER) if kind == "dir" else curses.color_pair(_PAIR_DIM)
                if i == sel:
                    attr = curses.color_pair(_PAIR_SELECTED) | curses.A_BOLD
                    _safe_addstr(self.scr, row, 2, " " * (w - 4), attr)
                _safe_addstr(self.scr, row, 3, f"{icon}{name}", attr)

            _draw_statusbar(self.scr,
                            "↑/↓ Navigate  Enter Open  Space Select here  Esc Cancel")
            self.scr.refresh()

            key = self.scr.getch()
            if key == curses.KEY_UP and sel > 0:
                sel -= 1
            elif key == curses.KEY_DOWN and sel < len(entries) - 1:
                sel += 1
            elif key in (curses.KEY_ENTER, 10, 13):
                if not entries:
                    continue
                kind, name, path = entries[sel]
                if kind == "parent":
                    cwd = os.path.dirname(cwd)
                else:
                    cwd = path
                sel = 0
                scroll = 0
            elif key == ord(" "):
                return cwd
            elif key == 27:
                return None

    def _list_dir(self, path: str, filter_ext: str) -> list[tuple[str, str, str]]:
        entries = [("parent", "..", os.path.dirname(path))]
        try:
            for name in sorted(os.listdir(path)):
                full = os.path.join(path, name)
                if name.startswith("."):
                    continue
                if os.path.isdir(full):
                    entries.append(("dir", name + "/", full))
                elif name.endswith(filter_ext):
                    entries.append(("file", name, full))
        except PermissionError:
            pass
        return entries

    # ── Options editor ───────────────────────────────────────────────────

    def _edit_options(self):
        fields = [
            ("backend", "Verifier backend", ["esbmc", "cbmc"]),
            ("reference", "Reference side", ["left", "right"]),
            ("timeout", "Timeout (seconds)", "int"),
            ("unwind", "Loop unwind bound", "int"),
            ("vec_size", "Vector size (blank=auto)", "str"),
            ("auto_bounds", "Auto-infer bounds", "bool"),
            ("validate_bounds", "Validate bounds (2×)", "bool"),
            ("interpret", "NL interpretation", "bool"),
            ("emit_harness", "Keep harness files", "bool"),
            ("docker_path", "Docker binary path", "str"),
            ("esbmc_image", "ESBMC Docker image", "str"),
        ]
        sel = 0

        while True:
            self.scr.clear()
            h, w = self.scr.getmaxyx()
            _draw_header(self.scr, "Options")

            for i, (key, label, kind) in enumerate(fields):
                row = 3 + i
                val = self.options[key]
                if isinstance(val, bool):
                    display = "● Yes" if val else "○ No"
                else:
                    display = str(val) if val != "" else "(default)"

                prefix = " ▸ " if i == sel else "   "
                attr = curses.color_pair(_PAIR_SELECTED) | curses.A_BOLD if i == sel else 0
                if i == sel:
                    _safe_addstr(self.scr, row, 2, " " * (w - 4), attr)

                _safe_addstr(self.scr, row, 2, prefix, attr)
                _safe_addstr(self.scr, row, 5, f"{label}: ", attr)
                val_attr = attr if i == sel else curses.color_pair(_PAIR_ACCENT)
                _safe_addstr(self.scr, row, 5 + len(label) + 2, display, val_attr)

            _draw_statusbar(self.scr,
                            "↑/↓ Navigate  Enter/Space Toggle  e Edit value  Esc Back")
            self.scr.refresh()

            key_press = self.scr.getch()
            if key_press == curses.KEY_UP and sel > 0:
                sel -= 1
            elif key_press == curses.KEY_DOWN and sel < len(fields) - 1:
                sel += 1
            elif key_press in (curses.KEY_ENTER, 10, 13, ord(" ")):
                key, label, kind = fields[sel]
                if isinstance(kind, list):
                    # Cycle through options
                    idx = kind.index(self.options[key]) if self.options[key] in kind else 0
                    self.options[key] = kind[(idx + 1) % len(kind)]
                elif kind == "bool":
                    self.options[key] = not self.options[key]
                elif kind == "int":
                    val = self._input_box(f"{label}:", str(self.options[key]))
                    if val is not None:
                        try:
                            self.options[key] = int(val)
                        except ValueError:
                            pass
                elif kind == "str":
                    val = self._input_box(f"{label}:", str(self.options[key]))
                    if val is not None:
                        self.options[key] = val
            elif key_press == ord("e"):
                key, label, kind = fields[sel]
                if kind not in ("bool",) and not isinstance(kind, list):
                    val = self._input_box(f"{label}:", str(self.options[key]))
                    if val is not None:
                        if kind == "int":
                            try:
                                self.options[key] = int(val)
                            except ValueError:
                                pass
                        else:
                            self.options[key] = val
            elif key_press == 27:
                return

    def _input_box(self, prompt: str, default: str = "") -> str | None:
        """Show a small input dialog and return the entered text."""
        h, w = self.scr.getmaxyx()
        box_w = min(60, w - 4)
        box_h = 5
        box_y = h // 2 - 2
        box_x = (w - box_w) // 2

        win = curses.newwin(box_h, box_w, box_y, box_x)
        win.keypad(True)
        curses.curs_set(1)

        buf = list(default)
        cursor = len(buf)

        while True:
            win.clear()
            win.border()
            _safe_addstr(win, 1, 2, prompt[:box_w - 4],
                         curses.color_pair(_PAIR_ACCENT))
            text = "".join(buf)
            _safe_addstr(win, 2, 2, text[:box_w - 4], 0)
            _safe_addstr(win, 3, 2, "Enter OK  Esc Cancel",
                         curses.color_pair(_PAIR_DIM))
            try:
                win.move(2, min(2 + cursor, box_w - 2))
            except curses.error:
                pass
            win.refresh()

            ch = win.getch()
            if ch in (curses.KEY_ENTER, 10, 13):
                curses.curs_set(0)
                return "".join(buf)
            elif ch == 27:
                curses.curs_set(0)
                return None
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                if cursor > 0:
                    buf.pop(cursor - 1)
                    cursor -= 1
            elif ch == curses.KEY_DC:
                if cursor < len(buf):
                    buf.pop(cursor)
            elif ch == curses.KEY_LEFT:
                cursor = max(0, cursor - 1)
            elif ch == curses.KEY_RIGHT:
                cursor = min(len(buf), cursor + 1)
            elif ch == curses.KEY_HOME:
                cursor = 0
            elif ch == curses.KEY_END:
                cursor = len(buf)
            elif 32 <= ch <= 126:
                buf.insert(cursor, chr(ch))
                cursor += 1

    # ── Compare flow ─────────────────────────────────────────────────────

    def _flow_compare(self):
        filepath = self._pick_file(self.start_dir, ".json", "Select Spec File")
        if filepath is None:
            return

        try:
            with open(filepath) as f:
                data = json.load(f)
            inp = CompareInput.from_dict(data)
        except Exception as e:
            self._show_message(f"Error loading {filepath}:\n{e}", is_error=True)
            return

        # Preview
        if not self._preview_input(inp, filepath):
            return

        result = self._run_comparison(inp)
        if result is not None:
            self._show_results(inp, result, os.path.basename(filepath))

    def _flow_batch(self):
        dirpath = self._pick_directory(self.start_dir, "Select Specs Directory")
        if dirpath is None:
            return

        files = sorted(Path(dirpath).glob("*.json"))
        if not files:
            self._show_message(f"No .json files found in:\n{dirpath}", is_error=True)
            return

        results = []
        for i, fpath in enumerate(files):
            self.scr.clear()
            h, w = self.scr.getmaxyx()
            _draw_header(self.scr, "Batch Compare")

            progress = (i / len(files)) * 100
            bar_w = min(40, w - 20)
            filled = int(bar_w * i / len(files))
            bar = "█" * filled + "░" * (bar_w - filled)

            _safe_addstr(self.scr, 3, 4, f"Progress: [{bar}] {progress:.0f}%",
                         curses.color_pair(_PAIR_ACCENT))
            _safe_addstr(self.scr, 5, 4, f"File {i+1}/{len(files)}: {fpath.name}",
                         0)
            _safe_addstr(self.scr, 7, 4, "Running verifier...",
                         curses.color_pair(_PAIR_DIM))

            # Show results so far
            for j, (name, r) in enumerate(results[-8:]):
                row = 9 + j
                eq = "✔ equivalent" if r.equivalent else "✘ not equivalent"
                attr = curses.color_pair(_PAIR_PROVED) if r.equivalent else curses.color_pair(_PAIR_REFUTED)
                _safe_addstr(self.scr, row, 6, f"{name}: {eq}", attr)

            _draw_statusbar(self.scr, "Running... (cannot interrupt)")
            self.scr.refresh()

            try:
                with open(fpath) as f:
                    data = json.load(f)
                inp = CompareInput.from_dict(data)
                result = self._do_compare(inp)
                results.append((fpath.stem, result))
            except Exception:
                pass

        if results:
            self._show_batch_results(results)

    # ── Preview ──────────────────────────────────────────────────────────

    def _preview_input(self, inp: CompareInput, filepath: str) -> bool:
        """Show spec preview; return True to proceed, False to cancel."""
        while True:
            self.scr.clear()
            h, w = self.scr.getmaxyx()
            _draw_header(self.scr, "Spec Preview")

            _safe_addstr(self.scr, 2, 2, f"File: {os.path.basename(filepath)}",
                         curses.color_pair(_PAIR_DIM))

            sig = (f"{inp.function.return_type} {inp.function.name}("
                   + ", ".join(f"{p.type} {p.name}" for p in inp.function.params)
                   + ")")
            _safe_addstr(self.scr, 4, 2, "Function:", curses.color_pair(_PAIR_ACCENT))
            _safe_addstr(self.scr, 4, 12, sig, curses.A_BOLD)

            col_w = max(20, (w - 6) // 2)

            # Left specs
            _safe_addstr(self.scr, 6, 2, f"── Left ({inp.left.label or 'left'}) ──",
                         curses.color_pair(_PAIR_HEADER) | curses.A_BOLD)
            row = 7
            if inp.left.preconditions:
                _safe_addstr(self.scr, row, 4, "Pre:", curses.color_pair(_PAIR_DIM))
                for pre in inp.left.preconditions[:4]:
                    row += 1
                    _safe_addstr(self.scr, row, 6, pre[:col_w - 8], 0)
            row += 1
            if inp.left.postconditions:
                _safe_addstr(self.scr, row, 4, "Post:", curses.color_pair(_PAIR_DIM))
                for post in inp.left.postconditions[:4]:
                    row += 1
                    _safe_addstr(self.scr, row, 6, post[:col_w - 8], 0)

            # Right specs
            right_y = 6
            rx = col_w + 4
            _safe_addstr(self.scr, right_y, rx,
                         f"── Right ({inp.right.label or 'right'}) ──",
                         curses.color_pair(_PAIR_HEADER) | curses.A_BOLD)
            row = right_y + 1
            if inp.right.preconditions:
                _safe_addstr(self.scr, row, rx + 2, "Pre:", curses.color_pair(_PAIR_DIM))
                for pre in inp.right.preconditions[:4]:
                    row += 1
                    _safe_addstr(self.scr, row, rx + 4, pre[:col_w - 8], 0)
            row += 1
            if inp.right.postconditions:
                _safe_addstr(self.scr, row, rx + 2, "Post:", curses.color_pair(_PAIR_DIM))
                for post in inp.right.postconditions[:4]:
                    row += 1
                    _safe_addstr(self.scr, row, rx + 4, post[:col_w - 8], 0)

            _draw_statusbar(self.scr, "Enter Run comparison  o Options  Esc Cancel")
            self.scr.refresh()

            key = self.scr.getch()
            if key in (curses.KEY_ENTER, 10, 13):
                return True
            elif key == ord("o"):
                self._edit_options()
            elif key == 27:
                return False

    # ── Run comparison ───────────────────────────────────────────────────

    def _make_backend(self):
        if self.options["backend"] == "cbmc":
            return CBMCBackend(unwind=self.options["unwind"])
        return ESBMCBackend(
            unwind=self.options["unwind"],
            docker_path=self.options["docker_path"],
            image=self.options["esbmc_image"],
        )

    def _do_compare(self, inp: CompareInput) -> CompareResult:
        backend = self._make_backend()
        vec_size = None
        vs = self.options["vec_size"]
        if isinstance(vs, int):
            vec_size = vs
        elif isinstance(vs, str) and vs.strip().isdigit():
            vec_size = int(vs.strip())

        return compare(
            inp, backend,
            timeout=self.options["timeout"],
            reference=self.options["reference"],
            emit_harness=self.options["emit_harness"],
            auto_bounds=self.options["auto_bounds"],
            vec_size=vec_size,
            validate_bounds=self.options["validate_bounds"],
        )

    def _run_comparison(self, inp: CompareInput) -> CompareResult | None:
        """Run with a spinner animation."""
        result_holder: list[CompareResult | None] = [None]
        error_holder: list[str | None] = [None]
        done = threading.Event()

        def worker():
            try:
                result_holder[0] = self._do_compare(inp)
            except Exception as e:
                error_holder[0] = str(e)
            done.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        frame = 0
        checks = [
            "Checking preconditions (L→R)...",
            "Checking preconditions (R→L)...",
            "Checking postconditions (L→R)...",
            "Checking postconditions (R→L)...",
            "Checking vacuity...",
            "Finalizing...",
        ]

        while not done.is_set():
            self.scr.clear()
            h, w = self.scr.getmaxyx()
            _draw_header(self.scr, "Running Comparison")

            sig = (f"{inp.function.return_type} {inp.function.name}("
                   + ", ".join(f"{p.type} {p.name}" for p in inp.function.params)
                   + ")")
            _safe_addstr(self.scr, 3, 4, f"Function: {sig}",
                         curses.color_pair(_PAIR_DIM))
            _safe_addstr(self.scr, 4, 4, f"Backend: {self.options['backend']}  "
                         f"Timeout: {self.options['timeout']}s  "
                         f"Unwind: {self.options['unwind']}",
                         curses.color_pair(_PAIR_DIM))

            cy = h // 2 - 2
            s = spinner[frame % len(spinner)]
            _safe_addstr(self.scr, cy, (w // 2) - 15,
                         f"  {s}  Running bounded model checker...",
                         curses.color_pair(_PAIR_ACCENT) | curses.A_BOLD)

            # Show check phases
            elapsed = frame // 5
            phase = min(elapsed, len(checks) - 1)
            for i in range(phase + 1):
                icon = "✔" if i < phase else s
                attr = curses.color_pair(_PAIR_PROVED) if i < phase else curses.color_pair(_PAIR_DIM)
                _safe_addstr(self.scr, cy + 2 + i, (w // 2) - 15,
                             f"  {icon}  {checks[i]}", attr)

            _draw_statusbar(self.scr, "Please wait...")
            self.scr.refresh()

            frame += 1
            done.wait(timeout=0.1)

        if error_holder[0]:
            self._show_message(f"Comparison failed:\n{error_holder[0]}", is_error=True)
            return None

        return result_holder[0]

    # ── Results view ─────────────────────────────────────────────────────

    def _show_results(self, inp: CompareInput, result: CompareResult,
                      filename: str = ""):
        """Interactive results viewer."""
        checks = self._build_check_list(result)
        sel = 0
        scroll = 0

        while True:
            self.scr.clear()
            h, w = self.scr.getmaxyx()
            _draw_header(self.scr, "Results")

            # Summary line
            sig = f"{inp.function.return_type} {inp.function.name}(…)"
            _safe_addstr(self.scr, 2, 2, f"{filename}  {sig}",
                         curses.color_pair(_PAIR_DIM))

            eq_text = "✔  EQUIVALENT" if result.equivalent else "✘  NOT EQUIVALENT"
            eq_attr = curses.color_pair(_PAIR_PROVED) if result.equivalent else curses.color_pair(_PAIR_REFUTED)
            _safe_addstr(self.scr, 2, w - len(eq_text) - 4, eq_text,
                         eq_attr | curses.A_BOLD)

            # Verifier info
            _safe_addstr(self.scr, 3, 2,
                         f"verifier={result.verifier}  bounds={result.bounds}",
                         curses.color_pair(_PAIR_DIM))

            # Verdict table
            table_y = 5
            _safe_addstr(self.scr, table_y, 2,
                         f"{'Check':<28} {'Verdict':<12} {'Vacuous':<8}",
                         curses.color_pair(_PAIR_HEADER) | curses.A_BOLD)
            _safe_addstr(self.scr, table_y + 1, 2, "─" * min(56, w - 4),
                         curses.color_pair(_PAIR_DIM))

            detail_start = table_y + 2 + len(checks) + 2
            visible_checks = min(len(checks), h - detail_start - 4)

            for i in range(visible_checks):
                row = table_y + 2 + i
                name, ir = checks[i]
                v = ir.verdict.value
                icon = _VERDICT_ICONS.get(v, " ")
                vac = "yes" if ir.vacuous else ""

                if i == sel:
                    _safe_addstr(self.scr, row, 2, " " * min(56, w - 4),
                                 curses.color_pair(_PAIR_SELECTED))
                    _safe_addstr(self.scr, row, 2, f" ▸ {name:<25}", 
                                 curses.color_pair(_PAIR_SELECTED) | curses.A_BOLD)
                    _safe_addstr(self.scr, row, 31, f"{icon} {v:<10}",
                                 curses.color_pair(_PAIR_SELECTED) | curses.A_BOLD)
                    _safe_addstr(self.scr, row, 44, vac,
                                 curses.color_pair(_PAIR_SELECTED))
                else:
                    _safe_addstr(self.scr, row, 2, f"   {name:<25}", 0)
                    _safe_addstr(self.scr, row, 31, f"{icon} {v:<10}",
                                 _verdict_attr(v))
                    _safe_addstr(self.scr, row, 44, vac,
                                 curses.color_pair(_PAIR_VACUOUS) if vac else 0)

            # Detail panel for selected check
            detail_y = table_y + 2 + visible_checks + 1
            if detail_y < h - 3:
                _safe_addstr(self.scr, detail_y, 2, "─" * min(56, w - 4),
                             curses.color_pair(_PAIR_DIM))
                detail_y += 1

                name, ir = checks[sel]
                if ir.counterexample:
                    _safe_addstr(self.scr, detail_y, 2, "Counterexample:",
                                 curses.color_pair(_PAIR_ACCENT) | curses.A_BOLD)
                    detail_y += 1
                    for var, val in list(ir.counterexample.items())[:h - detail_y - 2]:
                        _safe_addstr(self.scr, detail_y, 4, f"{var}",
                                     curses.color_pair(_PAIR_HEADER))
                        _safe_addstr(self.scr, detail_y,
                                     4 + len(var), f" = {val}",
                                     0)
                        detail_y += 1
                elif ir.diagnostics:
                    _safe_addstr(self.scr, detail_y, 2, "Diagnostics:",
                                 curses.color_pair(_PAIR_ACCENT) | curses.A_BOLD)
                    detail_y += 1
                    for diag in ir.diagnostics[:h - detail_y - 2]:
                        _safe_addstr(self.scr, detail_y, 4,
                                     diag[:w - 6],
                                     curses.color_pair(_PAIR_DIM))
                        detail_y += 1

            _draw_statusbar(self.scr,
                            "↑/↓ Select check  j/k Export JSON  Esc Back")
            self.scr.refresh()

            key = self.scr.getch()
            if key == curses.KEY_UP and sel > 0:
                sel -= 1
            elif key == curses.KEY_DOWN and sel < len(checks) - 1:
                sel += 1
            elif key == ord("j") or key == ord("k"):
                self._export_json(inp, result, filename)
            elif key == 27:
                return

    def _build_check_list(self, r: CompareResult) -> list[tuple[str, "ImplicationResult"]]:
        """Build ordered list of checks for display."""
        from .types import ImplicationResult
        checks = []
        pairs = [
            ("pre: L→R", r.pre_left_implies_right),
            ("pre: R→L", r.pre_right_implies_left),
            ("post: L→R", r.left_implies_right),
            ("post: R→L", r.right_implies_left),
        ]
        if r.post_soundness is not None and r.post_soundness is not r.left_implies_right:
            pairs.append(("post_soundness", r.post_soundness))
        if r.post_completeness is not None and r.post_completeness is not r.right_implies_left:
            pairs.append(("post_completeness", r.post_completeness))
        if r.pre_soundness is not None and r.pre_soundness is not r.pre_right_implies_left:
            pairs.append(("pre_soundness", r.pre_soundness))
        if r.pre_completeness is not None and r.pre_completeness is not r.pre_left_implies_right:
            pairs.append(("pre_completeness", r.pre_completeness))

        # Add domain aliases with mapped names
        if r.post_soundness is not None:
            has_alias = False
            for name, ir in pairs:
                if ir is r.post_soundness and name != "post_soundness":
                    has_alias = True
                    break
            if not has_alias:
                pass  # already added above

        if r.post_sound_under_gt_pre is not None:
            pairs.append(("post_sound (GT pre)", r.post_sound_under_gt_pre))
        if r.post_complete_under_gt_pre is not None:
            pairs.append(("post_complete (GT pre)", r.post_complete_under_gt_pre))

        for name, ir in pairs:
            if ir is not None:
                checks.append((name, ir))
        return checks

    def _export_json(self, inp: CompareInput, result: CompareResult,
                     filename: str):
        """Export results to a JSON file."""
        from .cli import _result_to_dict
        out = _result_to_dict(result)
        out_name = filename.replace(".json", "_results.json")
        out_path = self._input_box("Export to:", out_name)
        if out_path is None:
            return
        try:
            with open(out_path, "w") as f:
                json.dump(out, f, indent=2)
            self._show_message(f"Results exported to:\n{out_path}")
        except Exception as e:
            self._show_message(f"Export failed:\n{e}", is_error=True)

    # ── Batch results ────────────────────────────────────────────────────

    def _show_batch_results(self, results: list[tuple[str, CompareResult]]):
        sel = 0
        scroll = 0

        eq_count = sum(1 for _, r in results if r.equivalent)
        total = len(results)

        while True:
            self.scr.clear()
            h, w = self.scr.getmaxyx()
            _draw_header(self.scr, "Batch Results")

            _safe_addstr(self.scr, 2, 2,
                         f"Total: {total}   Equivalent: {eq_count}   "
                         f"Mismatches: {total - eq_count}",
                         curses.color_pair(_PAIR_ACCENT))

            # Summary bar
            if total > 0:
                bar_w = min(50, w - 10)
                filled = int(bar_w * eq_count / total)
                bar = "█" * filled + "░" * (bar_w - filled)
                pct = eq_count / total * 100
                _safe_addstr(self.scr, 3, 2, f"[{bar}] {pct:.0f}%",
                             curses.color_pair(_PAIR_PROVED if pct > 50 else _PAIR_REFUTED))

            # Table header
            table_y = 5
            _safe_addstr(self.scr, table_y, 2,
                         f"{'File':<30} {'pre L→R':<10} {'pre R→L':<10} "
                         f"{'post L→R':<10} {'post R→L':<10} {'Eq'}",
                         curses.color_pair(_PAIR_HEADER) | curses.A_BOLD)
            _safe_addstr(self.scr, table_y + 1, 2, "─" * min(75, w - 4),
                         curses.color_pair(_PAIR_DIM))

            visible = h - table_y - 5
            if sel < scroll:
                scroll = sel
            if sel >= scroll + visible:
                scroll = sel - visible + 1

            for i in range(scroll, min(scroll + visible, total)):
                row = table_y + 2 + (i - scroll)
                name, r = results[i]
                plr = r.pre_left_implies_right.verdict.value
                prl = r.pre_right_implies_left.verdict.value
                lr = r.left_implies_right.verdict.value
                rl = r.right_implies_left.verdict.value
                eq = "✔" if r.equivalent else "✘"

                if i == sel:
                    _safe_addstr(self.scr, row, 2, " " * min(75, w - 4),
                                 curses.color_pair(_PAIR_SELECTED))

                name_trunc = name[:28]
                attr = curses.color_pair(_PAIR_SELECTED) if i == sel else 0
                _safe_addstr(self.scr, row, 2, f"  {name_trunc:<28}", attr)

                for col, v in [(32, plr), (42, prl), (52, lr), (62, rl)]:
                    va = curses.color_pair(_PAIR_SELECTED) if i == sel else _verdict_attr(v)
                    _safe_addstr(self.scr, row, col, f"{v:<10}", va)

                eq_attr = curses.color_pair(_PAIR_SELECTED) if i == sel else (
                    curses.color_pair(_PAIR_PROVED) if r.equivalent else curses.color_pair(_PAIR_REFUTED))
                _safe_addstr(self.scr, row, 72, eq, eq_attr)

            _draw_statusbar(self.scr, "↑/↓ Navigate  Enter View details  Esc Back")
            self.scr.refresh()

            key = self.scr.getch()
            if key == curses.KEY_UP and sel > 0:
                sel -= 1
            elif key == curses.KEY_DOWN and sel < total - 1:
                sel += 1
            elif key == curses.KEY_PPAGE:
                sel = max(0, sel - visible)
            elif key == curses.KEY_NPAGE:
                sel = min(total - 1, sel + visible)
            elif key in (curses.KEY_ENTER, 10, 13):
                name, r = results[sel]
                # Create a minimal CompareInput for display
                dummy_inp = CompareInput(
                    function=FunctionSig(name=name, return_type=""),
                )
                self._show_results(dummy_inp, r, name)
            elif key == 27:
                return

    # ── Utility ──────────────────────────────────────────────────────────

    def _show_message(self, text: str, is_error: bool = False):
        """Show a modal message box."""
        self.scr.clear()
        h, w = self.scr.getmaxyx()
        _draw_header(self.scr, "Error" if is_error else "Info")

        lines = text.split("\n")
        attr = curses.color_pair(_PAIR_REFUTED) if is_error else curses.color_pair(_PAIR_ACCENT)
        for i, line in enumerate(lines[:h - 5]):
            _safe_addstr(self.scr, 3 + i, 4, line[:w - 6], attr)

        _draw_statusbar(self.scr, "Press any key to continue")
        self.scr.refresh()
        self.scr.getch()


# ── Entry point ──────────────────────────────────────────────────────────────

def run_tui(start_dir: str = "."):
    """Launch the refine TUI."""
    curses.wrapper(lambda stdscr: RefineTUI(stdscr, start_dir).run())
