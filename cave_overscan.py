"""Code cave: the vertical overscan, the active video lines clipped to what the game declares.

The display handler turns the lines the game programs with GP1 (`Y1` at r22+0x1c, `Y2` at r22+0x20, 0 when
none) into the picture quad (f7 = top, f4 = bottom, -1..1 is the frame). The stock NTSC block ends with a
branch to the shared tail at 0x115820; it now jumps here instead. This code

  * runs a verbatim copy of that tail, so every register the rest of the handler reads (f1 is the height of
    the texture span, f3 the width, cr7 the interlace compare) is exactly what the stock code leaves;
  * then replaces the quad's top and bottom: the video lines 16 to 248 (the active lines 16 to 256
    minus the 8 last, which games leave black and DuckStation also hides), clipped to the declared range so a game that declares less
    (Xenogears, lines 26 to 242) has no black lines at the edges, stretched over the whole frame:
    top = 2*(Y1 - first)/span - 1, bottom = 2*(Y2 - first)/span - 1;
  * rejoins the tail at 0x1158ac, where the stock code subtracts 1 from f13 to get the bottom.

The result depends only on what the game declares, so nothing adjusts while it runs. Float constants live in
free TOC slots (16.0, 2.0, 248.0). The first line can be raised by the owner with SELECT + UP / DOWN (cave_dpad.py),
in steps of 2 lines up to 24, kept in the integer slot at 0x1BE760 (0 after a restart). PAL keeps the stock tail.
"""
from __future__ import annotations

from ppcasm import assemble

NAME_OFF = -0x1880  # the card name of the game (cave_percard NAME_VA 0x1BE780, "SLUS-00067.VM1") from 0x1c0000
DELTA, DELTA_END = -0x18A0, -0x1894  # the owner's adjust of the top and of the end (cave_dpad.py), in lines
# serial -> (lines to start lower, lines to end earlier), measured on the owner's console
GAMES = {
    "SCUS-94163": (8, 0),  # Final Fantasy VII (all three discs share the card of disc 1)
}
HOOK_VA = 0x115AE0  # stock: b 0x115820
RESUME = 0x1158AC  # fsubs f4,f13,f2 (f4 = f13 - 1), then the beq at 0x1158b0
TOC_FIRST, TOC_TWO, TOC_LAST = -0x55DC, -0x55D8, -0x55D4  # 16.0, 2.0, 248.0


def _table() -> list[tuple]:
    """Leave (top, end) = the game's defaults + the owner's adjust at -0x20(r1) and -0x28(r1); r11 and r12 as before."""
    ops: list[tuple] = [
        ("std", "r11", -0x10, "r1"), ("std", "r12", -0x18, "r1"), ("li", "r0", 0),
        ("std", "r0", -0x20, "r1"), ("std", "r0", -0x28, "r1"), ("lis", "r10", 0x1C),
    ]
    for k, (serial, (top, end)) in enumerate(GAMES.items()):
        name = (serial + ".VM1").encode()
        for w in range(3):
            word = int.from_bytes(name[4 * w:4 * w + 4], "big")
            ops += [("lwz", "r0", NAME_OFF + 4 * w, "r10"), ("lis", "r11", word >> 16), ("ori", "r11", "r11", word & 0xFFFF),
                    ("cmpw", "r0", "r11"), ("bne", f"g{k}")]
        ops += [("li", "r0", top), ("std", "r0", -0x20, "r1"), ("li", "r0", end), ("std", "r0", -0x28, "r1"),
                ("b", "gdone"), ("label", f"g{k}")]
    ops += [
        ("label", "gdone"),
        ("lwz", "r0", DELTA, "r10"), ("ld", "r11", -0x20, "r1"), ("add", "r11", "r11", "r0"), ("std", "r11", -0x20, "r1"),
        ("lwz", "r0", DELTA_END, "r10"), ("ld", "r11", -0x28, "r1"), ("add", "r11", "r11", "r0"), ("std", "r11", -0x28, "r1"),
        ("ld", "r11", -0x10, "r1"), ("ld", "r12", -0x18, "r1"),
    ]
    return ops


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
        *_table(),
        ("lwz", "r0", 0x20, "r22"), ("cmpwi", "r0", 0), ("bne", "has"), ("addi", "r0", "r9", 240),  # none: 240 lines
        ("label", "has"),
        ("std", "r9", 0xC0, "r1"), ("lfd", "f0", 0xC0, "r1"), ("fcfid", "f0", "f0"), ("frsp", "f0", "f0"),
        ("std", "r0", 0xC0, "r1"), ("lfd", "f10", 0xC0, "r1"), ("fcfid", "f10", "f10"), ("frsp", "f10", "f10"),
        ("lfd", "f11", -0x20, "r1"), ("fcfid", "f11", "f11"), ("frsp", "f11", "f11"),
        ("lfs", "f12", TOC_FIRST, "r2"), ("fadds", "f11", "f11", "f12"),  # first = 16 + the lines to start lower
        ("lfd", "f12", -0x28, "r1"), ("fcfid", "f12", "f12"), ("frsp", "f12", "f12"),
        ("lfs", "f13", TOC_LAST, "r2"), ("fsubs", "f12", "f13", "f12"),  # last = 248 - the lines to end earlier
        ("fsubs", "f13", "f0", "f11"), ("fsel", "f13", "f13", "f0", "f11"),  # f13 = max(Y1, 16)
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
