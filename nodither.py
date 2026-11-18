"""Patches that remove the dithering of the emulated PS1 picture (a modern TV does not need it).

The PS1 GPU is emulated in software on the SPUs (`gpusoft.cc`, an SPU ELF embedded in the emulator at file offset
0x171100). Its draw routines pick one of two tables of 16 vectors by a flag: `XBGR_DITHER` (0x15170 in the SPU local
store, the 4x4 matrix of channel offsets) or `XBGR_NO_DITHER` (0x15270, zeros). Both are filled once at start by
`_Z41__static_initialization_and_destruction_0ii` (SPU 0x1018). The 14 stores that put a non-zero offset into
`XBGR_DITHER` now store `$5`, the zero vector the initialiser makes with `il $5,0`, so both tables are the same zeros
and no primitive is dithered, whatever the game asks for in its draw mode.

The RSX also has a dither switch (method 0x0304, `SetDitherEnable` at 0x28244, always called with a constant). It only
matters for targets with fewer than 8 bits per channel, but it is switched off too: the function now sends 0.
"""
from __future__ import annotations

SPU_IMAGE_OFF, SPU_IMAGE_SIZE = 0x171100, 0x19100  # the SPU ELF of the GPU inside the emulator image
SPU_TO_VA = 0x10000 + SPU_IMAGE_OFF + 0x80  # SPU address of a text instruction -> emulator virtual address
ZERO_REG = 5  # `il $5,0` at SPU 0x1040
# SPU address of every `stqr $x,XBGR_DITHER+n` with a non-zero offset in $x (the two zero ones already store $5)
SPU_STORES = (0x105C, 0x1060, 0x1064, 0x1068, 0x106C, 0x1070, 0x1074, 0x107C, 0x1084, 0x1088, 0x108C, 0x1090, 0x1094, 0x109C)
SPU_STOCK = {  # the stock word at each site (a store of a non-zero offset vector to a dither table entry)
    0x105C: 0x23A83287, 0x1060: 0x23A83606, 0x1064: 0x23A83989, 0x1068: 0x23A83B08, 0x106C: 0x23A83C84,
    0x1070: 0x23A8260C, 0x1074: 0x23A83D8A, 0x107C: 0x23A81E86, 0x1084: 0x23A82187, 0x1088: 0x23A82504,
    0x108C: 0x23A8268A, 0x1090: 0x23A82809, 0x1094: 0x23A82988, 0x109C: 0x23A82C84,
}
SETDITHER_VA = 0x2825C  # stock: mr r30,r4 (the enable flag); now li r30,0
SETDITHER_STOCK, SETDITHER_NEW = 0x7C9E2378, 0x3BC00000


def _zero_source(word: int) -> int:
    """The same `stqr` with the zero vector as its source (the source register is the low 7 bits)."""
    return (word & ~0x7F) | ZERO_REG


PATCHES = (
    *((SPU_TO_VA + v, SPU_STOCK[v], _zero_source(SPU_STOCK[v]), "SPU: dither table entry stores the zero vector")
      for v in SPU_STORES),
    (SETDITHER_VA, SETDITHER_STOCK, SETDITHER_NEW, "RSX: SetDitherEnable always sends 0"),
)
