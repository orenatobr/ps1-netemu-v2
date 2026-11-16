"""Code cave: crop the picture to the area the game really draws (content-aware vertical overscan).

The display handler (0x113f0c) ends by storing the picture quad (x1, x2, y1, y2 in f6, f5, f7, f4) at
0x114148. The hook there jumps here first. The GPU packet buffer (the object behind TOC slot -0x77f8,
commands from +0x60) holds the draw-area commands GP0 E3 (top left) and E4 (bottom right). The code scans
it for E3/E4 pairs with a plausible size (100..256 lines, 128..640 wide: a full-VRAM reset is not one),
keeps the tallest (the lowest top on a tie) and treats its rows as the picture. That window is intersected
with the overscan window the stock formula already applied, and y1/y2 are rescaled so the result fills the
screen. Half of the extra zoom goes to x, so the distortion is shared between both axes.

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
CONSTS = (-1.0, 1.0, 2.0, 0.5)  # data offsets 0, 4, 8, 12


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
        ("li", "r7", -1), ("li", "r9", 0), ("li", "r10", 0),
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
        ("cmpwi", "r12", 100), ("blt", "next"), ("cmpwi", "r12", 256), ("bgt", "next"),
        ("cmpwi", "r0", 127), ("blt", "next"), ("cmpwi", "r0", 639), ("bgt", "next"),
        ("cmpw", "r12", "r9"), ("blt", "next"), ("bgt", "take"), ("cmpw", "r7", "r10"), ("bge", "next"),
        ("label", "take"),
        ("mr", "r9", "r12"), ("mr", "r10", "r7"),
        ("label", "next"),
        ("li", "r7", -1),
        ("label", "adv"),
        ("addi", "r3", "r3", 4), ("cmpw", "r3", "r4"), ("blt", "loop"),
        # r10 = top row, r9 = height, r11 = H; the window is [a, b)
        ("cmpwi", "r9", 0), ("beq", "done"),
        ("rlwinm", "r0", "r11", 31, 1, 31),  # H / 2
        ("cmpw", "r10", "r0"), ("bge", "done"),
        ("add", "r9", "r10", "r9"), ("cmpw", "r9", "r11"), ("ble", "b1"), ("mr", "r9", "r11"),
        ("label", "b1"),
        ("cmpw", "r9", "r0"), ("ble", "done"),
        # float part
        ("std", "r10", 0x70, "r1"), ("lfd", "f0", 0x70, "r1"), ("fcfid", "f0", "f0"), ("frsp", "f0", "f0"),
        ("std", "r9", 0x70, "r1"), ("lfd", "f1", 0x70, "r1"), ("fcfid", "f1", "f1"), ("frsp", "f1", "f1"),
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
        ("fmadds", "f3", "f3", "f2", "f2"),  # zoom of x = (s + 1) / 2
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
