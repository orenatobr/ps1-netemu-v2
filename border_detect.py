"""Per-frame black-border detection (reference for the emulator port; not yet in the .self).

A row or column is a border when every pixel is black. The window that holds the picture is
tracked with hysteresis so dark scenes and fades do not make the zoom jump: a frame with no
content keeps the last window, and a new window is only taken after it repeats HOLD frames.
"""
from __future__ import annotations

from dataclasses import dataclass

Frame = list[list[int]]  # rows of 15-bit PS1 pixels; 0 is black
HOLD = 4
MIN_FRACTION = 0.5  # never crop below half of the frame in either axis


def bounds(frame: Frame) -> tuple[int, int, int, int] | None:
    """Return (top, bottom, left, right) of the non-black area (bottom/right exclusive), or None."""
    rows = [y for y, r in enumerate(frame) if any(r)]
    if not rows:
        return None
    cols = [x for x in range(len(frame[0])) if any(r[x] for r in frame)]
    return rows[0], rows[-1] + 1, cols[0], cols[-1] + 1


@dataclass
class Tracker:
    """Keeps the current window and moves it only after HOLD consistent frames."""

    window: tuple[int, int, int, int] | None = None
    pending: tuple[int, int, int, int] | None = None
    seen: int = 0

    def update(self, frame: Frame) -> tuple[int, int, int, int]:
        full = (0, len(frame), 0, len(frame[0]))
        b = bounds(frame)
        if b is None or (b[1] - b[0]) < MIN_FRACTION * full[1] or (b[3] - b[2]) < MIN_FRACTION * full[3]:
            return self.window or full  # fade, black frame or tiny content: keep the last window
        if self.window is None:
            self.window = b
        elif b == self.window:
            self.pending, self.seen = None, 0
        else:
            self.seen = self.seen + 1 if b == self.pending else 1
            self.pending = b
            if self.seen >= HOLD:
                self.window, self.pending, self.seen = b, None, 0
        return self.window


def _frame(h: int, w: int, top: int, bottom: int, left: int = 0, right: int | None = None, v: int = 1) -> Frame:
    right = w if right is None else right
    return [[v if top <= y < bottom and left <= x < right else 0 for x in range(w)] for y in range(h)]


def test_bounds_finds_black_borders() -> None:
    assert bounds(_frame(24, 32, 4, 20, 2, 30)) == (4, 20, 2, 30)
    assert bounds(_frame(24, 32, 0, 0)) is None


def test_black_frame_keeps_last_window() -> None:
    t = Tracker()
    w = t.update(_frame(24, 32, 4, 24))
    assert t.update(_frame(24, 32, 0, 0)) == w == (4, 24, 0, 32)


def test_window_changes_only_after_hold_frames() -> None:
    t = Tracker()
    old = t.update(_frame(24, 32, 4, 24))
    for _ in range(HOLD - 1):
        assert t.update(_frame(24, 32, 8, 24)) == old
    assert t.update(_frame(24, 32, 8, 24)) == (8, 24, 0, 32)


def test_tiny_content_is_ignored() -> None:
    t = Tracker()
    w = t.update(_frame(24, 32, 4, 24))
    assert t.update(_frame(24, 32, 10, 12)) == w
