"""Code cave: a game that enters DualShock configuration gets analog mode and vibration switched on.

Command 0x43 with data byte 1 means the game talks DualShock (analog sticks and motors). The data-byte
stage (0xd6b5c) already holds r27 = 1 on that path. The hook at 0xd6b7c jumps here, which

  * sets the pad's analog flag (record + 0x9c) to 1, as the original menu toggle does;
  * clears the vibration disable flag (X + 0x84, X from TOC slot -0x7b60; 0 = vibration on, read in the
    actuator function at 0xeb2d8), so the motors run if the game asks for them;
  * redoes the three stock instructions it displaced and continues at 0xd6b88.

The game's own 0x44 (set mode) and 0x4d (motor mapping) still have the last word. A game that never
sends 0x43 (digital only, such as Castlevania) never gets here.
"""
from __future__ import annotations

from ppcasm import assemble

HOOK_VA, HOOK_STOCK = 0xD6B7C, 0x381F00A0  # addi r0,r31,0xa0
RESUME = 0xD6B88  # lwz r9,4(r11): needs r11 = pad record + 0xa0 (+ the pad array base)
PAD_STATE_TOC, VIBRATION_OFF = -0x7B60, 0x84


def program() -> list[tuple]:
    return [
        ("addi", "r0", "r31", 0x9C), ("stwx", "r27", "r24", "r0"),  # analog flag = 1
        ("lwz", "r9", PAD_STATE_TOC, "r2"), ("li", "r0", 0), ("stw", "r0", VIBRATION_OFF, "r9"),  # vibration on
        ("addi", "r0", "r31", 0xA0), ("add", "r11", "r0", "r24"),  # what the stock code computed
        ("b", RESUME),
    ]


def build(cave_va: int) -> tuple[list[tuple[int, int, int, str]], int]:
    """Return the (va, old, new, what) patches and the number of bytes used from `cave_va`."""
    words = assemble(cave_va, program())
    patches = [(cave_va + 4 * i, 0, w, "analog cave code") for i, w in enumerate(words)]
    patches.append((HOOK_VA, HOOK_STOCK, assemble(HOOK_VA, [("b", cave_va)])[0], "jump to the analog cave"))
    return patches, 4 * len(words)
