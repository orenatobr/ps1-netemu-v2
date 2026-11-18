"""Code cave: the right analog stick acts as the d-pad while the game is in digital mode.

The pad thread (0xeb748) stores the PS1 button halfword (active low, first data byte in the high byte:
SELECT L3 R3 START UP RIGHT DOWN LEFT, then L2 R2 L1 R1 TRIANGLE CIRCLE CROSS SQUARE) with `sth r0,0(r3)` at
0xec16c. r29 points at the PS3 pad data copy, where the right stick is a halfword per axis (X at +0xc, Y at
+0xe, 0..255 with 128 at rest). The hook at 0xec16c jumps here, which

  * leaves everything alone when the pad is in analog mode (flag at G + pad * 0xd8 + 0x9c, G from TOC slot
    -0x7ccc: the same flag the protocol handler tests): the game then reads the sticks itself;
  * otherwise clears the UP / RIGHT / DOWN / LEFT bit when the right stick is past 0x40 / 0xC0, so a game with
    no analog mode (a digital pad only) can be steered with either stick;
  * stores the halfword (the displaced instruction) and continues at 0xec170.

r30 holds pad * 0x94 here; only pads 0 and 1 are told apart (a multitap pad 2 or 3 reads pad 1's flag).
"""
from __future__ import annotations

from ppcasm import assemble

HOOK_VA, HOOK_STOCK = 0xEC16C, 0xB0030000  # sth r0,0(r3)
RESUME = 0xEC170
PADS_TOC, ANALOG_FLAG, PAD_STRIDE = -0x7CCC, 0x9C, 0xD8
LOW, HIGH = 0x40, 0xC0
LEFT, RIGHT, UP, DOWN = 0x7FFF, 0xDFFF, 0xEFFF, 0xBFFF  # the button bit cleared = pressed (active low)


def program() -> list[tuple]:
    return [
        ("lwz", "r10", PADS_TOC, "r2"), ("cmpwi", "r30", 0), ("beq", "p0"), ("addi", "r10", "r10", PAD_STRIDE),
        ("label", "p0"),
        ("lwz", "r9", ANALOG_FLAG, "r10"), ("cmpwi", "r9", 0), ("bne", "done"),
        ("lhz", "r9", 0xC, "r29"), ("cmplwi", "r9", LOW), ("bge", "nl"), ("andi.", "r0", "r0", LEFT),
        ("label", "nl"),
        ("cmplwi", "r9", HIGH), ("ble", "nr"), ("andi.", "r0", "r0", RIGHT),
        ("label", "nr"),
        ("lhz", "r9", 0xE, "r29"), ("cmplwi", "r9", LOW), ("bge", "nu"), ("andi.", "r0", "r0", UP),
        ("label", "nu"),
        ("cmplwi", "r9", HIGH), ("ble", "done"), ("andi.", "r0", "r0", DOWN),
        ("label", "done"),
        ("sth", "r0", 0, "r3"), ("b", RESUME),
    ]


def build(cave_va: int) -> tuple[list[tuple[int, int, int, str]], int]:
    """Return the (va, old, new, what) patches and the number of bytes used from `cave_va`."""
    words = assemble(cave_va, program())
    patches = [(cave_va + 4 * i, 0, w, "d-pad cave code") for i, w in enumerate(words)]
    patches.append((HOOK_VA, HOOK_STOCK, assemble(HOOK_VA, [("b", cave_va)])[0], "jump to the d-pad cave"))
    return patches, 4 * len(words)
