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
        for _ in range(60000):
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
                elif op == 63 and xo5 == 23:  # fsel
                    self.f[rt] = self.f[fc] if self.f[fa] >= 0 else self.f[fb]
                elif op == 63 and xo10 == 0:  # fcmpu
                    a, b = self.f[fa], self.f[fb]
                    self.cr[(w >> 23) & 7] = (int(a < b), int(a > b), int(a == b))
                elif op == 63 and xo10 == 846:  # fcfid
                    self.f[rt] = float(_s64(struct.unpack(">Q", struct.pack(">d", self.f[fb]))[0]))
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
                if xo == 266:  # add
                    self.r[rt] = (self.r[ra] + self.r[rb]) & M64
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
                target = pc + (li - (1 << 26) if li & (1 << 25) else li)
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
    assert [k for k, _ in log].count("open") == [k for k, _ in log].count("close") == 2


# ---- content-aware overscan cave --------------------------------------------------------------

import cave_overscan as O

OBJ, OUT, STATE = 0x40000000, 0x30000000, 0x31000000
SOTN = {"x1": -0.8694444, "x2": 0.9013889, "y1": -1.0714286, "y2": 1.0714286}


def _e3(x, y):
    return 0xE3000000 | (y << 10) | x


def _e4(x, y):
    return 0xE4000000 | (y << 10) | x


def _reference(q, a, b, h):
    tw0, tw1 = (-1 - q["y1"]) / (q["y2"] - q["y1"]), (1 - q["y1"]) / (q["y2"] - q["y1"])
    t0, t1 = max(a / h, tw0), min(b / h, tw1)
    ln = t1 - t0
    s = (tw1 - tw0) / ln
    zx = (s + 1) / 2
    return {"x1": q["x1"] * zx, "x2": q["x2"] * zx, "y1": -1 - 2 * t0 / ln, "y2": -1 + 2 * (1 - t0) / ln}


def _frames(elf, frames, q=SOTN, y1d=16, y2d=256, mem=None):
    """Run the hook once per entry of `frames` (each a list of packet-buffer words), sharing memory so the
    remembered window carries over, and return the quad after each call."""
    mem = {} if mem is None else mem
    out = []
    for words in frames:
        cpu = Cpu(elf)
        cpu.mem = mem
        cpu.r[1], cpu.r[2], cpu.r[20], cpu.r[22] = STACK, TOC, OUT, STATE
        cpu.wr(STATE + 0x1C, 4, y1d)
        cpu.wr(STATE + 0x20, 4, y2d)
        cpu.wr(TOC - 0x77F8, 4, OBJ)
        for k in range(0x17A0 // 4):  # the buffer holds only this frame's words
            cpu.wr(OBJ + 0x60 + 4 * k, 4, words[k] if k < len(words) else 0)
        cpu.f[6], cpu.f[5], cpu.f[7], cpu.f[4] = (
            struct.unpack(">f", struct.pack(">f", v))[0] for v in (q["x1"], q["x2"], q["y1"], q["y2"]))
        sent_f = {i: 100.5 + i for i in (0, 1, 2, 3, 8, 9, 10, 13)}
        sent_r = {i: 0x5000 + i for i in (0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)}
        for i, v in sent_f.items():
            cpu.f[i] = v
        for i, v in sent_r.items():
            cpu.r[i] = v
        end = cpu.run(O.HOOK_VA, {O.RESUME})
        assert end == O.RESUME and cpu.r[1] == STACK
        assert all(cpu.f[i] == v for i, v in sent_f.items()) and all(cpu.r[i] == v for i, v in sent_r.items())
        assert cpu.rd(OUT + 0x1AC, 4) == struct.unpack(">I", struct.pack(">f", cpu.f[6]))[0]  # displaced store ran
        out.append({"x1": cpu.f[6], "x2": cpu.f[5], "y1": cpu.f[7], "y2": cpu.f[4]})
    return out


def _overscan(elf, words, q=SOTN, y1d=16, y2d=256):
    """The quad after the window has settled (O.HOLD identical frames)."""
    return _frames(elf, [words] * O.HOLD, q=q, y1d=y1d, y2d=y2d)[-1]


def _same(a, b, tol=1e-4):
    return all(abs(a[k] - b[k]) < tol for k in a)


def test_castlevania_lines_20_to_226_fill_the_window(cave_elf):
    words = [_e3(0, 20), _e4(255, 226), 0x2C808080, _e3(256, 20), _e4(511, 226), _e3(0, 0), _e4(1023, 1023)]
    got = _overscan(cave_elf, words)
    assert _same(got, _reference(SOTN, 20, 227, 240))
    assert got["y1"] < SOTN["y1"] and got["y2"] > SOTN["y2"]  # the picture grows past the screen on both sides


def test_a_game_that_fills_its_frame_is_left_alone(cave_elf):
    assert _same(_overscan(cave_elf, [_e3(0, 0), _e4(255, 239)]), SOTN)


@pytest.mark.parametrize("words", [[], [_e3(0, 0), _e4(1023, 1023)], [_e3(0, 20)], [_e4(255, 226)], [0x2C808080] * 8])
def test_no_usable_draw_area_changes_nothing(cave_elf, words):
    assert _same(_overscan(cave_elf, words), SOTN)


@pytest.mark.parametrize("y1d,y2d", [(20, 308), (0, 480), (16, 100)])
def test_heights_outside_ntsc_240_are_left_alone(cave_elf, y1d, y2d):
    assert _same(_overscan(cave_elf, [_e3(0, 20), _e4(255, 226)], y1d=y1d, y2d=y2d), SOTN)


def test_a_top_in_the_lower_half_is_ignored(cave_elf):
    assert _same(_overscan(cave_elf, [_e3(0, 150), _e4(255, 250)]), SOTN)


def test_the_tallest_area_wins_and_the_bottom_is_clamped(cave_elf):
    words = [_e3(0, 100), _e4(255, 219), _e3(0, 10), _e4(255, 250)]
    assert _same(_overscan(cave_elf, words), _reference(SOTN, 10, 240, 240))


def test_a_degenerate_quad_is_left_alone(cave_elf):
    q = {"x1": -0.9, "x2": 0.9, "y1": 0.0, "y2": 0.1}
    assert _same(_overscan(cave_elf, [_e3(0, 20), _e4(255, 226)], q=q), q)


def test_the_stock_overscan_window_is_kept_when_the_game_draws_more(cave_elf):
    got = _overscan(cave_elf, [_e3(0, 2), _e4(255, 237)])  # rows 2..238: wider than the 8..232 window
    assert _same(got, SOTN)


def test_the_window_shrinks_only_after_hold_frames_in_a_row(cave_elf):
    words = [_e3(0, 20), _e4(255, 226)]
    out = _frames(cave_elf, [words] * (O.HOLD + 3))
    assert all(_same(q, SOTN) for q in out[:O.HOLD - 1])  # still the stock picture
    assert all(_same(q, _reference(SOTN, 20, 227, 240)) for q in out[O.HOLD - 1:])  # then it stays cropped


def test_a_scene_that_alternates_areas_never_flickers(cave_elf):
    full, game = [_e3(0, 0), _e4(255, 239)], [_e3(0, 20), _e4(255, 226)]
    out = _frames(cave_elf, [full, game] * 40)
    assert all(_same(q, SOTN) for q in out)


def test_the_window_in_use_stays_when_a_frame_has_no_pair(cave_elf):
    game = [_e3(0, 20), _e4(255, 226)]
    out = _frames(cave_elf, [game] * O.HOLD + [[], [_e3(0, 0), _e4(1023, 1023)], []] + [game] * 2)
    cropped = _reference(SOTN, 20, 227, 240)
    assert all(_same(q, cropped) for q in out[O.HOLD - 1:])


def test_a_larger_area_restores_the_picture_at_once(cave_elf):
    game, full = [_e3(0, 20), _e4(255, 226)], [_e3(0, 0), _e4(255, 239)]
    out = _frames(cave_elf, [game] * O.HOLD + [full])
    assert _same(out[O.HOLD - 1], _reference(SOTN, 20, 227, 240)) and _same(out[-1], SOTN)


def test_a_different_smaller_area_needs_its_own_hold(cave_elf):
    a, b = [_e3(0, 20), _e4(255, 226)], [_e3(0, 40), _e4(255, 200)]
    out = _frames(cave_elf, [a] * O.HOLD + [b] * (O.HOLD - 1) + [b])
    assert _same(out[O.HOLD + O.HOLD - 2], _reference(SOTN, 20, 227, 240))  # one short: still window a
    assert _same(out[-1], _reference(SOTN, 40, 201, 240))
