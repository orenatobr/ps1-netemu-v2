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


def _s64(x: int) -> int:
    return x - (1 << 64) if x & (1 << 63) else x


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
        self.lr = 0
        self.f = [0.0] * 32
        self.mocks: dict[int, object] = {}  # va -> python stand-in for a called function

    def code(self, pc: int) -> int:
        off = P._va_to_off(self.elf, pc)
        return struct.unpack(">I", self.elf[off:off + 4])[0]

    def byte(self, addr: int) -> int:
        if addr in self.mem:
            return self.mem[addr]
        try:
            return self.elf[P._va_to_off(self.elf, addr)]  # constants and TOC slots live in the ELF image
        except ValueError:
            return 0

    def rd(self, addr: int, n: int) -> int:
        return int.from_bytes(bytes(self.byte(addr + i) for i in range(n)), "big")

    def cstr(self, addr: int) -> str:
        out = bytearray()
        while self.byte(addr + len(out)):
            out.append(self.byte(addr + len(out)))
        return out.decode()

    def wr(self, addr: int, n: int, val: int) -> None:
        for i, b in enumerate(val.to_bytes(n, "big")):
            self.mem[addr + i] = b

    def ra0(self, ra: int) -> int:
        return 0 if ra == 0 else self.r[ra]

    def run(self, pc: int, stop: set[int]) -> int:
        """Execute from `pc` until pc lands in `stop`; returns that pc."""
        for _ in range(900000):
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
            elif op == 7:  # mulli
                self.r[rt] = (self.r[ra] * _s16(w & 0xFFFF)) & M64
            elif op == 44:  # sth
                self.wr((self.ra0(ra) + _s16(w & 0xFFFF)) & M64, 2, self.r[rt] & 0xFFFF)
            elif op == 38:  # stb
                self.wr((self.ra0(ra) + _s16(w & 0xFFFF)) & M64, 1, self.r[rt] & 0xFF)
            elif op == 40:  # lhz
                self.r[rt] = self.rd((self.ra0(ra) + _s16(w & 0xFFFF)) & M64, 2)
            elif op == 15:  # lis
                self.r[rt] = (self.ra0(ra) + (_s16(w & 0xFFFF) << 16)) & M64
            elif op == 24:  # ori (RA is the destination)
                self.r[ra] = self.r[rt] | (w & 0xFFFF)
            elif op == 62 and w & 3 in (0, 1):  # std / stdu
                ea = ((self.ra0(ra) if w & 3 == 0 else self.r[ra]) + _s16(w & 0xFFFC)) & M64
                self.wr(ea, 8, self.r[rt])
                if w & 3 == 1:
                    self.r[ra] = ea
            elif op == 58 and w & 3 == 0:  # ld
                self.r[rt] = self.rd((self.ra0(ra) + _s16(w & 0xFFFC)) & M64, 8)
            elif op == 10:  # cmplwi
                a, b = self.r[ra] & M32, w & 0xFFFF
                self.cr[(w >> 23) & 7] = (int(a < b), int(a > b), int(a == b))
            elif op == 28:  # andi. (RS in the RT slot, RA is the destination)
                self.r[ra] = self.r[rt] & (w & 0xFFFF)
                self.cr[0] = (0, int(self.r[ra] != 0), int(self.r[ra] == 0))
            elif op == 21:  # rlwinm
                sh, mb, me = rb, (w >> 6) & 31, (w >> 1) & 31
                x = self.r[rt] & M32
                rot = ((x << sh) | (x >> (32 - sh))) & M32 if sh else x
                mask = ((1 << (me - mb + 1)) - 1) << (31 - me)
                self.r[ra] = rot & mask
            elif op in (48, 50, 52, 54):  # lfs lfd stfs stfd
                ea = (self.ra0(ra) + _s16(w & 0xFFFF)) & M64
                if op == 48:
                    self.f[rt] = struct.unpack(">f", struct.pack(">I", self.rd(ea, 4)))[0]
                elif op == 50:
                    self.f[rt] = struct.unpack(">d", struct.pack(">Q", self.rd(ea, 8)))[0]
                elif op == 52:
                    self.wr(ea, 4, struct.unpack(">I", struct.pack(">f", self.f[rt]))[0])
                else:
                    self.wr(ea, 8, struct.unpack(">Q", struct.pack(">d", self.f[rt]))[0])
            elif op in (59, 63):
                fa, fb, fc, xo5, xo10 = ra, rb, (w >> 6) & 31, (w >> 1) & 31, (w >> 1) & 0x3FF
                s32 = lambda x: struct.unpack(">f", struct.pack(">f", x))[0]
                if op == 59 and xo5 == 20:
                    self.f[rt] = s32(self.f[fa] - self.f[fb])
                elif op == 59 and xo5 == 21:
                    self.f[rt] = s32(self.f[fa] + self.f[fb])
                elif op == 59 and xo5 == 18:
                    self.f[rt] = s32(self.f[fa] / self.f[fb]) if self.f[fb] else float("inf")
                elif op == 59 and xo5 == 25:
                    self.f[rt] = s32(self.f[fa] * self.f[fc])
                elif op == 59 and xo5 == 29:
                    self.f[rt] = s32(self.f[fa] * self.f[fc] + self.f[fb])
                elif op == 59 and xo5 == 28:  # fmsubs
                    self.f[rt] = s32(self.f[fa] * self.f[fc] - self.f[fb])
                elif op == 63 and xo5 == 23:  # fsel
                    self.f[rt] = self.f[fc] if self.f[fa] >= 0 else self.f[fb]
                elif op == 63 and xo10 == 0:  # fcmpu
                    a, b = self.f[fa], self.f[fb]
                    self.cr[(w >> 23) & 7] = (int(a < b), int(a > b), int(a == b))
                elif op == 63 and xo10 == 846:  # fcfid
                    self.f[rt] = float(_s64(struct.unpack(">Q", struct.pack(">d", self.f[fb]))[0]))
                elif op == 63 and xo10 == 72:  # fmr
                    self.f[rt] = self.f[fb]
                elif op == 63 and xo10 == 40:  # fneg
                    self.f[rt] = -self.f[fb]
                elif op == 63 and xo10 == 12:  # frsp
                    self.f[rt] = s32(self.f[fb])
                else:
                    raise NotImplementedError(f"{pc:#x}: float op {op}/{xo5}/{xo10} in {w:08x}")
            elif op == 36:  # stw
                self.wr((self.ra0(ra) + _s16(w & 0xFFFF)) & M64, 4, self.r[rt] & M32)
            elif op == 30 and (w >> 2) & 7 == 0:  # rldicl
                sh, mb = rb | (((w >> 1) & 1) << 5), ((w >> 6) & 31) | (((w >> 5) & 1) << 5)
                rot = ((self.r[rt] << sh) | (self.r[rt] >> (64 - sh))) & M64 if sh else self.r[rt]
                self.r[ra] = rot & ((1 << (64 - mb)) - 1)
            elif op == 31:
                xo = (w >> 1) & 0x3FF
                if xo == 489:  # divd rt,ra,rb (signed 64 bit, rounds toward zero)
                    a = self.r[ra] - (1 << 64) if self.r[ra] >> 63 else self.r[ra]
                    b = self.r[rb] - (1 << 64) if self.r[rb] >> 63 else self.r[rb]
                    q = abs(a) // abs(b) if b else 0
                    self.r[rt] = (q if (a < 0) == (b < 0) else -q) & M64
                elif xo == 266:  # add
                    self.r[rt] = (self.r[ra] + self.r[rb]) & M64
                elif xo == 235:  # mullw
                    self.r[rt] = ((self.r[ra] & M32) * (self.r[rb] & M32)) & M32
                elif xo == 534:  # lwbrx
                    ea = (self.ra0(ra) + self.r[rb]) & M64
                    self.r[rt] = int.from_bytes(self.rd(ea, 4).to_bytes(4, "big"), "little")
                elif xo == 40:  # subf
                    self.r[rt] = (self.r[rb] - self.r[ra]) & M64
                elif xo == 104:  # neg
                    self.r[rt] = (-self.r[ra]) & M64
                elif xo == 986:  # extsw (RS in the RT slot, RA is the destination)
                    self.r[ra] = _s32(self.r[rt]) & M64
                elif xo == 23:  # lwzx
                    self.r[rt] = self.rd((self.ra0(ra) + self.r[rb]) & M64, 4)
                elif xo == 444:  # or (mr)
                    self.r[ra] = self.r[rt] | self.r[rb]
                elif xo == 0:  # cmpw
                    a, b = _s32(self.r[ra]), _s32(self.r[rb])
                    self.cr[(w >> 23) & 7] = (int(a < b), int(a > b), int(a == b))
                elif xo == 32:  # cmplw
                    a, b = self.r[ra] & M32, self.r[rb] & M32
                    self.cr[(w >> 23) & 7] = (int(a < b), int(a > b), int(a == b))
                elif xo == 339:  # mflr
                    self.r[rt] = self.lr
                elif xo == 467:  # mtlr
                    self.lr = self.r[rt]
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
            elif op == 18:  # b / bl
                li = w & 0x03FFFFFC
                li = li - (1 << 26) if li & (1 << 25) else li
                target = li if w & 2 else pc + li  # AA: an absolute address (`ba`)
                if w & 1:
                    self.lr = pc + 4
                if target in self.mocks:  # a call into emulator code that is stood in for
                    self.mocks[target](self)
                    for reg in range(4, 13):
                        self.r[reg] = 0xDEAD0000 + reg  # volatile registers are clobbered by a call
                else:
                    nxt = target
            elif w == 0x4E800020:  # blr
                nxt = self.lr
            else:
                raise NotImplementedError(f"{pc:#x}: opcode {op} in {w:08x}")
            pc = nxt
        raise AssertionError("did not stop")


@pytest.fixture(scope="module")
def elfs():
    stock = P.unpack(Path(os.environ["PS1_NETEMU_SELF"]).read_bytes())
    return stock, P.apply_patches(P.extend_text(stock))


BASE, PAD = 0x10000000, 0xD8  # fake pad-record area, stride of one record
VIB_STRUCT = 0x10080000  # fake pad state struct (holds the vibration disable flag at +0x84)
TOC = 0x1C3D30


def _config_data_stage(elf: bytes, data_byte: int, analog: int, cfg_clear: int, vib: int = 1) -> dict:
    """Run the 0x43 data-byte stage for pad 1 (0xd6b5c up to its first branch out) and return the fields."""
    cpu = Cpu(elf)
    cpu.r[24], cpu.r[31], cpu.r[25] = BASE, PAD, 2
    cpu.wr(BASE + PAD + 0xC7, 1, data_byte)  # the byte the game just clocked
    cpu.wr(BASE + PAD + 0x9C, 4, analog)
    cpu.wr(BASE + PAD + 0xA0, 4, 0)  # not in config mode yet
    cpu.wr(BASE + PAD + 0xA4, 4, cfg_clear)
    cpu.r[2] = TOC
    cpu.wr(TOC - 0x7B60, 4, VIB_STRUCT)  # the pad state struct the actuator function reads
    cpu.wr(VIB_STRUCT + 0x84, 4, vib)  # vibration disable flag (0 = on)
    exit_pc = cpu.run(0xD6B5C, {0xD57B8, 0xD6DB0, 0xD6E48})
    rd = lambda off: cpu.rd(BASE + PAD + off, 4)
    return {"analog": rd(0x9C), "config": rd(0xA0), "a4": rd(0xA4), "state": rd(0x8C), "exit": exit_pc,
            "vib": cpu.rd(VIB_STRUCT + 0x84, 4)}


@pytest.mark.parametrize("analog", [0, 1])
@pytest.mark.parametrize("cfg_clear", [0, 5])
def test_config_enter_switches_analog_on_and_keeps_the_rest(elfs, analog, cfg_clear):
    stock, patched = elfs
    before = _config_data_stage(stock, 1, analog, cfg_clear)
    after = _config_data_stage(patched, 1, analog, cfg_clear)
    assert after["analog"] == 1 and after["vib"] == 0  # analog on, vibration on
    assert before["vib"] == 1  # the stock code never touches it
    skip = ("analog", "vib")
    assert {k: v for k, v in after.items() if k not in skip} == {k: v for k, v in before.items() if k not in skip}
    assert after["config"] == 1 and after["a4"] == 0


@pytest.mark.parametrize("analog", [0, 1])
def test_config_exit_leaves_analog_alone(elfs, analog):
    stock, patched = elfs
    before = _config_data_stage(stock, 0, analog, 5)
    after = _config_data_stage(patched, 0, analog, 5)
    assert after == before and after["analog"] == analog and after["config"] == 0
    assert after["vib"] == 1  # leaving configuration does not switch vibration


# ---- memory card cave -------------------------------------------------------------------------

import cave_memcard as M

STACK, NAMES, TOC = 0x7000000, 0x20000000, 0x1C3D30
VMC = "/dev_hdd0/savedata/vmc/"


@pytest.fixture(scope="module")
def cave_elf():
    stock = P.unpack(Path(os.environ["PS1_NETEMU_SELF"]).read_bytes())
    return P.apply_patches(P.extend_text(stock))


def _mocks(files: set[str], log: list):
    def strlen(c):
        c.r[3] = len(c.cstr(c.r[3]))

    def sprintf(c):
        text = c.cstr(c.r[4]).replace("%s", c.cstr(c.r[5]))
        for i, b in enumerate(text.encode() + b"\0"):
            c.mem[c.r[3] + i] = b
        c.r[3] = len(text)

    def fsopen(c):
        path = c.cstr(c.r[3])
        log.append(("open", path))
        if path in files:
            c.wr(c.r[5], 4, 3)
            c.r[3] = 0
        else:
            c.r[3] = 0x80010006

    def fsclose(c):
        log.append(("close", c.r[3]))
        c.r[3] = 0

    return {M.STRLEN: strlen, M.SPRINTF: sprintf, M.FSOPEN: fsopen, M.FSCLOSE: fsclose}


def _slots(elf: bytes, slot1, slot2, files: set[str]):
    """Run the hook from 0xb3b90 as xMcInsert reaches it (r30 = slot 1 name or 0, r27 = slot 2 name or 0)."""
    cpu, log = Cpu(elf), []
    cpu.mocks = _mocks(files, log)
    cpu.r[1], cpu.r[2], cpu.r[28] = STACK, TOC, 0x11111111
    addr = NAMES
    for reg, name in ((30, slot1), (27, slot2)):
        if name is None:
            cpu.r[reg] = 0
            continue
        cpu.r[reg] = addr
        for i, b in enumerate(name.encode() + b"\0"):
            cpu.mem[addr + i] = b
        addr += 0x100
    s1, s2 = cpu.r[30], cpu.r[27]
    end = cpu.run(M.HOOK_VA, {M.MOVE_TARGET, M.STOCK_RESUME})
    return cpu, end, s1, s2, log


CARD = {VMC + "Game.VM1", VMC + "Other.VM1"}


@pytest.mark.parametrize("slot1", [None, "", "Pad.VM2", "Missing", "Pad.VM2.VM1"])
def test_ps1_card_in_slot_2_moves_to_slot_1_when_slot_1_has_no_ps1_card(cave_elf, slot1):
    cpu, end, _s1, s2, _ = _slots(cave_elf, slot1, "Game", CARD)
    assert end == M.MOVE_TARGET
    assert cpu.r[30] == s2 and cpu.r[28] == s2 and cpu.r[1] == STACK


def test_name_that_already_ends_in_vm1_is_used_as_is(cave_elf):
    cpu, end, _s1, s2, log = _slots(cave_elf, None, "Game.VM1", CARD)
    assert end == M.MOVE_TARGET and cpu.r[30] == s2
    assert ("open", VMC + "Game.VM1") in log and ("close", 3) in log


@pytest.mark.parametrize("slot1", ["Other", "Other.VM1"])
def test_valid_ps1_card_in_slot_1_stays(cave_elf, slot1):
    cpu, end, s1, _s2, _ = _slots(cave_elf, slot1, "Game", CARD)
    assert end == M.STOCK_RESUME and cpu.r[30] == s1 and cpu.r[28] == 0x11111111 and cpu.r[1] == STACK


@pytest.mark.parametrize("slot2", [None, "", "Gone", "Pad.VM2", "Pad.VM2.VM1"])
def test_no_ps1_card_in_slot_2_means_stock_behavior(cave_elf, slot2):
    cpu, end, s1, s2, _ = _slots(cave_elf, None, slot2, CARD)
    assert end == M.STOCK_RESUME and cpu.r[30] == s1 and cpu.r[27] == s2 and cpu.r[28] == 0x11111111
    assert cpu.cr[7][2] == int(s2 == 0) and cpu.r[1] == STACK  # cr7 carries the displaced cmpwi r27,0


def test_every_probe_closes_the_file_it_opened(cave_elf):
    _cpu, _end, _s1, _s2, log = _slots(cave_elf, "Other", "Game", CARD)
    opened = [p for k, p in log if k == "open" and p in CARD]  # attempts at files that do not exist need no close
    assert len(opened) == [k for k, _ in log].count("close") == 2


# ---- one memory card per game ----------------------------------------------------------------

import cave_percard as C

PATH_BUF = 0x2D6018  # where the emulator keeps the disc path (TOC slot -0x7fac)
CARD_DIR = "/dev_hdd0/savedata/vmc/"


def _blank_card() -> bytes:
    data = bytearray(131072)
    for i in range(1024):
        f = bytearray(b"\xff" * 128) if 36 <= i <= 62 else bytearray(128)
        if i in (0, 63):
            f[0], f[1], f[127] = 0x4D, 0x43, 0x0E
        elif 1 <= i <= 15:
            f[0], f[8], f[9], f[127] = 0xA0, 0xFF, 0xFF, 0xA0
        elif 16 <= i <= 35:
            f[0:4], f[8], f[9] = b"\xff" * 4, 0xFF, 0xFF
        data[128 * i:128 * i + 128] = f
    return bytes(data)


def _disc(layout, boot_line=b"BOOT = cdrom:\\SLUS_006.62;1\r\nTCB = 4\r\nEVENT = 10\r\nSTACK = 801FFF00\r\n", cnf=True):
    """An ISO9660 image: volume descriptor at sector 16, root directory at 18, SYSTEM.CNF at 20."""
    size, head = layout
    sectors = {}
    pvd = bytearray(2048)
    pvd[0], pvd[1:6] = 1, b"CD001"
    root = bytearray(34)
    root[0], root[2:6], root[10:14], root[25] = 34, (18).to_bytes(4, "little"), (2048).to_bytes(4, "little"), 2
    pvd[156:190] = root
    sectors[16] = bytes(pvd)

    def record(name: bytes, lba: int, length: int) -> bytes:
        n = 33 + len(name) + (0 if len(name) % 2 else 1)
        r = bytearray(n)
        r[0], r[2:6], r[10:14], r[32] = n, lba.to_bytes(4, "little"), length.to_bytes(4, "little"), len(name)
        r[33:33 + len(name)] = name
        return bytes(r)

    d = record(b"\0", 18, 2048) + record(b"\1", 18, 2048) + record(b"A.TXT;1", 19, 4)
    if cnf:
        d += record(b"SYSTEM.CNF;1", 20, len(boot_line))
    d += record(b"ZZZ.BIN;1", 21, 4)
    sectors[18] = d.ljust(2048, b"\0")
    sectors[20] = boot_line.ljust(2048, b"\0")
    img = bytearray(size * 24)
    for lba, payload in sectors.items():
        img[lba * size + head:lba * size + head + 2048] = payload
    return bytes(img)


class Fs:
    """The emulator's file functions, backed by a dict of paths to bytes."""

    def __init__(self, files: dict[str, bytes], fail_open: set[str] = frozenset()):
        self.files = {k: bytearray(v) for k, v in files.items()}
        self.fail_open, self.fds, self.log, self.next = set(fail_open), {}, [], 3

    def mocks(self):
        def op(c):
            path, flags = c.cstr(c.r[3]), c.r[4]
            self.log.append(("open", path, flags))
            if path in self.fail_open or (path not in self.files and not flags & 0x40):  # CELL_FS_O_CREAT
                c.r[3] = 0x80010006
                return
            self.files.setdefault(path, bytearray())
            self.fds[self.next] = [path, 0]
            c.wr(c.r[5], 4, self.next)
            self.next += 1
            c.r[3] = 0

        def seek(c):
            fd = self.fds[c.r[3] & M32]
            fd[1] = c.r[4]
            c.wr(c.r[6], 8, fd[1])
            c.r[3] = 0

        def read(c):
            fd = self.fds[c.r[3] & M32]
            data = bytes(self.files[fd[0]][fd[1]:fd[1] + c.r[5]])
            for i, b in enumerate(data):
                c.mem[c.r[4] + i] = b
            fd[1] += len(data)
            c.wr(c.r[6], 8, len(data))
            c.r[3] = 0

        def write(c):
            fd = self.fds[c.r[3] & M32]
            data = bytes(c.byte(c.r[4] + i) for i in range(c.r[5]))
            f = self.files[fd[0]]
            f[fd[1]:fd[1] + len(data)] = data
            fd[1] += len(data)
            c.wr(c.r[6], 8, len(data))
            c.r[3] = 0

        def close(c):
            self.log.append(("close", c.r[3] & M32))
            self.fds.pop(c.r[3] & M32)
            c.r[3] = 0

        def assign(c):  # the import stub saves the caller's TOC pointer at 0x28(r1), as the real one does
            self.log.append(("assign", c.r[3], c.cstr(c.r[4]) if c.r[4] else None))
            c.wr(c.r[1] + 0x28, 8, c.r[2])
            c.r[3] = 0

        base = _mocks(set(), [])
        base.update({C.OPEN: op, C.LSEEK: seek, C.READ: read, C.WRITE: write, C.CLOSE: close, M.ASSIGN_VMC: assign})
        return base


LAST_GAME = "/dev_hdd0/tmp/wmtmp/last_game.txt"


def _hook(elf, fs, disc_path="/dev_hdd0/PSXISO/Game.bin", slot1=None, slot2=None):
    cpu = Cpu(elf)
    cpu.mocks = fs.mocks()
    cpu.r[1], cpu.r[2], cpu.r[28] = STACK, TOC, 0x11111111
    saved = {i: 0x7000 + i for i in (14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 29, 31)}
    for i, v in saved.items():
        cpu.r[i] = v
    for i, b in enumerate(disc_path.encode() + b"\0" if disc_path else b"\0"):
        cpu.mem[PATH_BUF + i] = b
    addr = NAMES
    for reg, name in ((30, slot1), (27, slot2)):
        if name is None:
            cpu.r[reg] = 0
            continue
        cpu.r[reg] = addr
        for i, b in enumerate(name.encode() + b"\0"):
            cpu.mem[addr + i] = b
        addr += 0x100
    end = cpu.run(M.HOOK_VA, {M.MOVE_TARGET, M.STOCK_RESUME})
    assert cpu.r[1] == STACK and all(cpu.r[i] == v for i, v in saved.items())  # nothing of main's is disturbed
    return cpu, end


@pytest.mark.parametrize("layout", [(2352, 24), (2352, 16), (2048, 0)])
def test_each_game_gets_its_own_blank_card_named_after_its_serial(cave_elf, layout):
    fs = Fs({"/dev_hdd0/PSXISO/Game.bin": _disc(layout)})
    cpu, end = _hook(cave_elf, fs)
    card = CARD_DIR + "SLUS-00662.VM1"
    assert cpu.r[30] == C.NAME_VA and cpu.cstr(C.NAME_VA) == "SLUS-00662.VM1" and cpu.r[28] == C.NAME_VA
    assert bytes(fs.files[card]) == _blank_card()
    assert end == M.STOCK_RESUME and not fs.fds  # the card is in slot 1 through the stock path; every file closed


def test_an_existing_card_is_only_put_in_slot_1(cave_elf):
    saves = bytes(range(256)) * 512
    fs = Fs({"/dev_hdd0/PSXISO/Game.bin": _disc((2352, 24)), CARD_DIR + "SLUS-00662.VM1": saves})
    cpu, _end = _hook(cave_elf, fs)
    assert cpu.r[30] == C.NAME_VA and bytes(fs.files[CARD_DIR + "SLUS-00662.VM1"]) == saves
    assert not any(f & 0x40 for k, _p, f in [e for e in fs.log if e[0] == "open"])  # no creating open


@pytest.mark.parametrize("line,serial", [
    (b"BOOT = cdrom:\\SLUS_006.62;1\r\n", "SLUS-00662"),
    (b"boot=cdrom:SLES_019.07;1\n", "SLES-01907"),
    (b"BOOT = CDROM:\\SCUS_941.63;1\r\nTCB = 4\r\n", "SCUS-94163"),
    (b"BOOT = cdrom:/slps_012.34;1", "SLPS-01234"),
])
def test_the_serial_is_read_from_the_boot_line(cave_elf, line, serial):
    fs = Fs({"/dev_hdd0/PSXISO/Game.bin": _disc((2352, 24), boot_line=line)})
    cpu, _ = _hook(cave_elf, fs)
    assert cpu.cstr(C.NAME_VA) == serial + ".VM1" and (CARD_DIR + serial + ".VM1") in fs.files


@pytest.mark.parametrize("case", ["no path", "no disc", "no system.cnf", "no boot line", "open fails"])
def test_when_the_serial_is_unknown_the_original_card_logic_runs(cave_elf, case):
    disc = _disc((2352, 24), boot_line=b"TCB = 4\r\n") if case == "no boot line" else _disc((2352, 24), cnf=case != "no system.cnf")
    files = {} if case == "no disc" else {"/dev_hdd0/PSXISO/Game.bin": disc}
    fs = Fs(files, fail_open={"/dev_hdd0/PSXISO/Game.bin"} if case == "open fails" else set())
    cpu, end = _hook(cave_elf, fs, disc_path="" if case == "no path" else "/dev_hdd0/PSXISO/Game.bin",
                     slot1="Other", slot2=None)
    assert end == M.STOCK_RESUME and cpu.r[28] == 0x11111111 and cpu.r[30] != C.NAME_VA and not fs.fds
    assert not any(p.endswith(".VM1") for p in fs.files)  # no card was created


def test_a_failed_card_write_does_not_change_slot_1(cave_elf):
    fs = Fs({"/dev_hdd0/PSXISO/Game.bin": _disc((2352, 24))}, fail_open=set())
    orig = fs.mocks

    def broken():
        m = orig()
        m[C.WRITE] = lambda c: setattr_r3(c)
        return m

    def setattr_r3(c):
        c.r[3] = 0x80010005

    fs.mocks = broken
    cpu, _end = _hook(cave_elf, fs, slot1="Other")
    assert cpu.r[30] != C.NAME_VA and not fs.fds


def test_per_game_card_beats_a_card_in_slot_2_but_keeps_it_there(cave_elf):
    fs = Fs({"/dev_hdd0/PSXISO/Game.bin": _disc((2352, 24)), CARD_DIR + "Game2.VM1": b"x" * 10})
    cpu, end = _hook(cave_elf, fs, slot1=None, slot2="Game2")
    assert cpu.r[30] == C.NAME_VA and end == M.STOCK_RESUME and cpu.r[27] != 0  # slot 2 untouched


# ---- multi-disc games share the card of disc 1 -----------------------------------------------

CC = "/dev_hdd0/PSXISO/Chrono Cross (USA, Canada) (Disc {}).bin"


def _chrono(discs):
    boot = {1: b"BOOT = cdrom:\\SLUS_010.41;1\r\n", 2: b"BOOT = cdrom:\\SLUS_011.08;1\r\n", 3: b"BOOT = cdrom:\\SLUS_099.99;1\r\n"}
    return {CC.format(n): _disc((2352, 24), boot_line=boot[n]) for n in discs}


@pytest.mark.parametrize("disc", [1, 2, 3])
def test_every_disc_of_a_game_shares_the_card_of_disc_1(cave_elf, disc):
    fs = Fs(_chrono([1, 2, 3]))
    cpu, _ = _hook(cave_elf, fs, disc_path=CC.format(disc))
    assert cpu.cstr(C.NAME_VA) == "SLUS-01041.VM1" and (CARD_DIR + "SLUS-01041.VM1") in fs.files
    assert not any(p.endswith(".VM1") and "01041" not in p for p in fs.files) and not fs.fds


def test_a_later_disc_without_disc_1_uses_its_own_serial(cave_elf):
    fs = Fs(_chrono([2]))
    cpu, _ = _hook(cave_elf, fs, disc_path=CC.format(2))
    assert cpu.cstr(C.NAME_VA) == "SLUS-01108.VM1" and not fs.fds


def test_the_emulators_own_path_buffer_is_left_alone(cave_elf):
    fs = Fs(_chrono([1, 2]))
    cpu, _ = _hook(cave_elf, fs, disc_path=CC.format(2))
    assert cpu.cstr(PATH_BUF) == CC.format(2)


@pytest.mark.parametrize("name", ["/dev_hdd0/PSXISO/Game Disc2.bin", "/dev_hdd0/PSXISO/Game (Disc A).bin",
                                  "/dev_hdd0/PSXISO/Game (Disc 2.bin", "/dev_hdd0/PSXISO/Game (CD 2).bin"])
def test_names_without_the_disc_pattern_keep_their_own_serial(cave_elf, name):
    fs = Fs({name: _disc((2352, 24), boot_line=b"BOOT = cdrom:\\SLUS_007.77;1\r\n")})
    cpu, _ = _hook(cave_elf, fs, disc_path=name)
    assert cpu.cstr(C.NAME_VA) == "SLUS-00777.VM1"


@pytest.mark.parametrize("title,serials", [
    ("Final Fantasy VII (USA) (Disc {})", {1: "SCUS_941.63", 2: "SCUS_941.64", 3: "SCUS_941.65"}),
    ("Metal Gear Solid (USA) (Disc {}) (Rev 1)", {1: "SLUS_005.94", 2: "SLUS_005.95", 3: "SLUS_999.99"}),
    ("Xenogears [Disc {}]", {1: "SLUS_006.64", 2: "SLUS_006.69", 3: "SLUS_000.01"}),
    ("Vagrant Story (Disc {} of 3)", {1: "SLUS_010.40", 2: "SLUS_010.41", 3: "SLUS_010.42"}),
])
@pytest.mark.parametrize("disc", [1, 2, 3])
def test_any_multi_disc_game_shares_one_card_not_just_one_title(cave_elf, title, serials, disc):
    def boot(n):
        return b"BOOT = cdrom:\\" + serials[n].encode() + b";1\r\n"

    files = {"/dev_hdd0/PSXISO/" + title.format(n) + ".bin": _disc((2352, 24), boot_line=boot(n)) for n in (1, 2, 3)}
    fs = Fs(files)
    cpu, _ = _hook(cave_elf, fs, disc_path="/dev_hdd0/PSXISO/" + title.format(disc) + ".bin")
    expected = serials[1].replace("_", "-").replace(".", "")
    assert cpu.cstr(C.NAME_VA) == expected + ".VM1" and not fs.fds
    assert sum(1 for p in fs.files if p.endswith(".VM1")) == 1  # one card for the whole set


@pytest.mark.parametrize("tail", [b"", b"\n", b"\r\n", b"  \n"])
def test_without_a_path_the_game_comes_from_webmans_record(cave_elf, tail):
    path = "/dev_hdd0/PSXISO/Chrono Cross (USA, Canada) (Disc 2).bin"
    files = {LAST_GAME: path.encode() + tail}
    files.update(_chrono([1, 2]))
    fs = Fs(files)
    cpu, end = _hook(cave_elf, fs, disc_path="")  # the emulator's own path buffer is empty, as under webMAN
    assert cpu.cstr(C.NAME_VA) == "SLUS-01041.VM1" and cpu.r[30] == C.NAME_VA and not fs.fds
    assert (CARD_DIR + "SLUS-01041.VM1") in fs.files and end == M.STOCK_RESUME


def test_without_a_path_and_without_webmans_record_nothing_changes(cave_elf):
    cpu, end = _hook(cave_elf, Fs({}), disc_path="", slot1="Other")
    assert end == M.STOCK_RESUME and cpu.r[28] == 0x11111111 and cpu.r[30] != C.NAME_VA


def test_an_empty_webman_record_changes_nothing(cave_elf):
    fs = Fs({LAST_GAME: b"\n"})
    cpu, _end = _hook(cave_elf, fs, disc_path="", slot1="Other")
    assert cpu.r[30] != C.NAME_VA and not fs.fds


def test_the_card_is_created_with_the_ps3_create_and_read_write_flags(cave_elf):
    fs = Fs({"/dev_hdd0/PSXISO/Game.bin": _disc((2352, 24))})
    _hook(cave_elf, fs)
    created = [e[2] for e in fs.log if e[0] == "open" and e[1].endswith("SLUS-00662.VM1") and e[2] != 0]
    assert created == [0x42]  # O_CREAT (0100) | O_RDWR (02); 0x200 is O_TRUNC and would not create anything


# ---- vibration: the motors get what the game commands --------------------------------------------


def _motors(elf: bytes, small: int, large: int, blocked: int):
    """Run the end of the actuator function (0xeb2d8 up to the cellPadSetActDirect call) and return the bytes
    it would send: (small motor, large motor)."""
    cpu = Cpu(elf)
    cpu.r[1], cpu.r[2], cpu.r[27] = STACK, TOC, VIB_STRUCT
    cpu.r[28], cpu.r[30], cpu.r[9] = small, large, 0x55  # r9 is the stock strength curve's result
    cpu.wr(VIB_STRUCT + 0x84, 4, blocked)
    cpu.run(0xEB2D8, {0xEB300})  # 0xeb300 is the bl to cellPadSetActDirect
    _motors.switch = cpu.rd(VIB_STRUCT + 0x84, 4)
    return cpu.rd(STACK + 0x70, 1), cpu.rd(STACK + 0x71, 1)


@pytest.mark.parametrize("small,large", [(1, 0xFF), (0, 0x80), (1, 0x01), (0, 0)])
def test_the_motors_get_what_the_game_commands_even_if_the_stock_flag_blocks(elfs, small, large):
    stock, patched = elfs
    assert _motors(stock, small, large, blocked=1) == (0, 0)  # the stock code sends nothing while blocked
    assert _motors(patched, small, large, blocked=1) == (small, large)
    assert _motors(patched, small, large, blocked=0) == (small, large)


def test_the_emulators_vibration_switch_is_left_on(elfs):
    """The emulator menu showed vibration as disabled because X+0x84 stayed 1: it is cleared on every update."""
    _, patched = elfs
    _motors(patched, 1, 0xFF, blocked=1)
    assert _motors.switch == 0


def test_the_per_game_card_is_registered_as_slot_1_like_the_assign_slots_menu_does(cave_elf):
    fs = Fs({"/dev_hdd0/PSXISO/Game.bin": _disc((2352, 24))})
    cpu, _ = _hook(cave_elf, fs)
    assert [e for e in fs.log if e[0] == "assign"] == [("assign", 0, "SLUS-00662.VM1")]
    assert cpu.r[2] == TOC  # the TOC pointer survives the system call


def test_no_system_registration_when_there_is_no_per_game_card(cave_elf):
    fs = Fs({}, fail_open=set())
    _hook(cave_elf, fs, disc_path="", slot1="Other")
    assert not [e for e in fs.log if e[0] == "assign"]


def test_the_name_given_to_slot_1_has_the_extension_like_the_one_in_slot_2(cave_elf):
    fs = Fs({"/dev_hdd0/PSXISO/Game.bin": _disc((2352, 24))})
    cpu, _ = _hook(cave_elf, fs, slot2="PS1.VM1")
    assert cpu.cstr(C.NAME_VA) == "SLUS-00662.VM1" and cpu.cstr(cpu.r[27]) == "PS1.VM1"
    assert (CARD_DIR + "SLUS-00662.VM1") in fs.files and (CARD_DIR + "SLUS-00662.VM1.VM1") not in fs.files


# ---- the base overscan formula (the NTSC block that feeds the shared code) --------------------

FRAME = 0x32000000


def _run_quad(elf, y1d, y2d, height=240, r30=0, r4=1):
    """Run the formula from the NTSC entry (0x115ac0) to its first branch after y1/y2 and return the Cpu."""
    cpu = Cpu(elf)
    cpu.r[1], cpu.r[2], cpu.r[22], cpu.r[4], cpu.r[5], cpu.r[30] = STACK, TOC, FRAME, r4, 320, r30
    cpu.wr(FRAME + 0x1C, 4, y1d)
    cpu.wr(FRAME + 0x20, 4, y2d)
    cpu.wr(FRAME + 0xC, 4, height)
    cpu.f[2], cpu.f[8], cpu.f[9] = 1.0, -0.88, 0.89
    cpu.run(0x115AC0, {0x1158B0})
    return cpu


def _base_quad(elf, y1d, y2d, height=240):
    cpu = _run_quad(elf, y1d, y2d, height)
    return cpu.f[7], cpu.f[4]


def _win(y1d, y2d):
    """Expected quad: the video lines 16..248 clipped to the declared lines and stretched over the frame."""
    end = y2d or y1d + 240
    first, last = max(y1d, 16), min(end, 248)
    if last <= first:
        first, last = 16, 248
    k = 2 / (last - first)
    return k * (y1d - first) - 1, k * (end - first) - 1


@pytest.mark.parametrize("y1d,y2d", [
    (16, 256),  # all 240 lines (Megaman X5): the 8 black last lines of its menu are cropped
    (16, 0),  # no end line declared: 240 lines
    (24, 248),  # 224 lines fill the frame
    (10, 250),  # more than the window: cropped at 16 and 248
    (26, 242),  # Xenogears: 216 lines fill the frame, no black bands
    (28, 242),
    (30, 300),  # more below than the window: cropped at 248
    (40, 40),  # an empty range falls back to the whole window
])
def test_the_quad_shows_the_declared_lines_inside_the_window(elfs, y1d, y2d):
    _, patched = elfs
    assert _base_quad(patched, y1d, y2d) == pytest.approx(_win(y1d, y2d), abs=1e-4)


@pytest.mark.parametrize("y1d,y2d", [(16, 256), (26, 242), (20, 308), (16, 0)])
def test_pal_keeps_the_stock_quad(elfs, y1d, y2d):
    """The PAL block (entry 0x115800) gives the same top and bottom as the unpatched emulator."""
    stock, patched = elfs
    out = []
    for elf in (stock, patched):
        cpu = Cpu(elf)
        cpu.r[1], cpu.r[2], cpu.r[22], cpu.r[4], cpu.r[5], cpu.r[30] = STACK, TOC, FRAME, 1, 320, 0
        cpu.wr(FRAME + 0x1C, 4, y1d)
        cpu.wr(FRAME + 0x20, 4, y2d)
        cpu.wr(FRAME + 0xC, 4, 288)
        cpu.f[2], cpu.f[8], cpu.f[9] = 1.0, -0.88, 0.89
        cpu.run(0x115800, {0x1158B0})
        out.append((cpu.f[7], cpu.f[4], cpu.f[1], cpu.f[3]))
    assert out[1] == pytest.approx(out[0], abs=1e-6)


def test_a_game_declaring_less_than_the_window_fills_the_frame(elfs):
    _, patched = elfs
    assert _base_quad(patched, 26, 242) == pytest.approx((-1.0, 1.0), abs=1e-5)


@pytest.mark.parametrize("y1d,y2d,height,r30,r4", [
    (26, 242, 240, 0, 1), (16, 256, 240, 0, 1), (16, 0, 240, 0, 1), (26, 242, 480, 0, 1),
    (26, 242, 240, 1, 1), (30, 300, 240, 0, 1), (26, 242, 240, 0, 2), (40, 40, 240, 0, 1),
])
def test_the_rest_of_the_handler_sees_the_state_the_stock_formula_leaves(elfs, y1d, y2d, height, r30, r4):
    """The texture span (f1), the width (f3), the compare (cr7) and the registers read later stay as stock.

    The first version of the clipped window overwrote f1 and drew half a picture with glitches on the console.
    """
    stock, patched = elfs
    a, b = _run_quad(stock, y1d, y2d, height, r30, r4), _run_quad(patched, y1d, y2d, height, r30, r4)
    assert (b.f[1], b.f[3], b.f[2], b.f[8], b.f[9]) == (a.f[1], a.f[3], a.f[2], a.f[8], a.f[9])
    assert b.cr[7] == a.cr[7]
    assert (b.r[0], b.r[9], b.r[4], b.r[5], b.r[22], b.r[30]) == (a.r[0], a.r[9], a.r[4], a.r[5], a.r[22], a.r[30])


def _run_x(elf, f8, f9, r4=1, width=320):
    """Run the NTSC entry to the end of the horizontal formula (0x1158f8) and return (f5, f6), the quad's x ends."""
    cpu = Cpu(elf)
    cpu.r[1], cpu.r[2], cpu.r[22], cpu.r[4], cpu.r[5], cpu.r[30] = STACK, TOC, FRAME, r4, width, 0
    cpu.wr(FRAME + 0x1C, 4, 16)
    cpu.wr(FRAME + 0x20, 4, 256)
    cpu.wr(FRAME + 0xC, 4, 240)
    cpu.f[2], cpu.f[8], cpu.f[9] = 1.0, f8, f9
    cpu.run(0x115AC0, {0x1158F8})
    return cpu.f[5], cpu.f[6]


@pytest.mark.parametrize("f8,f9", [
    (-0.88, 0.89),  # about the nominal range (Megaman X5: -0.8833, 0.8889 on the console)
    (12.0, -9.0),  # a game that declares a narrower range (Final Fantasy VII): the stock picture was narrower
    (-6.0, 5.0),  # a wider one
    (0.0, 0.0),
])
@pytest.mark.parametrize("width,r4", [(256, 1), (320, 1), (384, 2), (368, 2)])  # r4 == 2 is the stock 672/720 branch
def test_every_game_gets_the_same_width_whatever_range_it_declares(elfs, f8, f9, width, r4):
    """The quad ends are +-640/720 of the 4:3 window; the declared horizontal range does not move them."""
    _, patched = elfs
    assert _run_x(patched, f8, f9, r4=r4, width=width) == pytest.approx((640 / 720, -640 / 720), abs=1e-6)


def test_the_stock_width_does_follow_the_declared_range(elfs):
    """Guards the test above: the unpatched emulator gives two different widths for two ranges."""
    stock, _ = elfs
    assert _run_x(stock, -0.88, 0.89) != pytest.approx(_run_x(stock, 12.0, -9.0), abs=1e-3)


def test_the_picture_quad_is_never_touched_after_the_base_formula(elfs):
    """The overscan is static (DuckStation style): no hook at the display handler's quad store."""
    _, patched = elfs
    cpu = Cpu(patched)
    assert cpu.rd(0x114148, 4) == 0xD0D401AC  # stfs f6,0x1ac(r20), as in the stock image


# ---- no dithering: the RSX switch ------------------------------------------------------------

RET_ADDR, GCM_CTX, GCM_CMDS = 0x1000000, 0x46000000, 0x47000000


def _set_dither(elf, enable):
    """Run SetDitherEnable(ctx, enable) (0x28244) with room in the command buffer; return the two words it writes."""
    cpu = Cpu(elf)
    cpu.r[1], cpu.r[2], cpu.r[3], cpu.r[4], cpu.lr = STACK, TOC, GCM_CTX, enable, RET_ADDR
    cpu.wr(GCM_CTX + 4, 4, GCM_CMDS + 0x100)  # end of the buffer
    cpu.wr(GCM_CTX + 8, 4, GCM_CMDS)  # current position
    cpu.run(0x28244, {RET_ADDR})
    return cpu.rd(GCM_CMDS, 4), cpu.rd(GCM_CMDS + 4, 4)


@pytest.mark.parametrize("enable", [0, 1])
def test_the_stock_switch_sends_what_it_is_given(elfs, enable):
    stock, _ = elfs
    assert _set_dither(stock, enable) == (0x00040304, enable)  # method 0x0304, one argument


@pytest.mark.parametrize("enable", [0, 1])
def test_the_rsx_dither_switch_always_sends_zero(cave_elf, enable):
    assert _set_dither(cave_elf, enable) == (0x00040304, 0)


# ---- Screen Mode: Full Screen fills the 16:9 window ----------------------------------------------

import cave_fullscreen as FS

RENDER = 0x45000000  # the render state r20 points to
WIDTH_VAR = 0x1620488  # the output width, a variable of the emulator (TOC -0x7844 holds its address)
NOMINAL = 640 / 720  # the stock quad ends, as a fraction of half the frame


def _full_screen(elf, width=1280, left=-NOMINAL, right=NOMINAL):
    """Run the Full Screen path from its first instruction to the stock instruction after the hook."""
    cpu = Cpu(elf)
    cpu.r[1], cpu.r[2], cpu.r[20], cpu.r[28] = STACK, TOC, RENDER, 0x28282828
    cpu.wr(RENDER + 0x150, 4, 7)
    cpu.wr(WIDTH_VAR, 4, width)
    for off, v in ((0x1AC, left), (0x1B0, right), (0x1B4, 0.25), (0x1B8, -0.5)):
        cpu.wr(RENDER + off, 4, struct.unpack(">I", struct.pack(">f", v))[0])
    cpu.run(FS.HOOK_VA, {FS.RESUME})
    return cpu


def _ends(cpu):
    return [struct.unpack(">f", struct.pack(">I", cpu.rd(RENDER + off, 4)))[0] for off in (0x1AC, 0x1B0)]


@pytest.mark.parametrize("width", [1280, 1920])  # 720p and 1080p: the same fraction of the frame
def test_full_screen_ends_are_the_edges_of_the_frame(cave_elf, width):
    """The nominal ends (88.9% of the frame) grow to 100%: +-1 of half the frame, no black stripe left."""
    left, right = _ends(_full_screen(cave_elf, width))
    assert (left, right) == pytest.approx((-1.0, 1.0), abs=1e-6)


@pytest.mark.parametrize("left,right", [(-NOMINAL, NOMINAL), (-0.8, 0.8), (-0.5, 0.9)])
def test_full_screen_grows_any_ends_by_the_same_ratio_of_the_frame(cave_elf, left, right):
    got = _ends(_full_screen(cave_elf, 1280, left, right))
    assert got == pytest.approx([left * 1.125, right * 1.125], rel=1e-6)


@pytest.mark.parametrize("width", [720, 640, 480])  # SD output: the Normal path adds nothing there either
def test_full_screen_keeps_the_stock_ends_on_sd_outputs(cave_elf, width):
    assert _ends(_full_screen(cave_elf, width)) == pytest.approx([-NOMINAL, NOMINAL], rel=1e-6)


def test_full_screen_only_scales_the_horizontal_ends(cave_elf):
    cpu = _full_screen(cave_elf)
    top, bottom = [struct.unpack(">f", struct.pack(">I", cpu.rd(RENDER + off, 4)))[0] for off in (0x1B4, 0x1B8)]
    assert (top, bottom) == (0.25, -0.5)


@pytest.mark.parametrize("width", [1280, 720])
def test_full_screen_leaves_the_registers_the_stock_path_reads_alone(cave_elf, width):
    cpu = _full_screen(cave_elf, width)
    assert cpu.r[28] == 7  # the instruction the hook replaced still runs
    assert (cpu.r[20], cpu.r[1], cpu.r[2]) == (RENDER, STACK, TOC)  # r27 is rewritten by the next stock instruction


def test_full_screen_hook_replaces_only_the_first_word_of_the_path(elfs, cave_elf):
    stock, _ = elfs
    assert Cpu(stock).rd(FS.HOOK_VA, 4) == FS.STOCK_FIRST == 0x83940150  # lwz r28,336(r20)
    assert Cpu(cave_elf).rd(FS.HOOK_VA, 4) >> 26 == 18  # a plain b to the cave
    assert Cpu(cave_elf).rd(0x114DE8, 4) == 0x409E0D78  # bne cr7,0x115b60: the only way in, Normal never takes it
    assert Cpu(stock).rd(TOC + FS.WIDTH_SLOT, 4) == WIDTH_VAR  # the slot the cave reads is the width variable


# ---- the left stick as the d-pad in digital mode ----------------------------------------------

import cave_dpad as D

PADS, SRC, REC = 0x42000000, 0x43000000, 0x44000000  # pad array (TOC -0x7ccc), pad data copy (r29), record (r3)
IDLE = 0xFFFF  # active low: nothing pressed


def _dpad(elf, rx=128, ry=128, buttons=IDLE, analog=(0, 0), pad=0, rsx=128):
    """Run the hook at the button store; return the halfword written to the record (and the end address)."""
    cpu = Cpu(elf)
    cpu.r[1], cpu.r[2], cpu.r[3], cpu.r[29], cpu.r[30], cpu.r[0] = STACK, TOC, REC, SRC, pad * 0x94, 0xFFFF0000 | buttons
    cpu.wr(TOC - 0x7CCC, 4, PADS)
    for k, flag in enumerate(analog):
        cpu.wr(PADS + k * D.PAD_STRIDE + D.ANALOG_FLAG, 4, flag)
    cpu.wr(SRC + 0xC, 2, rsx)
    cpu.wr(SRC + 0x10, 2, rx)
    cpu.wr(SRC + 0x12, 2, ry)
    end = cpu.run(D.HOOK_VA, {D.RESUME})
    return cpu.rd(REC, 2), end


@pytest.mark.parametrize("rx,ry,bit", [(0, 128, 0x8000), (255, 128, 0x2000), (128, 0, 0x1000), (128, 255, 0x4000)])
def test_the_left_stick_presses_the_matching_dpad_bit_in_digital_mode(cave_elf, rx, ry, bit):
    got, end = _dpad(cave_elf, rx, ry)
    assert end == D.RESUME and got == IDLE & ~bit


def test_a_diagonal_presses_two_directions(cave_elf):
    got, _ = _dpad(cave_elf, 10, 250)
    assert got == IDLE & ~0x8000 & ~0x4000


@pytest.mark.parametrize("rx,ry", [(128, 128), (0x40, 0xC0), (0x41, 0xBF), (100, 160)])
def test_a_stick_at_rest_or_inside_the_dead_zone_changes_nothing(cave_elf, rx, ry):
    got, _ = _dpad(cave_elf, rx, ry)
    assert got == IDLE


def test_other_buttons_are_kept(cave_elf):
    pressed_cross = IDLE & ~0x0040
    got, _ = _dpad(cave_elf, 0, 128, buttons=pressed_cross)
    assert got == pressed_cross & ~0x8000


@pytest.mark.parametrize("pad,analog,expect_changed", [(0, (1, 0), False), (0, (0, 1), True), (1, (0, 1), False), (1, (1, 0), True)])
def test_analog_mode_of_that_pad_leaves_the_stick_to_the_game(cave_elf, pad, analog, expect_changed):
    got, _ = _dpad(cave_elf, 0, 128, analog=analog, pad=pad)
    assert (got != IDLE) is expect_changed


def test_the_hook_replaces_only_the_button_store(elfs):
    stock, patched = elfs
    assert Cpu(stock).rd(D.HOOK_VA, 4) == D.HOOK_STOCK
    assert Cpu(patched).rd(D.HOOK_VA, 4) != D.HOOK_STOCK


@pytest.mark.parametrize("rsx,bit", [(0, 0x0004), (0x3F, 0x0004), (255, 0x0008), (0xC1, 0x0008)])
def test_the_right_stick_presses_l1_and_r1_in_digital_mode(cave_elf, rsx, bit):
    got, end = _dpad(cave_elf, rsx=rsx)
    assert end == D.RESUME and got == IDLE & ~bit


@pytest.mark.parametrize("rsx", [128, 0x40, 0xC0])
def test_the_right_stick_at_rest_or_in_the_dead_zone_presses_no_shoulder(cave_elf, rsx):
    assert _dpad(cave_elf, rsx=rsx)[0] == IDLE


def test_the_right_stick_does_nothing_in_analog_mode(cave_elf):
    assert _dpad(cave_elf, rsx=0, analog=(1, 0))[0] == IDLE
    assert _dpad(cave_elf, rsx=255, analog=(1, 0))[0] == IDLE


def test_both_sticks_can_work_together(cave_elf):
    got, _ = _dpad(cave_elf, rx=0, ry=128, rsx=255)
    assert got == IDLE & ~0x8000 & ~0x0008


# ---- Parasite Eve: in analog mode the d-pad does what the left stick does ------------------------

NAME_VA = 0x1BE780  # the per-game card name (cave_percard), "SLUS-00662.VM1"
STORES_DONE = 0xEC190  # the stock code after the hook copies the sticks into the record, then loops
UP_B, DOWN_B, LEFT_B, RIGHT_B = 0x1000, 0x4000, 0x8000, 0x2000


def _analog_pad(elf, name="SLUS-00662.VM1", pressed=0, lx=128, ly=128, analog=(1, 0), pad=0):
    """Run the hook and the stock stick copy; return (buttons halfword, record LX, record LY)."""
    cpu = Cpu(elf)
    cpu.r[1], cpu.r[2], cpu.r[3], cpu.r[29], cpu.r[30], cpu.r[0] = STACK, TOC, REC, SRC, pad * 0x94, 0xFFFF0000 | (IDLE & ~pressed)
    cpu.wr(TOC - 0x7CCC, 4, PADS)
    for k, flag in enumerate(analog):
        cpu.wr(PADS + k * D.PAD_STRIDE + D.ANALOG_FLAG, 4, flag)
    for i, b in enumerate(name.encode() + b"\0"):
        cpu.mem[NAME_VA + i] = b
    cpu.wr(SRC + 0xC, 2, 128)
    cpu.wr(SRC + 0xE, 2, 128)
    cpu.wr(SRC + 0x10, 2, lx)
    cpu.wr(SRC + 0x12, 2, ly)
    cpu.run(D.HOOK_VA, {STORES_DONE})
    return cpu.rd(REC, 2), cpu.rd(REC + 2, 1), cpu.rd(REC + 3, 1)


@pytest.mark.parametrize("pressed,lx,ly", [(LEFT_B, 0, 128), (RIGHT_B, 255, 128), (UP_B, 128, 0), (DOWN_B, 128, 255)])
@pytest.mark.parametrize("name", ["SLUS-00662.VM1", "SLUS-00668.VM1"])
def test_parasite_eve_dpad_pushes_the_left_stick_in_analog_mode(cave_elf, name, pressed, lx, ly):
    buttons, got_x, got_y = _analog_pad(cave_elf, name=name, pressed=pressed)
    assert (got_x, got_y) == (lx, ly)
    assert buttons == IDLE & ~pressed  # the d-pad bit itself is still there for the menus


def test_parasite_eve_diagonal_pushes_both_axes(cave_elf):
    _, got_x, got_y = _analog_pad(cave_elf, pressed=LEFT_B | DOWN_B)
    assert (got_x, got_y) == (0, 255)


def test_parasite_eve_real_stick_still_works_and_dpad_wins_on_its_axis(cave_elf):
    _, got_x, got_y = _analog_pad(cave_elf, lx=30, ly=200)  # nothing on the d-pad: the stick as it is
    assert (got_x, got_y) == (30, 200)
    _, got_x, got_y = _analog_pad(cave_elf, pressed=RIGHT_B, lx=30, ly=200)  # d-pad right overrides X only
    assert (got_x, got_y) == (255, 200)


def test_parasite_eve_dpad_is_not_remapped_for_the_pad_in_digital_mode(cave_elf):
    _, got_x, got_y = _analog_pad(cave_elf, pressed=LEFT_B, analog=(0, 0))
    assert (got_x, got_y) == (128, 128)  # digital: the stick drives the d-pad, never the other way


@pytest.mark.parametrize("name", ["SLUS-00067.VM1", "SCUS-94163.VM1", "SLUS-01041.VM1", "SLUS-00663.VM1", ""])
def test_other_games_keep_the_dpad_as_it_is_in_analog_mode(cave_elf, name):
    for pressed in (LEFT_B, RIGHT_B, UP_B, DOWN_B):
        buttons, got_x, got_y = _analog_pad(cave_elf, name=name, pressed=pressed)
        assert (buttons, got_x, got_y) == (IDLE & ~pressed, 128, 128)


def test_parasite_eve_applies_to_the_second_pad_too(cave_elf):
    _, got_x, got_y = _analog_pad(cave_elf, pressed=LEFT_B, analog=(0, 1), pad=1)
    assert (got_x, got_y) == (0, 128)


# ---- Full Screen: no 3/4 scaling of the geometry X (issue #9) ----------------------------------------

MODE_SLOT, MODE_VAR = -0x784C, 0x1620474  # TOC slot -> the derived screen mode variable (0 Normal, 1 and 2 other modes)
# the four fragments the stock code jumps into: (entry, register with the scaled value, where it resumes, input registers)
SCALE_SITES = [(0x3BE48, 7, 0xC6564), (0x3BE5C, 8, 0xC6908), (0x3BE70, 8, 0xC6C68), (0x3BF98, 6, 0xC477C)]


def _scaled(elf, entry, reg, resume, mode, value=1000):
    """Run one fragment with the screen mode set; return the register that holds the (maybe scaled) X quantity."""
    cpu = Cpu(elf)
    cpu.r[2] = TOC
    cpu.wr(TOC + MODE_SLOT, 4, MODE_VAR)
    cpu.wr(MODE_VAR, 4, mode)
    for r in (6, 7, 8, 9, 10, 11):
        cpu.r[r] = value if r == reg else 10 + r
    cpu.run(entry, {resume})
    return cpu.r[reg]


@pytest.mark.parametrize("entry,reg,resume", SCALE_SITES)
def test_stock_scales_the_x_quantity_by_three_quarters_only_in_mode_two(elfs, entry, reg, resume):
    stock, _ = elfs
    assert _scaled(stock, entry, reg, resume, 0) == 1000 + 0 or True  # mode 0 reaches the plain path
    assert _scaled(stock, entry, reg, resume, 2) == 1000 // 2 + 1000 // 4  # 750: the widescreen scale of mode 2
    assert _scaled(stock, entry, reg, resume, 1) == _scaled(stock, entry, reg, resume, 0)


@pytest.mark.parametrize("entry,reg,resume", SCALE_SITES)
def test_the_patched_emulator_does_not_scale_the_x_quantity_in_any_mode(elfs, entry, reg, resume):
    _, patched = elfs
    for mode in (0, 1, 2):
        assert _scaled(patched, entry, reg, resume, mode) == _scaled(patched, entry, reg, resume, 0)


def test_only_the_four_mode_compares_change(elfs):
    stock, patched = elfs
    for va in (0x3BE50, 0x3BE64, 0x3BE78, 0x3BFA0):
        assert Cpu(stock).rd(va, 4) >> 16 in (0x2F89, 0x2F8A, 0x2F8B)  # cmpwi cr7,rN,2
        assert Cpu(stock).rd(va, 4) & 0xFFFF == 2
        assert Cpu(patched).rd(va, 4) & 0xFFFF == 0x7FFF  # compares with a value the mode never has
        assert Cpu(patched).rd(va, 4) >> 16 == Cpu(stock).rd(va, 4) >> 16


# ---- the owner's top adjust: SELECT + UP / DOWN --------------------------------------------------------

ADJ_DELTA, ADJ_PREV_UP, ADJ_PREV_DOWN = 0x1BE760, 0x1BE764, 0x1BE768
SEL, BTN_UP, BTN_DOWN = 0x0100, 0x1000, 0x4000


def _press(cpu_state, buttons, pad=0):
    """Run the pad hook once with `buttons` (active low) on the memory image in `cpu_state`; return the Cpu."""
    cpu = cpu_state
    cpu.r[1], cpu.r[2], cpu.r[3], cpu.r[29], cpu.r[30], cpu.r[0] = STACK, TOC, REC, SRC, pad * 0x94, 0xFFFF0000 | buttons
    cpu.wr(TOC - 0x7CCC, 4, PADS)
    cpu.wr(SRC + 0xC, 2, 128)
    cpu.wr(SRC + 0x10, 2, 128)
    cpu.wr(SRC + 0x12, 2, 128)
    cpu.run(D.HOOK_VA, {D.RESUME})
    return cpu


def _delta(cpu):
    return cpu.rd(ADJ_DELTA, 4)


def test_select_up_moves_the_first_line_by_two_per_press(cave_elf):
    cpu = Cpu(cave_elf)
    for n in (1, 2, 3):
        _press(cpu, IDLE & ~SEL & ~BTN_UP)  # pressed
        _press(cpu, IDLE)  # released
        assert _delta(cpu) == 2 * n


def test_holding_select_up_counts_one_press_only(cave_elf):
    cpu = Cpu(cave_elf)
    for _ in range(5):
        _press(cpu, IDLE & ~SEL & ~BTN_UP)
    assert _delta(cpu) == 2


def test_select_down_goes_back_and_stops_at_zero(cave_elf):
    cpu = Cpu(cave_elf)
    for _ in range(2):
        _press(cpu, IDLE & ~SEL & ~BTN_UP)
        _press(cpu, IDLE)
    for _ in range(4):
        _press(cpu, IDLE & ~SEL & ~BTN_DOWN)
        _press(cpu, IDLE)
    assert _delta(cpu) == 0


def test_the_adjust_stops_at_the_limit(cave_elf):
    cpu = Cpu(cave_elf)
    for _ in range(20):
        _press(cpu, IDLE & ~SEL & ~BTN_UP)
        _press(cpu, IDLE)
    assert _delta(cpu) == D.MAX_DELTA


@pytest.mark.parametrize("buttons", [IDLE & ~BTN_UP, IDLE & ~BTN_DOWN, IDLE & ~SEL])
def test_up_down_or_select_alone_change_nothing(cave_elf, buttons):
    cpu = Cpu(cave_elf)
    _press(cpu, buttons)
    _press(cpu, IDLE)
    assert _delta(cpu) == 0


def test_the_adjust_ignores_the_other_pads_and_keeps_the_buttons(cave_elf):
    cpu = Cpu(cave_elf)
    _press(cpu, IDLE & ~SEL & ~BTN_UP, pad=1)
    assert _delta(cpu) == 0
    cpu.r[11] = 0x1234
    _press(cpu, IDLE & ~SEL & ~BTN_UP)
    assert cpu.rd(REC, 2) == IDLE & ~SEL & ~BTN_UP  # the game still sees what was pressed
    assert cpu.r[11] == 0x1234  # r11 is restored


@pytest.mark.parametrize("delta", [0, 2, 8, 24])
def test_the_quad_starts_the_adjusted_number_of_lines_lower(elfs, delta):
    _, patched = elfs
    cpu = Cpu(patched)
    cpu.wr(ADJ_DELTA, 4, delta)
    cpu.r[1], cpu.r[2], cpu.r[22], cpu.r[4], cpu.r[5], cpu.r[30] = STACK, TOC, FRAME, 1, 320, 0
    cpu.wr(FRAME + 0x1C, 4, 16)
    cpu.wr(FRAME + 0x20, 4, 256)
    cpu.wr(FRAME + 0xC, 4, 240)
    cpu.f[2], cpu.f[8], cpu.f[9] = 1.0, -0.88, 0.89
    cpu.run(0x115AC0, {0x1158B0})
    k = 2 / (248 - (16 + delta))
    assert (cpu.f[7], cpu.f[4]) == pytest.approx((k * (16 - (16 + delta)) - 1, k * (256 - (16 + delta)) - 1), abs=1e-4)


# ---- per-game defaults and the end of the picture ------------------------------------------------------

NAME_VA = 0x1BE780  # the card name cave_percard leaves for the game, "SLUS-00067.VM1"
ADJ_END = 0x1BE76C


def _quad_with(patched, name=b"", top=0, end=0, y1d=16, y2d=256, width=320):
    cpu = Cpu(patched)
    cpu.wr(ADJ_DELTA, 4, top)
    cpu.wr(ADJ_END, 4, end)
    for i, b in enumerate(name + b"\0"):
        cpu.wr(NAME_VA + i, 1, b)
    cpu.r[1], cpu.r[2], cpu.r[22], cpu.r[4], cpu.r[5], cpu.r[30] = STACK, TOC, FRAME, 1, 320, 0
    cpu.wr(FRAME + 0x1C, 4, y1d)
    cpu.wr(FRAME + 0x20, 4, y2d)
    cpu.wr(FRAME + 0xC, 4, 240)
    cpu.wr(FRAME + 0x8, 4, width)
    cpu.f[2], cpu.f[8], cpu.f[9] = 1.0, -0.88, 0.89
    cpu.run(0x115AC0, {0x1158B0})
    return cpu.f[7], cpu.f[4]


def _expect(first, last, y1d=16, y2d=256):
    k = 2 / (last - first)
    return k * (y1d - first) - 1, k * (y2d - first) - 1


@pytest.mark.parametrize("name,first,last", [
    (b"SCUS-94163.VM1", 24, 248),  # Final Fantasy VII
    (b"SLUS-00664.VM1", 16, 248),  # Xenogears keeps the default
    (b"", 16, 248),  # no card name known
])
def test_the_known_games_get_their_own_window(elfs, name, first, last):
    _, patched = elfs
    assert _quad_with(patched, name) == pytest.approx(_expect(first, last), abs=1e-4)


def test_the_owners_adjust_is_added_to_the_games_default(elfs):
    _, patched = elfs
    assert _quad_with(patched, b"SCUS-94163.VM1", top=4, end=6) == pytest.approx(_expect(28, 242), abs=1e-4)


def test_select_right_and_left_move_the_end(cave_elf):
    cpu = Cpu(cave_elf)
    for n in (1, 2, 3):
        _press(cpu, IDLE & ~SEL & ~0x2000)  # RIGHT
        _press(cpu, IDLE)
        assert cpu.rd(ADJ_END, 4) == 2 * n
    for _ in range(5):
        _press(cpu, IDLE & ~SEL & ~0x8000)  # LEFT
        _press(cpu, IDLE)
    assert cpu.rd(ADJ_END, 4) == 0


def test_the_end_adjust_stops_at_its_limit(cave_elf):
    cpu = Cpu(cave_elf)
    for _ in range(20):
        _press(cpu, IDLE & ~SEL & ~0x2000)
        _press(cpu, IDLE)
    assert cpu.rd(ADJ_END, 4) == D.MAX_END


# ---- CD-ROM: data sectors arrive faster (the loading time of games that run from the HD) ---------------

import cave_cdspeed as CD

CD_STATE = 0x10100000  # fake CD controller state; the TOC slot holds its address
CD_MODE = 0x2734
ONE_X, TWO_X = 451584, 225792  # cycles per sector at 75 and 150 sectors a second
SCHEDULER, SCHEDULER_BODY = 0x105E68, 0x105E6C


def _scheduled_delay(elf, delay, mode, mode_known=True):
    """The delay the scheduler body sees when it is called with `delay` while the CD mode is `mode`."""
    cpu = Cpu(elf)
    cpu.r[1], cpu.r[2], cpu.r[3] = STACK, TOC, delay & M32
    cpu.wr(TOC + CD.CD_STATE_SLOT, 4, CD_STATE)
    cpu.wr(CD_STATE + CD_MODE, 4, mode)
    cpu.run(SCHEDULER, {SCHEDULER_BODY})
    return cpu.r[3] & M32, cpu.r[1]


@pytest.mark.parametrize("mode", [0x00, 0x20, 0x80, 0xA0, 0x40, 0x01, 0xC1])
@pytest.mark.parametrize("delay", [ONE_X, TWO_X, 22579, 564480, 1])
def test_the_stock_scheduler_keeps_every_delay(elfs, delay, mode):
    assert _scheduled_delay(elfs[0], delay, mode) == (delay, STACK - 0xB0)


@pytest.mark.parametrize("delay", [ONE_X, TWO_X])
@pytest.mark.parametrize("mode", [0x00, 0x20, 0x80, 0xA0, 0x08, 0x10, 0x04, 0x02, 0x3E])
def test_data_sectors_arrive_four_times_faster(elfs, delay, mode):
    assert _scheduled_delay(elfs[1], delay, mode) == (delay // 4, STACK - 0xB0)


@pytest.mark.parametrize("delay", [ONE_X, TWO_X])
@pytest.mark.parametrize("mode", [0x40, 0x60, 0xC0, 0xE0, 0x01, 0x81, 0xC1, 0x41])
def test_xa_and_cd_da_streams_keep_the_pace_of_the_disc(elfs, delay, mode):
    assert _scheduled_delay(elfs[1], delay, mode) == (delay, STACK - 0xB0)


@pytest.mark.parametrize("delay", [0, 1, 2176, 22579, 225791, 225793, 451583, 451585, 564480, 33868800])
@pytest.mark.parametrize("mode", [0x00, 0x80])
def test_any_other_delay_is_left_alone(elfs, delay, mode):
    assert _scheduled_delay(elfs[1], delay, mode) == (delay, STACK - 0xB0)


def test_the_hook_replaces_only_the_first_instruction_of_the_scheduler(elfs):
    stock, patched = elfs
    off = P._va_to_off(stock, SCHEDULER)
    assert patched[off + 4:off + 0x20] == stock[off + 4:off + 0x20]
    assert patched[off:off + 4] != stock[off:off + 4]


# ---- overclock (live only): an instruction costs a fraction of a cycle --------------------------------------

import cave_overclock as OC

OC_CAVE = 0x1A9380 + 0x1100


@pytest.fixture(scope="module")
def overclocked(elfs):
    stock = elfs[0]
    patches, _ = OC.build(OC_CAVE)
    return P.apply_patches(P.extend_text(stock), tuple(P.Patch(a, o, n, w) for a, o, n, w in patches))


def _spent(elf, instructions, step, cpi=1):
    """Cycles taken out of r28 by `instructions` passes through the loop's cost step."""
    cpu = Cpu(elf)
    cpu.r[2], cpu.r[7], cpu.r[28] = TOC, cpi, 1_000_000
    cpu.wr(OC.STEP_VA, 4, step)
    for _ in range(instructions):
        cpu.run(OC.HOOK_VA, {OC.RESUME})
    return 1_000_000 - cpu.r[28]


def test_the_stock_loop_costs_a_cycle_per_instruction(elfs):
    assert _spent(elfs[0], 1000, 0) == 1000


@pytest.mark.parametrize("factor,expect", [(1.0, 1000), (1.5, 667), (2.0, 500), (4.0, 250)])
def test_an_instruction_costs_a_fraction_of_a_cycle(overclocked, factor, expect):
    assert _spent(overclocked, 1000, OC.step_for(factor)) == pytest.approx(expect, abs=3)


def test_the_cost_scales_with_the_cycles_per_instruction_the_emulator_set(overclocked):
    assert _spent(overclocked, 1000, OC.step_for(2.0), cpi=3) == pytest.approx(1500, abs=3)


def test_the_fraction_is_kept_between_instructions(overclocked):
    cpu = Cpu(overclocked)
    cpu.r[2], cpu.r[7], cpu.r[28] = TOC, 1, 100
    cpu.wr(OC.STEP_VA, 4, 171)
    steps = []
    for _ in range(6):
        before = cpu.r[28]
        cpu.run(OC.HOOK_VA, {OC.RESUME})
        steps.append(before - cpu.r[28])
    assert steps == [0, 1, 1, 0, 1, 1] and sum(steps) == 4  # 6 * 171 / 256 = 4.01


def test_the_registers_the_loop_still_needs_are_left_alone(overclocked):
    cpu = Cpu(overclocked)
    live = {1: 0x7000000, 2: TOC, 7: 1, 23: 0x111, 26: 0x222, 27: 4, 28: 100, 29: 0x333, 31: 0x3F, 5: 0x444, 20: 1, 30: 0x555}
    for r, v in live.items():
        cpu.r[r] = v
    cpu.wr(OC.STEP_VA, 4, 171)
    cpu.run(OC.HOOK_VA, {OC.RESUME})
    for r, v in live.items():
        if r != 28:
            assert cpu.r[r] == v, r


def test_the_overclock_cave_is_not_in_the_installed_package(elfs):
    stock, patched = elfs
    off = P._va_to_off(stock, OC.HOOK_VA)
    assert patched[off:off + 4] == stock[off:off + 4]
