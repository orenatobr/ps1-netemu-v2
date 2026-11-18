"""Code cave: the emulator always starts in "Screen mode: Normal" with "Smoothing: Disabled".

The two options of the emulator's menu live in setting 49 (the settings reader at 0xb65f0, `li r3,0x31`): bit 0 chooses the
second geometry path of the display handler (0x1143c4 instead of 0x115a3c, the one the overscan formula lives in) and
bit 1 enables the smoothing stages (0x113cfc, 0x113e28, 0x113998). Whatever the saved configuration of the console says,
the reader now returns setting 49 with both bits clear, so the display code always sees Normal and no smoothing, and the
menu shows them as such.

The hook replaces `clrldi r3,r3,0x20` at the end of the reader (0xb6628), where r9 is the address of the setting.
"""
from __future__ import annotations

from ppcasm import assemble

HOOK_VA, HOOK_STOCK = 0xB6628, 0x78630020  # clrldi r3,r3,0x20
SETTINGS_TOC = -0x7E94
SETTING = 49
ADDRESS_OFFSET = 4 * SETTING + 0x10  # the reader adds 4*index + 0x10 to the base, then loads 4(r9)


def program() -> list[tuple]:
    return [
        ("clrldi", "r3", "r3", 32),
        ("lwz", "r11", SETTINGS_TOC, "r2"), ("addi", "r11", "r11", ADDRESS_OFFSET), ("cmpw", "r9", "r11"), ("bne", "out"),
        ("rlwinm", "r3", "r3", 0, 0, 29),  # clear bits 0 and 1
        ("label", "out"),
        ("blr",),
    ]


def build(cave_va: int) -> tuple[list[tuple[int, int, int, str]], int]:
    """Return the (va, old, new, what) patches and the number of bytes used from `cave_va`."""
    words = assemble(cave_va, program())
    patches = [(cave_va + 4 * i, 0, w, "display settings cave code") for i, w in enumerate(words)]
    patches.append((HOOK_VA, HOOK_STOCK, assemble(HOOK_VA, [("b", cave_va)])[0], "jump to the display settings cave"))
    return patches, 4 * len(words)
