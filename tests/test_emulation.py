"""Run patched emulator code in a tiny PowerPC interpreter to check what the patches do.

The interpreter covers only the instructions of the regions under test and raises on any other,
so a patch that introduces something unexpected fails loudly. Needs a stock ps1_netemu.self in
PS1_NETEMU_SELF; skipped otherwise.
"""
import os
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ps1_netemu_patch as P

pytestmark = pytest.mark.skipif("PS1_NETEMU_SELF" not in os.environ, reason="stock SELF not provided")

M64 = (1 << 64) - 1
M32 = (1 << 32) - 1


def _s16(x: int) -> int:
    return x - 0x10000 if x & 0x8000 else x


def _s32(x: int) -> int:
    x &= M32
    return x - (1 << 32) if x & 0x80000000 else x


class Cpu:
    """Registers, one CR field per cr0..cr7 (lt, gt, eq) and byte-addressed memory."""

    def __init__(self, elf: bytes):
        self.elf = elf
        self.r = [0] * 32
        self.cr = [(0, 0, 0)] * 8
        self.mem: dict[int, int] = {}

    def code(self, pc: int) -> int:
        off = P._va_to_off(self.elf, pc)
        return struct.unpack(">I", self.elf[off:off + 4])[0]

    def rd(self, addr: int, n: int) -> int:
        return int.from_bytes(bytes(self.mem.get(addr + i, 0) for i in range(n)), "big")

    def wr(self, addr: int, n: int, val: int) -> None:
        for i, b in enumerate(val.to_bytes(n, "big")):
            self.mem[addr + i] = b

    def ra0(self, ra: int) -> int:
        return 0 if ra == 0 else self.r[ra]

    def run(self, pc: int, stop: set[int]) -> int:
        """Execute from `pc` until pc lands in `stop`; returns that pc."""
        for _ in range(500):
            if pc in stop:
                return pc
            w = self.code(pc)
            op, rt, ra, rb = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
            nxt = pc + 4
            if op == 14:  # addi / li
                self.r[rt] = (self.ra0(ra) + _s16(w & 0xFFFF)) & M64
            elif op == 32:  # lwz
                self.r[rt] = self.rd((self.ra0(ra) + _s16(w & 0xFFFF)) & M64, 4)
            elif op == 34:  # lbz
                self.r[rt] = self.rd((self.ra0(ra) + _s16(w & 0xFFFF)) & M64, 1)
            elif op == 36:  # stw
                self.wr((self.ra0(ra) + _s16(w & 0xFFFF)) & M64, 4, self.r[rt] & M32)
            elif op == 30 and (w >> 2) & 7 == 0:  # rldicl
                sh, mb = rb | (((w >> 1) & 1) << 5), ((w >> 6) & 31) | (((w >> 5) & 1) << 5)
                rot = ((self.r[rt] << sh) | (self.r[rt] >> (64 - sh))) & M64 if sh else self.r[rt]
                self.r[ra] = rot & ((1 << (64 - mb)) - 1)
            elif op == 31:
                xo = (w >> 1) & 0x3FF
                if xo == 266:  # add
                    self.r[rt] = (self.r[ra] + self.r[rb]) & M64
                elif xo == 104:  # neg
                    self.r[rt] = (-self.r[ra]) & M64
                elif xo == 986:  # extsw (RS in the RT slot, RA is the destination)
                    self.r[ra] = _s32(self.r[rt]) & M64
                elif xo == 23:  # lwzx
                    self.r[rt] = self.rd((self.ra0(ra) + self.r[rb]) & M64, 4)
                elif xo == 151:  # stwx
                    self.wr((self.ra0(ra) + self.r[rb]) & M64, 4, self.r[rt] & M32)
                else:
                    raise NotImplementedError(f"{pc:#x}: xo {xo} in {w:08x}")
            elif op == 11:  # cmpwi
                a, b = _s32(self.r[ra]), _s16(w & 0xFFFF)
                self.cr[(w >> 23) & 7] = (int(a < b), int(a > b), int(a == b))
            elif op == 16:  # bc (only the two conditional forms used here)
                bo, bi = rt, ra
                bit = self.cr[bi // 4][bi % 4]
                taken = bit == 1 if bo == 12 else bit == 0 if bo == 4 else None
                if taken is None:
                    raise NotImplementedError(f"{pc:#x}: bo {bo}")
                if taken:
                    nxt = pc + _s16(w & 0xFFFC)
            elif op == 18:  # b
                li = w & 0x03FFFFFC
                nxt = pc + (li - (1 << 26) if li & (1 << 25) else li)
            else:
                raise NotImplementedError(f"{pc:#x}: opcode {op} in {w:08x}")
            pc = nxt
        raise AssertionError("did not stop")


@pytest.fixture(scope="module")
def elfs():
    stock = P.unpack(Path(os.environ["PS1_NETEMU_SELF"]).read_bytes())
    return stock, P.apply_patches(stock)


BASE, PAD = 0x10000000, 0xD8  # fake pad-record area, stride of one record


def _config_data_stage(elf: bytes, data_byte: int, analog: int, cfg_clear: int) -> dict:
    """Run the 0x43 data-byte stage for pad 1 (0xd6b5c up to its first branch out) and return the fields."""
    cpu = Cpu(elf)
    cpu.r[24], cpu.r[31], cpu.r[25] = BASE, PAD, 2
    cpu.wr(BASE + PAD + 0xC7, 1, data_byte)  # the byte the game just clocked
    cpu.wr(BASE + PAD + 0x9C, 4, analog)
    cpu.wr(BASE + PAD + 0xA0, 4, 0)  # not in config mode yet
    cpu.wr(BASE + PAD + 0xA4, 4, cfg_clear)
    exit_pc = cpu.run(0xD6B5C, {0xD57B8, 0xD6DB0, 0xD6E48})
    rd = lambda off: cpu.rd(BASE + PAD + off, 4)
    return {"analog": rd(0x9C), "config": rd(0xA0), "a4": rd(0xA4), "state": rd(0x8C), "exit": exit_pc}


@pytest.mark.parametrize("analog", [0, 1])
@pytest.mark.parametrize("cfg_clear", [0, 5])
def test_config_enter_switches_analog_on_and_keeps_the_rest(elfs, analog, cfg_clear):
    stock, patched = elfs
    before = _config_data_stage(stock, 1, analog, cfg_clear)
    after = _config_data_stage(patched, 1, analog, cfg_clear)
    assert after["analog"] == 1
    assert {k: v for k, v in after.items() if k != "analog"} == {k: v for k, v in before.items() if k != "analog"}
    assert after["config"] == 1 and after["a4"] == 0


@pytest.mark.parametrize("analog", [0, 1])
def test_config_exit_leaves_analog_alone(elfs, analog):
    stock, patched = elfs
    before = _config_data_stage(stock, 0, analog, 5)
    after = _config_data_stage(patched, 0, analog, 5)
    assert after == before and after["analog"] == analog and after["config"] == 0
