"""The emulator draws the PS1 picture with a software GPU on the SPUs (gpusoft.cc). Dithering lives in two 16-vector tables
that every draw routine picks by a flag: XBGR_DITHER (the 4x4 matrix of channel offsets) and XBGR_NO_DITHER (zeros).

These tests run the real table initialisation of the SPU image embedded in the emulator in a small SPU interpreter and
check that, after the patch, XBGR_DITHER holds the same zeros as XBGR_NO_DITHER. Needs a stock ps1_netemu.self in
PS1_NETEMU_SELF; skipped otherwise.
"""
import os
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nodither as N
import ps1_netemu_patch as P

pytestmark = pytest.mark.skipif("PS1_NETEMU_SELF" not in os.environ, reason="stock SELF not provided")

DITHER, NO_DITHER, TABLE_BYTES = 0x15170, 0x15270, 0x100
INIT_ENTRY = 0x10E8  # _GLOBAL__I_gpu1InSpuIO_Ptr: calls the static initialiser with (1, 0xffff)
M32, M128 = (1 << 32) - 1, (1 << 128) - 1


def _s(x: int, bits: int) -> int:
    return x - (1 << bits) if x & (1 << (bits - 1)) else x


@pytest.fixture(scope="module")
def elfs():
    stock = P.unpack(Path(os.environ["PS1_NETEMU_SELF"]).read_bytes())
    return stock, P.apply_patches(P.extend_text(stock))


def _spu_image(elf: bytes) -> bytes:
    off = N.SPU_IMAGE_OFF
    return elf[off:off + N.SPU_IMAGE_SIZE]


def _local_store(image: bytes) -> bytearray:
    """Lay the LOAD segments of the SPU ELF out in a 256 KB local store (bss stays zero)."""
    ls = bytearray(0x40000)
    phoff, phentsize, phnum = struct.unpack(">I", image[0x1C:0x20])[0], *struct.unpack(">HH", image[0x2A:0x2E])
    for i in range(phnum):
        ptype, off, vaddr, _, filesz, _, _, _ = struct.unpack(">IIIIIIII", image[phoff + i * phentsize:phoff + i * phentsize + 32])
        if ptype == 1:
            ls[vaddr:vaddr + filesz] = image[off:off + filesz]
    return ls


class Spu:
    """The few SPU instructions of the table initialiser (anything else raises)."""

    def __init__(self, ls: bytearray):
        self.ls, self.r = ls, [0] * 128

    def words(self, reg: int) -> list[int]:
        return [(self.r[reg] >> (96 - 32 * i)) & M32 for i in range(4)]

    def put(self, reg: int, w: list[int]) -> None:
        self.r[reg] = (w[0] << 96) | (w[1] << 64) | (w[2] << 32) | w[3]

    def run(self, pc: int, limit: int = 2000) -> int:
        for _ in range(limit):
            w = struct.unpack(">I", self.ls[pc:pc + 4])[0]
            rt, ra, rb = w & 127, (w >> 7) & 127, (w >> 14) & 127
            op7, op8, op9, op10, op11 = w >> 25, w >> 24, w >> 23, w >> 22, w >> 21
            i16, i18, i10 = (w >> 7) & 0xFFFF, (w >> 7) & 0x3FFFF, (w >> 14) & 0x3FF
            nxt = pc + 4
            if op7 == 0x21:  # ila
                self.put(rt, [i18] * 4)
            elif op9 == 0x081:  # il
                self.put(rt, [_s(i16, 16) & M32] * 4)
            elif op9 == 0x067:  # lqr
                a = (pc + (_s(i16, 16) << 2)) & 0x3FFF0
                self.r[rt] = int.from_bytes(self.ls[a:a + 16], "big")
            elif op9 == 0x047:  # stqr
                a = (pc + (_s(i16, 16) << 2)) & 0x3FFF0
                self.ls[a:a + 16] = self.r[rt].to_bytes(16, "big")
            elif op8 == 0x7C:  # ceqi (words)
                imm = _s(i10, 10) & M32
                self.put(rt, [M32 if x == imm else 0 for x in self.words(ra)])
            elif op11 == 0x3C0:  # ceq (words)
                self.put(rt, [M32 if x == y else 0 for x, y in zip(self.words(ra), self.words(rb))])
            elif op11 == 0x049:  # nor
                self.r[rt] = ~(self.r[ra] | self.r[rb]) & M128
            elif op11 == 0x041:  # or
                self.r[rt] = self.r[ra] | self.r[rb]
            elif op9 == 0x064:  # br
                nxt = (pc + (_s(i16, 16) << 2)) & 0x3FFFC
            elif op11 == 0x129:  # binz: the function returns when the word of rt is not zero
                if self.words(rt)[0]:
                    raise AssertionError("the initialiser returned before filling the tables")
            elif op11 in (0x1AC, 0x201, 0x001):  # hbr / hbrp (hints), nop / lnop
                pass
            elif op11 == 0x1A8:  # bi $0: the end of the function
                return pc
            else:
                raise NotImplementedError(f"{pc:#x}: {w:08x}")
            pc = nxt
        raise AssertionError("did not stop")


def _tables(elf: bytes) -> tuple[bytes, bytes]:
    spu = Spu(_local_store(_spu_image(elf)))
    assert spu.run(INIT_ENTRY) == 0x10E4  # bi $0 of _Z41__static_initialization_and_destruction_0ii
    return bytes(spu.ls[DITHER:DITHER + TABLE_BYTES]), bytes(spu.ls[NO_DITHER:NO_DITHER + TABLE_BYTES])


def test_the_stock_dither_table_holds_the_ps1_matrix_and_the_other_one_is_zero(elfs):
    stock, _ = elfs
    dither, none = _tables(stock)
    assert none == bytes(TABLE_BYTES)
    vectors = {dither[i:i + 16] for i in range(0, TABLE_BYTES, 16)}
    assert len(vectors) == 8  # the eight distinct offsets of the PS1 4x4 matrix (-4..3), one of them zero
    assert bytes(16) in vectors and dither != bytes(TABLE_BYTES)


def test_after_the_patch_the_dither_table_is_zero_like_the_no_dither_one(elfs):
    _, patched = elfs
    dither, none = _tables(patched)
    assert dither == none == bytes(TABLE_BYTES)


def test_every_store_into_the_dither_table_is_covered(elfs):
    """No store into XBGR_DITHER is missed: after the patch they all store the zero register $5."""
    stock, patched = elfs
    sites = []
    for pc in range(0x1018, 0x10E8, 4):
        w = struct.unpack(">I", _spu_image(patched)[0x80 + pc:0x84 + pc])[0]
        if w >> 23 == 0x047 and DITHER <= (pc + (_s((w >> 7) & 0xFFFF, 16) << 2)) & 0x3FFF0 < DITHER + TABLE_BYTES:
            sites.append(w & 127)
    assert len(sites) == 16 and set(sites) == {5}


def test_only_the_store_sources_change(elfs):
    stock, patched = elfs
    a, b = _spu_image(stock), _spu_image(patched)
    assert [i for i in range(0, len(a), 4) if a[i:i + 4] != b[i:i + 4]] == [0x80 + v for v in N.SPU_STORES]
    for va, old, new, _ in N.PATCHES:
        assert struct.unpack(">I", stock[va - 0x10000:va - 0x10000 + 4])[0] == old
        assert struct.unpack(">I", patched[va - 0x10000:va - 0x10000 + 4])[0] == new
