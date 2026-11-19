"""Code cave: the left analog stick acts as the d-pad while the game is in digital mode.

The pad thread (0xeb748) stores the PS1 button halfword (active low, first data byte in the high byte:
SELECT L3 R3 START UP RIGHT DOWN LEFT, then L2 R2 L1 R1 TRIANGLE CIRCLE CROSS SQUARE) with `sth r0,0(r3)` at
0xec16c. r29 points at the PS3 pad data copy, where the left stick is a halfword per axis (X at +0x10, Y at
+0x12, 0..255 with 128 at rest). The hook at 0xec16c jumps here, which

  * leaves everything alone when the pad is in analog mode (flag at G + pad * 0xd8 + 0x9c, G from TOC slot
    -0x7ccc: the same flag the protocol handler tests): the game then reads the sticks itself. The one exception
    is Parasite Eve (PARASITE_EVE, matched by the card name of the game): there the d-pad pushes the left stick
    all the way (0 or 255 on its axis, the other axis as it is) while the game is in analog mode, so the d-pad
    walks Aya like the stick does; the d-pad bits stay as they are for the menus;
  * otherwise clears the UP / RIGHT / DOWN / LEFT bit when the left stick is past 0x40 / 0xC0, and L1 / R1 when
    the right stick X (+0xc) is pushed left / right, so a game with no analog mode (a digital pad only) can be
    steered with the left stick and has both shoulder buttons on the right one;
  * stores the halfword (the displaced instruction) and continues at 0xec170.

r30 holds pad * 0x94 here; only pads 0 and 1 are told apart (a multitap pad 2 or 3 reads pad 1's flag).
"""
from __future__ import annotations

from ppcasm import assemble

HOOK_VA, HOOK_STOCK = 0xEC16C, 0xB0030000  # sth r0,0(r3)
RESUME = 0xEC170
PADS_TOC, ANALOG_FLAG, PAD_STRIDE = -0x7CCC, 0x9C, 0xD8
NAME_OFF = -0x1880  # the card name of the game (cave_percard NAME_VA 0x1BE780, "SLUS-00662.VM1") from 0x1c0000
# Parasite Eve (USA), discs 1 and 2; disc 2 shares the card of disc 1 when its image is named "(Disc 2)", but the
# serial of disc 2 is listed too for any other naming
PARASITE_EVE = ("SLUS-00662", "SLUS-00668")
LOW, HIGH = 0x40, 0xC0
# Overscan top adjust: SELECT + UP / DOWN moves the first video line shown by 2 (0 to MAX_DELTA), see cave_overscan.py
# Offsets from 0x1c0000: the top at 0x1BE760 (+64 and +68 remember the buttons), the end at 0x1BE76C (+70, +74).
DELTA, PREV_UP, PREV_DOWN, MAX_DELTA = -0x18A0, -0x189C, -0x1898, 24
DELTA_END, PREV_LEFT, PREV_RIGHT, MAX_END = -0x1894, -0x1890, -0x188C, 16
# Overclock level: SELECT + R1 / SELECT + L1 move it by 2 (0 to MAX_LEVEL), see cave_overclock.py
LEVEL, PREV_R1, PREV_L1, MAX_LEVEL = -0x1820, -0x1818, -0x1814, 6
L1_BTN, R1_BTN = 0x0004, 0x0008
SELECT, UP_BTN, DOWN_BTN, LEFT_BTN, RIGHT_BTN = 0x0100, 0x1000, 0x4000, 0x8000, 0x2000  # active low: bit set = not pressed
LEFT, RIGHT, UP, DOWN = 0x7FFF, 0xDFFF, 0xEFFF, 0xBFFF  # the button bit cleared = pressed (active low)
L1, R1 = 0xFFFB, 0xFFF7  # bits 2 and 3 of the second byte


def _adjust(tag: str, mask: int, prev: int, up: bool, slot: int, cap: int) -> list[tuple]:
    """One SELECT + button edge: a press (not a hold) moves the value in `slot` by 2, inside 0..cap."""
    keep = ("bge" if up else "ble", f"{tag}2")
    step = ("cmpwi", "r10", cap if up else 0), keep, ("addi", "r10", "r10", 2 if up else -2)
    return [
        ("li", "r11", 0), ("andi.", "r10", "r0", SELECT), ("cmpwi", "r10", 0), ("bne", f"{tag}1"),
        ("andi.", "r10", "r0", mask), ("cmpwi", "r10", 0), ("bne", f"{tag}1"), ("li", "r11", 1),
        ("label", f"{tag}1"),
        ("lwz", "r10", prev, "r9"), ("stw", "r11", prev, "r9"),
        ("cmpwi", "r10", 0), ("bne", f"{tag}2"), ("cmpwi", "r11", 0), ("beq", f"{tag}2"),
        ("lwz", "r10", slot, "r9"), *step, ("stw", "r10", slot, "r9"),
        ("label", f"{tag}2"),
    ]


def _analog_dpad() -> list[tuple]:
    """Analog mode: for Parasite Eve the d-pad bits in r0 set the left stick halfwords of the pad data copy (r29),
    which the stock code copies into the record right after the hook; any other game stores the buttons as they are."""
    ops: list[tuple] = [("label", "analog")]
    for k, serial in enumerate(PARASITE_EVE):
        name = (serial + ".VM1").encode()
        for w in range(3):
            word = int.from_bytes(name[4 * w:4 * w + 4], "big")
            ops += [("lis", "r9", 0x1C), ("lwz", "r9", NAME_OFF + 4 * w, "r9"), ("lis", "r10", word >> 16),
                    ("ori", "r10", "r10", word & 0xFFFF), ("cmpw", "r9", "r10"), ("bne", f"pe{k}")]
        ops += [("b", "pe")]
        ops += [("label", f"pe{k}")]
    ops += [("b", "done"), ("label", "pe")]
    for axis, low, high in ((0x10, LEFT_BTN, RIGHT_BTN), (0x12, UP_BTN, DOWN_BTN)):
        ops += [
            ("lhz", "r9", axis, "r29"),
            ("andi.", "r10", "r0", low), ("cmpwi", "r10", 0), ("bne", f"a{axis}l"), ("li", "r9", 0), ("label", f"a{axis}l"),
            ("andi.", "r10", "r0", high), ("cmpwi", "r10", 0), ("bne", f"a{axis}h"), ("li", "r9", 255), ("label", f"a{axis}h"),
            ("sth", "r9", axis, "r29"),
        ]
    return ops + [("b", "done")]


def program() -> list[tuple]:
    return [
        ("cmpwi", "r30", 0), ("bne", "noadj"),  # pad 1 only
        ("std", "r11", -0x10, "r1"), ("lis", "r9", 0x1C),
        *_adjust("u", UP_BTN, PREV_UP, True, DELTA, MAX_DELTA), *_adjust("d", DOWN_BTN, PREV_DOWN, False, DELTA, MAX_DELTA),
        *_adjust("r", RIGHT_BTN, PREV_RIGHT, True, DELTA_END, MAX_END),
        *_adjust("l", LEFT_BTN, PREV_LEFT, False, DELTA_END, MAX_END),
        *_adjust("o", R1_BTN, PREV_R1, True, LEVEL, MAX_LEVEL), *_adjust("p", L1_BTN, PREV_L1, False, LEVEL, MAX_LEVEL),
        ("ld", "r11", -0x10, "r1"),
        ("label", "noadj"),
        ("lwz", "r10", PADS_TOC, "r2"), ("cmpwi", "r30", 0), ("beq", "p0"), ("addi", "r10", "r10", PAD_STRIDE),
        ("label", "p0"),
        ("lwz", "r9", ANALOG_FLAG, "r10"), ("cmpwi", "r9", 0), ("bne", "analog"),
        ("lhz", "r9", 0x10, "r29"), ("cmplwi", "r9", LOW), ("bge", "nl"), ("andi.", "r0", "r0", LEFT),
        ("label", "nl"),
        ("cmplwi", "r9", HIGH), ("ble", "nr"), ("andi.", "r0", "r0", RIGHT),
        ("label", "nr"),
        ("lhz", "r9", 0x12, "r29"), ("cmplwi", "r9", LOW), ("bge", "nu"), ("andi.", "r0", "r0", UP),
        ("label", "nu"),
        ("cmplwi", "r9", HIGH), ("ble", "nd"), ("andi.", "r0", "r0", DOWN),
        ("label", "nd"),
        ("lhz", "r9", 0xC, "r29"), ("cmplwi", "r9", LOW), ("bge", "nb"), ("andi.", "r0", "r0", L1),
        ("label", "nb"),
        ("cmplwi", "r9", HIGH), ("ble", "done"), ("andi.", "r0", "r0", R1),
        ("label", "done"),
        ("sth", "r0", 0, "r3"), ("b", RESUME),
        *_analog_dpad(),
    ]


def build(cave_va: int) -> tuple[list[tuple[int, int, int, str]], int]:
    """Return the (va, old, new, what) patches and the number of bytes used from `cave_va`."""
    words = assemble(cave_va, program())
    patches = [(cave_va + 4 * i, 0, w, "d-pad cave code") for i, w in enumerate(words)]
    patches.append((HOOK_VA, HOOK_STOCK, assemble(HOOK_VA, [("b", cave_va)])[0], "jump to the d-pad cave"))
    return patches, 4 * len(words)
