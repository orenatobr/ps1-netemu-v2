"""Code cave: the picture of "Screen Mode: Full Screen" fills the 16:9 window like the 4:3 one fills its window.

The display handler keeps the horizontal ends of the picture quad in the render state (`+0x1ac` left, `+0x1b0` right
of r20, nominal +-640/720). Two paths draw them:

  * Screen Mode "Normal" (the emulator's up-convert mode `0x1620474` is 0): the quad is multiplied by
    f31 = (height / width) * 1.4668 = 0.825, the 4:3 window of a 16:9 frame (0.75) with the 10% of overscan the
    emulator adds around it, so the picture fills 97.8% of its window;
  * Screen Mode "Full Screen" (any other value, branch at 0x114de8): the quad is drawn with the nominal ends
    unchanged, which fills only 88.9% of the 16:9 window (5.5% of the frame width stays black on each side).

Normal fills 0.75 * 1.1001 = 82.5% of the frame width, i.e. 97.8% of its own window. This cave runs at the start of
the second path and multiplies both ends by 1.1001 (0.75 * 1.4668, the overscan the Normal path applies), so Full Screen
fills 97.8% of its window too, with the same margin (1.1% of the frame width on each side). Everything is a fraction of
the frame, so it holds at 720p and at 1080p. Like the Normal path it only acts when the output is wider than 720
(HD); the SD outputs keep the stock picture. The vertical ends are not touched and Normal never reaches this code.

Registers: r0, r27 (the stock code sets it to r20 + 244 right after), f0, f13 and cr7 are free at the hook (the code that follows loads them again before reading them; the
call at 0x115b88 clobbers the volatile condition fields anyway).
"""
from __future__ import annotations

import struct

from ppcasm import assemble

HOOK_VA = 0x115B60  # stock: lwz r28,336(r20), the first instruction of the Full Screen path
RESUME = 0x115B64
WIDTH_SLOT = -0x7844  # TOC slot holding the address of the output width the emulator itself tests against 720
LEFT, RIGHT = 0x1AC, 0x1B0  # offsets from r20 of the left and right end of the quad
# 0.75 (a 4:3 window inside a 16:9 frame) * 1.4668133 (the stock constant at TOC -0x7788) = 1.10011, the overscan
FACTOR = 0.75 * struct.unpack(">f", struct.pack(">I", 0x3FBBC08A))[0]
FACTOR_BITS = struct.unpack(">I", struct.pack(">f", FACTOR))[0]
STOCK_FIRST = 0x83940150  # lwz r28,336(r20)


def program() -> list[tuple]:
    return [
        ("lwz", "r28", 0x150, "r20"),  # the instruction the hook replaced
        ("lwz", "r27", WIDTH_SLOT, "r2"), ("lwz", "r27", 0, "r27"), ("cmpwi", "r27", 720), ("ble", "sd"),  # HD only
        ("lis", "r0", FACTOR_BITS >> 16), ("ori", "r0", "r0", FACTOR_BITS & 0xFFFF),
        ("stw", "r0", -8, "r1"), ("lfs", "f13", -8, "r1"),
        ("lfs", "f0", LEFT, "r20"), ("fmuls", "f0", "f0", "f13"), ("stfs", "f0", LEFT, "r20"),
        ("lfs", "f0", RIGHT, "r20"), ("fmuls", "f0", "f0", "f13"), ("stfs", "f0", RIGHT, "r20"),
        ("label", "sd"),
        ("b", RESUME),
    ]


def build(cave_va: int) -> tuple[list[tuple[int, int, int, str]], int]:
    """Return the (va, old, new, what) patches and the number of bytes used from `cave_va`."""
    words = assemble(cave_va, program())
    patches = [(cave_va + 4 * i, 0, w, "full screen cave code") for i, w in enumerate(words)]
    patches.append((HOOK_VA, STOCK_FIRST, assemble(HOOK_VA, [("b", cave_va)])[0], "jump to the full screen cave"))
    return patches, 4 * len(words)
