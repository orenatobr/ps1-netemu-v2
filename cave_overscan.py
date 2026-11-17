"""Code cave: crop the picture to the area the game really draws (content-aware vertical overscan).

The display handler (0x113f0c) ends by storing the picture quad (x1, x2, y1, y2 in f6, f5, f7, f4) at
0x114148. The hook there jumps here first. The GPU packet buffer (the object behind TOC slot -0x77f8,
commands from +0x60) holds the draw-area commands GP0 E3 (top left) and E4 (bottom right). The code scans
it for E3/E4 pairs with a plausible size (16..256 lines, 128..640 wide: a full-VRAM reset is not one) whose
top lies in the first buffer (above the declared height) and JOINS them: the window is from the lowest top to
the highest bottom. A game that draws its scene and its menu as two areas (Final Fantasy VII battles: 320x158
plus 320x74) keeps both; taking only the tallest cut the menu and zoomed the scene to 1.45x. That window is
intersected with the window the base formula already applied, and y1/y2 are rescaled so the result fills the
screen. Only a quarter of the extra zoom goes to x (the rest is vertical), so the picture stays inside the
4:3 frame. A window shorter than three quarters of the display is not trusted (that zoom would push the
width out of the frame) and is ignored.

The window is remembered between calls (five words at STATE_VA): it grows at once and shrinks only after
HOLD consecutive display commands asked for the same smaller one; with no pair the window in use stays.

Nothing changes when the declared height is not 200..256 lines, no pair is found, the top is not in the
upper half, the bottom is not in the lower half, or the quad is degenerate. Everything it touches is
saved and restored.
"""
from __future__ import annotations

import struct

from ppcasm import assemble

HOOK_VA, HOOK_STOCK = 0x114148, 0xD0D401AC  # stfs f6,0x1ac(r20)
RESUME = 0x11414C
OBJ_TOC = -0x77F8
FRAME = 0x140
SAVE_R = [0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
SAVE_F = [0, 1, 2, 3, 8, 9, 10, 13]
CONSTS = (-1.0, 1.0, 2.0, 0.5, 0.25, 0.75)  # data offsets 0, 4, 8, 12, 16, 20
STATE_VA = 0x1BE75C  # five free words in the writable data segment: window a, b, candidate a, b, count
HOLD = 24  # display commands in a row before the window shrinks


def program(data_va: int) -> list[tuple]:
    hi, lo = (data_va >> 16) & 0xFFFF, data_va & 0xFFFF
    p: list[tuple] = [("stdu", "r1", -FRAME, "r1")]
    p += [("std", f"r{r}", 0x80 + 8 * k, "r1") for k, r in enumerate(SAVE_R)]
    p += [("stfd", f"f{f}", 0xE0 + 8 * j, "r1") for j, f in enumerate(SAVE_F)]
    p += [  # H = declared lines (Y2 - Y1, or 240 when Y2 is 0)
        ("lwz", "r9", 0x1C, "r22"), ("lwz", "r10", 0x20, "r22"), ("li", "r11", 240),
        ("cmpwi", "r10", 0), ("beq", "hok"), ("subf", "r11", "r9", "r10"),
        ("label", "hok"),
        ("cmpwi", "r11", 200), ("blt", "done"), ("cmpwi", "r11", 256), ("bgt", "done"),
        # scan the packet buffer for E3/E4 pairs
        ("lwz", "r3", OBJ_TOC, "r2"), ("addi", "r3", "r3", 0x60), ("addi", "r4", "r3", 0x17A0),
        ("li", "r7", -1), ("li", "r9", 0), ("li", "r10", 0x7FFF),  # r9 = highest bottom, r10 = lowest top
        ("label", "loop"),
        ("lwz", "r5", 0, "r3"), ("rlwinm", "r6", "r5", 8, 24, 31),
        ("cmpwi", "r6", 0xE3), ("bne", "note3"),
        ("rlwinm", "r7", "r5", 22, 22, 31), ("rlwinm", "r8", "r5", 0, 22, 31), ("b", "adv"),
        ("label", "note3"),
        ("cmpwi", "r6", 0xE4), ("bne", "adv"),
        ("cmpwi", "r7", 0), ("blt", "adv"),
        ("rlwinm", "r12", "r5", 22, 22, 31), ("rlwinm", "r0", "r5", 0, 22, 31),
        ("subf", "r12", "r7", "r12"), ("addi", "r12", "r12", 1),
        ("subf", "r0", "r8", "r0"),  # x2 - x1 (the width minus one)
        ("cmpwi", "r12", 16), ("blt", "next"), ("cmpwi", "r12", 256), ("bgt", "next"),
        ("cmpwi", "r0", 127), ("blt", "next"), ("cmpwi", "r0", 639), ("bgt", "next"),
        ("cmpw", "r7", "r11"), ("bge", "next"),  # the second buffer sits below the display
        ("cmpw", "r7", "r10"), ("bge", "topok"), ("mr", "r10", "r7"),
        ("label", "topok"),
        ("add", "r12", "r7", "r12"), ("cmpw", "r12", "r9"), ("ble", "next"), ("mr", "r9", "r12"),
        ("label", "next"),
        ("li", "r7", -1),
        ("label", "adv"),
        ("addi", "r3", "r3", 4), ("cmpw", "r3", "r4"), ("blt", "loop"),
        # r10 = top row, r9 = bottom row (0 when no pair was found), r11 = H
        ("lis", "r12", (STATE_VA >> 16) & 0xFFFF), ("ori", "r12", "r12", STATE_VA & 0xFFFF),
        ("lwz", "r5", 0, "r12"), ("lwz", "r6", 4, "r12"),  # the window in use: [a, b)
        ("cmpwi", "r9", 0), ("beq", "noc"),
        ("rlwinm", "r0", "r11", 31, 1, 31),  # H / 2
        ("cmpw", "r10", "r0"), ("bge", "noc"),
        ("cmpw", "r9", "r11"), ("ble", "b1"), ("mr", "r9", "r11"),
        ("label", "b1"),
        ("cmpw", "r9", "r0"), ("ble", "noc"),
        # a window under three quarters of the display would zoom too much: not trusted
        ("subf", "r7", "r10", "r9"), ("rlwinm", "r8", "r11", 30, 2, 31), ("subf", "r8", "r8", "r11"),
        ("cmpw", "r7", "r8"), ("blt", "noc"),
        # a usable candidate [r10, r9): it grows the window at once and shrinks it only after HOLD
        # display commands in a row asked for the same smaller one, so a scene that alternates
        # areas (or has none for a while) cannot make the picture jump
        ("cmpwi", "r6", 0), ("bne", "have"), ("li", "r5", 0), ("mr", "r6", "r11"),
        ("label", "have"),
        ("subf", "r7", "r10", "r9"), ("subf", "r8", "r5", "r6"), ("cmpw", "r7", "r8"), ("blt", "smaller"),
        ("mr", "r5", "r10"), ("mr", "r6", "r9"), ("li", "r7", 0), ("b", "keepc"),
        ("label", "smaller"),
        ("lwz", "r7", 8, "r12"), ("lwz", "r8", 12, "r12"),
        ("cmpw", "r7", "r10"), ("bne", "newc"), ("cmpw", "r8", "r9"), ("bne", "newc"),
        ("lwz", "r7", 16, "r12"), ("addi", "r7", "r7", 1), ("cmpwi", "r7", HOLD), ("blt", "keepc"),
        ("mr", "r5", "r10"), ("mr", "r6", "r9"), ("li", "r7", 0), ("b", "keepc"),
        ("label", "newc"),
        ("stw", "r10", 8, "r12"), ("stw", "r9", 12, "r12"), ("li", "r7", 1),
        ("label", "keepc"),
        ("stw", "r7", 16, "r12"), ("stw", "r5", 0, "r12"), ("stw", "r6", 4, "r12"), ("b", "usewin"),
        ("label", "noc"),
        ("cmpwi", "r6", 0), ("beq", "done"),  # nothing applied yet: leave the picture alone
        ("label", "usewin"),
        # float part
        ("std", "r5", 0x70, "r1"), ("lfd", "f0", 0x70, "r1"), ("fcfid", "f0", "f0"), ("frsp", "f0", "f0"),
        ("std", "r6", 0x70, "r1"), ("lfd", "f1", 0x70, "r1"), ("fcfid", "f1", "f1"), ("frsp", "f1", "f1"),
        ("std", "r11", 0x70, "r1"), ("lfd", "f2", 0x70, "r1"), ("fcfid", "f2", "f2"), ("frsp", "f2", "f2"),
        ("fdivs", "f0", "f0", "f2"), ("fdivs", "f1", "f1", "f2"),  # a/H, b/H
        ("lis", "r12", hi), ("ori", "r12", "r12", lo),
        ("fsubs", "f3", "f4", "f7"),  # y2 - y1
        ("lfs", "f2", 12, "r12"), ("fcmpu", "f3", "f2"), ("blt", "done"),
        ("lfs", "f2", 0, "r12"), ("fsubs", "f8", "f2", "f7"), ("fdivs", "f8", "f8", "f3"),  # t at y = -1
        ("lfs", "f2", 4, "r12"), ("fsubs", "f9", "f2", "f7"), ("fdivs", "f9", "f9", "f3"),  # t at y = +1
        ("fsubs", "f10", "f0", "f8"), ("fsel", "f0", "f10", "f0", "f8"),  # t0 = max(a/H, t_-1)
        ("fsubs", "f10", "f9", "f1"), ("fsel", "f1", "f10", "f1", "f9"),  # t1 = min(b/H, t_+1)
        ("fsubs", "f10", "f1", "f0"),  # t1 - t0
        ("lfs", "f2", 12, "r12"), ("fcmpu", "f10", "f2"), ("blt", "done"),
        ("fsubs", "f3", "f9", "f8"), ("fdivs", "f3", "f3", "f10"),  # s = stock window / new window
        ("lfs", "f2", 16, "r12"), ("lfs", "f13", 20, "r12"), ("fmadds", "f3", "f3", "f2", "f13"),  # zoom of x = 1 + (s - 1) / 4
        ("lfs", "f2", 4, "r12"), ("fsubs", "f2", "f2", "f0"), ("fdivs", "f2", "f2", "f10"),
        ("lfs", "f13", 8, "r12"), ("fmuls", "f2", "f2", "f13"), ("lfs", "f13", 0, "r12"), ("fadds", "f4", "f13", "f2"),
        ("fdivs", "f0", "f0", "f10"), ("lfs", "f13", 8, "r12"), ("fmuls", "f0", "f0", "f13"),
        ("lfs", "f13", 0, "r12"), ("fsubs", "f7", "f13", "f0"),
        ("fmuls", "f6", "f6", "f3"), ("fmuls", "f5", "f5", "f3"),
        ("label", "done"),
    ]
    p += [("lfd", f"f{f}", 0xE0 + 8 * j, "r1") for j, f in enumerate(SAVE_F)]
    p += [("ld", f"r{r}", 0x80 + 8 * k, "r1") for k, r in enumerate(SAVE_R)]
    p += [("addi", "r1", "r1", FRAME), ("stfs", "f6", 0x1AC, "r20"), ("b", RESUME)]
    return p


def build(cave_va: int) -> tuple[list[tuple[int, int, int, str]], int]:
    """Return the (va, old, new, what) patches and the number of bytes used from `cave_va`."""
    size = 4 * len(assemble(cave_va, program(0)))
    data_va = cave_va + size
    words = assemble(cave_va, program(data_va))
    data = [struct.unpack(">I", struct.pack(">f", c))[0] for c in CONSTS]
    patches = [(cave_va + 4 * i, 0, w, "overscan cave code") for i, w in enumerate(words)]
    patches += [(data_va + 4 * i, 0, w, "overscan cave data") for i, w in enumerate(data)]
    patches.append((HOOK_VA, HOOK_STOCK, assemble(HOOK_VA, [("b", cave_va)])[0], "jump to the overscan cave"))
    return patches, size + 4 * len(data)
