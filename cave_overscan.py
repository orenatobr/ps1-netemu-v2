"""Code cave: the vertical overscan, DuckStation's fixed window clipped to what the game declares.

The display handler turns the lines the game programs with GP1 (`Y1` at r22+0x1c, `Y2` at r22+0x20, 0 when
none) into the picture quad (f7 = top, f4 = bottom, -1..1 is the frame). The stock NTSC block ends with a
branch to the shared tail at 0x115820; it now jumps here instead. This code

  * runs a verbatim copy of that tail, so every register the rest of the handler reads (f1 is the height of
    the texture span, f3 the width, cr7 the interlace compare) is exactly what the stock code leaves;
  * then replaces the quad's top and bottom: DuckStation's overscan window, video lines 24 to 248
    (gpu.cpp NTSC_OVERSCAN_VERTICAL_ACTIVE_*), clipped to the declared range so a game that declares less
    (Xenogears, lines 26 to 242) has no black lines at the edges, stretched over the whole frame:
    top = 2*(Y1 - first)/span - 1, bottom = 2*(Y2 - first)/span - 1;
  * rejoins the tail at 0x1158ac, where the stock code subtracts 1 from f13 to get the bottom.

The result depends only on what the game declares, so nothing adjusts while it runs. Float constants live in
free TOC slots (24.0, 2.0, 248.0). PAL keeps the stock tail.
"""
from __future__ import annotations

from ppcasm import assemble

HOOK_VA = 0x115AE0  # stock: b 0x115820
RESUME = 0x1158AC  # fsubs f4,f13,f2 (f4 = f13 - 1), then the beq at 0x1158b0
TOC_FIRST, TOC_TWO, TOC_LAST = -0x55DC, -0x55D8, -0x55D4  # 24.0, 2.0, 248.0


def program() -> list[tuple]:
    return [
        # verbatim copy of the shared tail 0x115820..0x1158a8 (the doubling at 0x115894/0x1158a0 stays a fadds)
        ("lwz", "r0", 0x20, "r22"), ("fsubs", "f12", "f0", "f13"), ("fmr", "f0", "f11"), ("cmpwi", "r0", 0),
        ("beq", "t1"),
        ("subf", "r0", "r9", "r0"), ("clrldi", "r0", "r0", 32), ("std", "r0", 0xC0, "r1"),
        ("lfd", "f10", 0xC0, "r1"), ("fcfid", "f10", "f10"), ("frsp", "f0", "f10"),
        ("label", "t1"),
        ("lwz", "r0", 0xC, "r22"), ("fdivs", "f0", "f0", "f11"), ("cmpwi6", "r30", 0),
        ("std", "r0", 0xC0, "r1"), ("lfd", "f11", 0xC0, "r1"), ("fcfid", "f11", "f11"), ("frsp", "f10", "f11"),
        ("fmuls", "f1", "f0", "f10"), ("beq6", "t2"), ("fadds", "f12", "f12", "f12"),
        ("label", "t2"),
        ("fadds", "f13", "f12", "f1"), ("clrldi", "r0", "r5", 32), ("fadds", "f11", "f8", "f9"), ("cmpwi", "r4", 2),
        ("std", "r0", 0xC0, "r1"), ("fdivs", "f0", "f12", "f10"), ("lfd", "f3", 0xC0, "r1"), ("fcfid", "f3", "f3"),
        ("fadds", "f0", "f0", "f0"), ("fdivs", "f13", "f13", "f10"), ("frsp", "f12", "f3"),
        ("fadds", "f13", "f13", "f13"), ("fsubs", "f7", "f0", "f2"), ("fsubs", "f3", "f12", "f11"),
        # the quad: f0 = Y1, f10 = Y2 (r9 still holds Y1 for the stock code that follows)
        ("lwz", "r0", 0x20, "r22"), ("cmpwi", "r0", 0), ("bne", "has"), ("addi", "r0", "r9", 240),  # none: 240 lines
        ("label", "has"),
        ("std", "r9", 0xC0, "r1"), ("lfd", "f0", 0xC0, "r1"), ("fcfid", "f0", "f0"), ("frsp", "f0", "f0"),
        ("std", "r0", 0xC0, "r1"), ("lfd", "f10", 0xC0, "r1"), ("fcfid", "f10", "f10"), ("frsp", "f10", "f10"),
        ("lfs", "f11", TOC_FIRST, "r2"), ("lfs", "f12", TOC_LAST, "r2"),
        ("fsubs", "f13", "f0", "f11"), ("fsel", "f13", "f13", "f0", "f11"),  # f13 = max(Y1, 24)
        ("fsubs", "f4", "f10", "f12"), ("fsel", "f4", "f4", "f12", "f10"),  # f4 = min(Y2, 248)
        ("fsubs", "f6", "f4", "f13"), ("fsubs", "f7", "f6", "f2"),  # span; span - 1 < 0 means an empty range
        ("fsel", "f13", "f7", "f13", "f11"), ("fsel", "f4", "f7", "f4", "f12"),  # then the whole window
        ("fsubs", "f6", "f4", "f13"), ("lfs", "f12", TOC_TWO, "r2"), ("fdivs", "f6", "f12", "f6"),  # f6 = 2 / span
        ("fsubs", "f7", "f0", "f13"), ("fmuls", "f7", "f7", "f6"), ("fsubs", "f7", "f7", "f2"),  # top
        ("fsubs", "f13", "f10", "f13"), ("fmuls", "f13", "f13", "f6"),  # bottom + 1, the stock code subtracts the 1
        ("clrldi", "r0", "r5", 32), ("cmpwi", "r4", 2), ("b", RESUME),
    ]


def build(cave_va: int) -> tuple[list[tuple[int, int, int, str]], int]:
    """Return the (va, old, new, what) patches and the number of bytes used from `cave_va`."""
    words = assemble(cave_va, program())
    stock = assemble(HOOK_VA, [("b", 0x115820)])[0]
    patches = [(cave_va + 4 * i, 0, w, "overscan cave code") for i, w in enumerate(words)]
    patches.append((HOOK_VA, stock, assemble(HOOK_VA, [("b", cave_va)])[0], "jump to the overscan cave"))
    return patches, 4 * len(words)
