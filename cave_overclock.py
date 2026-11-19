"""Code cave: an overclock for the emulated R3000, applied live (not part of the installed package).

The interpreter loop (0x106748) costs every MIPS instruction `r7` CPU cycles (`subf r28,r7,r28` at 0x106798, r28 is
the cycles left until the next event, r7 is 1: the parameter 0x13 of the emulator, 0, plus 1), and the timers, the
video, the CD and the SPU all run on that same cycle count. Making an instruction cost less than a cycle is
therefore an overclock that keeps every hardware event in proportion, as DuckStation's does. A cycle is not
divisible, so the cost is kept in 1/256 of a cycle: the hook adds `r7 * STEP` to an accumulator in a free data word
and takes the whole cycles out of the budget (the fraction stays in the accumulator). STEP is 256 / factor (171 is
1.5x, 128 is 2x, 256 is the stock cost).

It is applied only through `ps3mapi_patch.py --overclock`: the cave goes into the text segment extension of the
running emulator and nothing is installed, so a game that does not like it is fixed by closing it.

Registers: r6, r8 and r10 are dead at the hook (the loop recomputes them, r9 and r0 right after it).
"""
from __future__ import annotations

from ppcasm import assemble

HOOK_VA = 0x106798
HOOK_STOCK = 0x7F87E050  # subf r28,r7,r28
RESUME = 0x10679C
ACC_OFF, STEP_OFF = -0x1820, -0x181C  # from 0x1c0000: the accumulator at 0x1be7e0 and its step at 0x1be7e4 (free, zeroed)
ACC_VA, STEP_VA = 0x1C0000 + ACC_OFF, 0x1C0000 + STEP_OFF


def step_for(factor: float) -> int:
    """The cost of one instruction in 1/256 cycle for an overclock of `factor` (1.0 is the stock cost)."""
    if not 1.0 <= factor <= 4.0:
        raise ValueError("the factor must be between 1.0 and 4.0")
    return round(256 / factor)


def program() -> list[tuple]:
    return [
        ("lis", "r6", 0x1C),
        ("lwz", "r8", ACC_OFF, "r6"), ("lwz", "r10", STEP_OFF, "r6"),
        ("mullw", "r10", "r10", "r7"), ("add", "r8", "r8", "r10"),
        ("rlwinm", "r10", "r8", 24, 8, 31),  # the whole cycles
        ("rlwinm", "r8", "r8", 0, 24, 31),  # the fraction stays
        ("stw", "r8", ACC_OFF, "r6"),
        ("subf", "r28", "r10", "r28"),
        ("b", RESUME),
    ]


def build(cave_va: int) -> tuple[list[tuple[int, int, int, str]], int]:
    """Return the (va, old, new, what) patches and the number of bytes used from `cave_va`."""
    words = assemble(cave_va, program())
    patches = [(cave_va + 4 * i, 0, w, "overclock cave code") for i, w in enumerate(words)]
    patches.append((HOOK_VA, HOOK_STOCK, assemble(HOOK_VA, [("b", cave_va)])[0], "jump to the overclock cave"))
    return patches, 4 * len(words)
