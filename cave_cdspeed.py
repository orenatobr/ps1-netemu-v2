"""Code cave: data sectors of the disc arrive 4 times faster, so the games that run from the HD load sooner.

The emulator schedules the arrival of each data sector as an event: `0x105e68(delay, callback, 0)` with `delay` in CPU
cycles (33.8688 MHz). A real disc delivers 75 sectors a second (451584 cycles) or 150 with the double speed bit of
the CD mode set (225792 cycles), and the emulator keeps that pace even though the game comes from the HD. About a
dozen places of the CD code build one of these two numbers and call the scheduler, so the hook is the scheduler:
when the delay is exactly one of them and the CD mode (`+0x2734` of the CD controller, TOC slot `-0x7c60`) has
neither the XA-ADPCM bit (`0x40`) nor the CD-DA bit (`0x01`), the delay is divided by 4. A stream that must play at
the pace of the disc (an FMV with its voices, the music on a CD track) keeps it, as in DuckStation's CD-ROM read
speedup.

Registers: only r0, r9 and r10 and cr0 and cr7 are used, all volatile, free at the entry of the function.
"""
from __future__ import annotations

from ppcasm import assemble

HOOK_VA, HOOK_STOCK = 0x105E68, 0xF821FF51  # stdu r1,-0xb0(r1), the first instruction of the scheduler
RESUME = 0x105E6C
CD_STATE_SLOT = -0x7C60  # TOC slot holding the address of the CD controller state
MODE = 0x2734  # offset of the CD mode register (bit 7 double speed, bit 6 XA-ADPCM, bit 0 CD-DA)
AUDIO_BITS = 0x41
ONE_X, TWO_X = 451584, 225792  # CPU cycles per sector at 75 and at 150 sectors a second
SPEEDUP_SHIFT = 2  # 4x


def program() -> list[tuple]:
    return [
        ("lis", "r0", TWO_X >> 16), ("ori", "r0", "r0", TWO_X & 0xFFFF),
        ("cmpw", "r3", "r0"), ("beq", "cd"),
        ("lis", "r0", ONE_X >> 16), ("ori", "r0", "r0", ONE_X & 0xFFFF),
        ("cmpw", "r3", "r0"), ("bne", "stock"),
        ("label", "cd"),
        ("lwz", "r9", CD_STATE_SLOT, "r2"), ("lwz", "r10", MODE, "r9"),
        ("andi.", "r10", "r10", AUDIO_BITS), ("bne0", "stock"),  # an audio stream keeps the pace of the disc
        ("rlwinm", "r3", "r3", 32 - SPEEDUP_SHIFT, SPEEDUP_SHIFT, 31),  # srwi r3,r3,2
        ("label", "stock"),
        ("stdu", "r1", -0xB0, "r1"),  # the instruction the hook replaced
        ("b", RESUME),
    ]


def build(cave_va: int) -> tuple[list[tuple[int, int, int, str]], int]:
    """Return the (va, old, new, what) patches and the number of bytes used from `cave_va`."""
    words = assemble(cave_va, program())
    patches = [(cave_va + 4 * i, 0, w, "CD speedup cave code") for i, w in enumerate(words)]
    patches.append((HOOK_VA, HOOK_STOCK, assemble(HOOK_VA, [("b", cave_va)])[0], "jump to the CD speedup cave"))
    return patches, 4 * len(words)
