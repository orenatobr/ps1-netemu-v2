"""Code cave: an overclock of the emulated R3000, off by default, switched by SELECT + L1 / SELECT + R1.

The interpreter loop (0x106748) costs every MIPS instruction `r7` CPU cycles (`subf r28,r7,r28` at 0x106798, r28 is
the cycles left until the next event, r7 is 1: the emulator parameter 0x13, 0, plus 1), and the timers, the video, the
CD and the SPU all run on that same cycle count. Making an instruction cost less than a cycle is therefore an
overclock that keeps every hardware event in proportion, as DuckStation's does. A cycle is not divisible, so the
cost is kept in 1/256 of a cycle: with LEVEL > 0 the hook adds `r7 * (256 - 21 * LEVEL)` to an accumulator and takes
only the whole cycles out of the budget (the fraction stays in the accumulator). LEVEL is 0 (stock, the cost is exactly
`r7`), 2 (1.2x), 4 (1.5x) or 6 (2x); SELECT + R1 raises it and SELECT + L1 lowers it (cave_dpad.py). It is 0 every time
the emulator starts.

Registers: r6, r8 and r10 and cr7 are dead at the hook (the loop recomputes them, r9 and r0 right after it).
"""
from __future__ import annotations

from ppcasm import assemble

HOOK_VA = 0x106798
HOOK_STOCK = 0x7F87E050  # subf r28,r7,r28
RESUME = 0x10679C
LEVEL_OFF, ACC_OFF = -0x1820, -0x181C  # from 0x1c0000: the level at 0x1be7e0 and the accumulator at 0x1be7e4 (free, zeroed)
LEVEL_VA, ACC_VA = 0x1C0000 + LEVEL_OFF, 0x1C0000 + ACC_OFF
MAX_LEVEL, LEVEL_STEP = 6, 21  # the cost is 256 - 21 * level


def program() -> list[tuple]:
    return [
        ("lis", "r6", 0x1C),
        ("lwz", "r10", LEVEL_OFF, "r6"), ("cmpwi", "r10", 0), ("beq", "stock"),
        ("lwz", "r8", ACC_OFF, "r6"),
        ("mulli", "r10", "r10", -LEVEL_STEP), ("addi", "r10", "r10", 256),
        ("mullw", "r10", "r10", "r7"), ("add", "r8", "r8", "r10"),
        ("rlwinm", "r10", "r8", 24, 8, 31),  # the whole cycles
        ("rlwinm", "r8", "r8", 0, 24, 31),  # the fraction stays
        ("stw", "r8", ACC_OFF, "r6"),
        ("subf", "r28", "r10", "r28"),
        ("b", RESUME),
        ("label", "stock"),
        ("subf", "r28", "r7", "r28"),  # the instruction the hook replaced
        ("b", RESUME),
    ]


def build(cave_va: int) -> tuple[list[tuple[int, int, int, str]], int]:
    """Return the (va, old, new, what) patches and the number of bytes used from `cave_va`."""
    words = assemble(cave_va, program())
    patches = [(cave_va + 4 * i, 0, w, "overclock cave code") for i, w in enumerate(words)]
    patches.append((HOOK_VA, HOOK_STOCK, assemble(HOOK_VA, [("b", cave_va)])[0], "jump to the overclock cave"))
    return patches, 4 * len(words)
