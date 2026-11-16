"""Code cave: move a PS1 memory card from slot 2 to slot 1 when slot 1 has no PS1 card.

`xMcInsert` (inside the emulator's `main`) receives the cards as argv[1] (slot 1, in r30) and
argv[2] (slot 2, in r27, NULL when absent). The hook at 0xb3b90 jumps here. A card name counts as a
PS1 card when `/dev_hdd0/savedata/vmc/<name>.VM1` can be opened (a name that already ends in ".VM1"
is used as is). If slot 2 holds a PS1 card and slot 1 is empty or holds something else (a PS2 `.VM2`
card, a missing file), slot 2's card becomes slot 1's through the emulator's own path at 0xb4124,
which inserts r30 into slot 1 and clears slot 2. Every other case falls back to the stock code.

Before that, `percard` (cave_percard.py) gives every game its own card in slot 1, named after the game's
serial, created when missing. The functions called are the emulator's own: strlen 0xa318c, sprintf 0xa5420, open 0x15532c,
close 0x14f80c; the stock format "/dev_hdd0/savedata/vmc/%s.VM1" sits in TOC slot -0x7b04.
"""
from __future__ import annotations

from cave_percard import NAME_VA
from cave_percard import program as _percard
from ppcasm import assemble

HOOK_VA, HOOK_STOCK = 0xB3B90, 0x2F9B0000  # cmpwi cr7,r27,0
STOCK_RESUME, MOVE_TARGET = 0xB3B94, 0xB4124
STRLEN, SPRINTF, FSOPEN, FSCLOSE = 0xA318C, 0xA5420, 0x15532C, 0x14F80C
TOC_FMT = -0x7B04
RAW_FMT = b"/dev_hdd0/savedata/vmc/%s\0"
EXISTS_FRAME, MAIN_FRAME = 0x520, 0x40


def program(fmt_va: int) -> list[tuple]:
    hi, lo = (fmt_va >> 16) & 0xFFFF, fmt_va & 0xFFFF
    return [
        ("stdu", "r1", -MAIN_FRAME, "r1"),
        # one card per game: when percard finds the serial, its card becomes slot 1
        ("bl", "percard"), ("cmpwi", "r3", 0), ("beq", "noper"),
        ("lis", "r30", (NAME_VA >> 16) & 0xFFFF), ("ori", "r30", "r30", NAME_VA & 0xFFFF), ("mr", "r28", "r30"),
        ("label", "noper"),
        ("cmpwi", "r27", 0), ("beq", "stock"),
        ("clrldi", "r3", "r27", 32), ("lbz", "r0", 0, "r3"),
        ("cmpwi", "r0", 0), ("beq", "stock"),
        ("bl", "exists"), ("cmpwi", "r3", 0), ("beq", "stock"),  # slot 2 must be a PS1 card on disk
        ("cmpwi", "r30", 0), ("beq", "move"),  # slot 1 empty
        ("clrldi", "r3", "r30", 32), ("bl", "exists"),
        ("cmpwi", "r3", 0), ("bne", "stock"),  # slot 1 already holds a PS1 card
        ("label", "move"),
        ("mr", "r28", "r27"), ("clrldi", "r30", "r27", 32),
        ("addi", "r1", "r1", MAIN_FRAME), ("b", MOVE_TARGET),
        ("label", "stock"),
        ("addi", "r1", "r1", MAIN_FRAME), ("cmpwi", "r27", 0), ("b", STOCK_RESUME),
        # exists(r3 = name) -> r3 = 1 when the .VM1 file can be opened, else 0
        ("label", "exists"),
        ("mflr", "r0"), ("std", "r0", 0x10, "r1"), ("stdu", "r1", -EXISTS_FRAME, "r1"),
        ("std", "r3", 0x70, "r1"), ("bl", STRLEN), ("ld", "r4", 0x70, "r1"),
        ("cmplwi", "r3", 4), ("blt", "ext"),
        ("add", "r5", "r4", "r3"), ("lwz", "r6", -4, "r5"),
        ("lis", "r7", 0x2E56), ("ori", "r7", "r7", 0x4D31),  # ".VM1"
        ("cmpw", "r6", "r7"), ("bne", "ext"),
        ("lis", "r4", hi), ("ori", "r4", "r4", lo), ("b", "build"),
        ("label", "ext"), ("lwz", "r4", TOC_FMT, "r2"),
        ("label", "build"),
        ("addi", "r3", "r1", 0x80), ("ld", "r5", 0x70, "r1"), ("bl", SPRINTF),
        ("addi", "r3", "r1", 0x80), ("li", "r4", 0), ("addi", "r5", "r1", 0x78),
        ("li", "r6", 0), ("li", "r7", 0), ("bl", FSOPEN),
        ("cmpwi", "r3", 0), ("bne", "no"),
        ("lwz", "r3", 0x78, "r1"), ("bl", FSCLOSE),
        ("li", "r3", 1), ("b", "done"),
        ("label", "no"), ("li", "r3", 0),
        ("label", "done"),
        ("addi", "r1", "r1", EXISTS_FRAME), ("ld", "r0", 0x10, "r1"), ("mtlr", "r0"), ("blr",),
        *_percard(),
    ]


def build(cave_va: int) -> tuple[list[tuple[int, int, int, str]], int]:
    """Return the (va, old, new, what) patches and the number of bytes used from `cave_va`."""
    size = 4 * len(assemble(cave_va, program(0)))
    fmt_va = cave_va + size
    words = assemble(cave_va, program(fmt_va))
    data = RAW_FMT + b"\0" * (-len(RAW_FMT) % 4)
    data_words = [int.from_bytes(data[i:i + 4], "big") for i in range(0, len(data), 4)]
    patches = [(cave_va + 4 * i, 0, w, "memory card cave code") for i, w in enumerate(words)]
    patches += [(fmt_va + 4 * i, 0, w, "memory card cave data") for i, w in enumerate(data_words)]
    hook = assemble(HOOK_VA, [("b", cave_va)])[0]
    patches.append((HOOK_VA, HOOK_STOCK, hook, "jump to the memory card cave"))
    return patches, size + len(data)
