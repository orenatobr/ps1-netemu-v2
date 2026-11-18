"""Code cave: the vertical overscan, DuckStation's fixed window clipped to what the game declares.

The display handler turns the lines the game programs with GP1 (`Y1` at r22+0x1c, `Y2` at r22+0x20, 0 when
none) into the picture quad (f7 = top, f4 = bottom, -1..1 is the frame). The stock NTSC block ends with a
branch to the shared tail at 0x115820; it now jumps here instead, and this code

  * takes DuckStation's overscan window, video lines 24 to 248 (gpu.cpp NTSC_OVERSCAN_VERTICAL_ACTIVE_*);
  * clips it to the declared range, so a game that declares less (Xenogears, lines 26 to 242) is not shown
    with black lines at the edges, and a game that declares more is cropped to the window;
  * stretches that span over the whole frame: top = 2*(Y1 - first)/span - 1, bottom = 2*(Y2 - first)/span - 1;
  * finishes what the tail would have (width in f3, compare in cr7) and rejoins it at 0x1158ac.

The result depends only on what the game declares, so nothing adjusts while it runs. Float constants live in
three free TOC slots (24.0, 248.0, 2.0). PAL keeps the stock tail.
"""
from __future__ import annotations

from ppcasm import assemble

HOOK_VA = 0x115AE0  # stock: b 0x115820
RESUME = 0x1158AC  # fsubs f4,f13,f2 (f4 = f13 - 1), then the beq at 0x1158b0
TOC_FIRST, TOC_LAST, TOC_TWO = -0x55E0, -0x55DC, -0x55D8  # 24.0, 248.0, 2.0
FIRST, LAST = 24, 248


def program() -> list[tuple]:
    return [
        ("lwz", "r9", 0x1C, "r22"), ("lwz", "r0", 0x20, "r22"), ("cmpwi", "r0", 0), ("bne", "has"),
        ("addi", "r0", "r9", 240),  # no end line declared: 240 lines
        ("label", "has"),
        ("std", "r9", 0xC0, "r1"), ("lfd", "f0", 0xC0, "r1"), ("fcfid", "f0", "f0"), ("frsp", "f0", "f0"),  # Y1
        ("std", "r0", 0xC0, "r1"), ("lfd", "f1", 0xC0, "r1"), ("fcfid", "f1", "f1"), ("frsp", "f1", "f1"),  # Y2
        ("lfs", "f10", TOC_FIRST, "r2"), ("lfs", "f11", TOC_LAST, "r2"), ("lfs", "f12", TOC_TWO, "r2"),
        ("fsubs", "f13", "f0", "f10"), ("fsel", "f13", "f13", "f0", "f10"),  # f13 = max(Y1, 24)
        ("fsubs", "f3", "f1", "f11"), ("fsel", "f3", "f3", "f11", "f1"),  # f3 = min(Y2, 248)
        ("fsubs", "f6", "f3", "f13"), ("fsubs", "f7", "f6", "f2"),  # span; span - 1 < 0 means an empty range
        ("fsel", "f13", "f7", "f13", "f10"), ("fsel", "f3", "f7", "f3", "f11"),  # then the whole window
        ("fsubs", "f6", "f3", "f13"), ("fdivs", "f6", "f12", "f6"),  # f6 = 2 / span
        ("fsubs", "f7", "f0", "f13"), ("fmuls", "f7", "f7", "f6"), ("fsubs", "f7", "f7", "f2"),  # top
        ("fsubs", "f13", "f1", "f13"), ("fmuls", "f13", "f13", "f6"),  # bottom + 1, the tail subtracts the 1
        ("clrldi", "r0", "r5", 32), ("std", "r0", 0xC0, "r1"), ("lfd", "f3", 0xC0, "r1"),
        ("fcfid", "f3", "f3"), ("frsp", "f12", "f3"), ("fadds", "f11", "f8", "f9"), ("fsubs", "f3", "f12", "f11"),
        ("cmpwi", "r4", 2), ("b", RESUME),
    ]


def build(cave_va: int) -> tuple[list[tuple[int, int, int, str]], int]:
    """Return the (va, old, new, what) patches and the number of bytes used from `cave_va`."""
    words = assemble(cave_va, program())
    stock = assemble(HOOK_VA, [("b", 0x115820)])[0]
    patches = [(cave_va + 4 * i, 0, w, "overscan cave code") for i, w in enumerate(words)]
    patches.append((HOOK_VA, stock, assemble(HOOK_VA, [("b", cave_va)])[0], "jump to the overscan cave"))
    return patches, 4 * len(words)
