"""Patch the RUNNING ps1_netemu through webMAN PS3MAPI (no file is modified).

Start a PS1 game first (with the webMAN netemu option the process is named
ps1_emu.self but runs ps1_netemu code), then:

    python ps3mapi_patch.py 192.168.1.85            # apply
    python ps3mapi_patch.py 192.168.1.85 --revert   # restore
    python ps3mapi_patch.py 192.168.1.85 --watch    # keep running; patch every game you start
    python ps3mapi_patch.py 192.168.1.85 --overclock 1.5          # emulated CPU 1.5x faster (live only)
    python ps3mapi_patch.py 192.168.1.85 --overclock 1.5 --revert

What it does:
  * vertical "overscan" (NTSC): the picture is shown in a window of 240 video lines
    starting at line 16, so a game that uses all of them loses none (a 224 line window
    cut 8 lines at the top and the bottom of Megaman X5). When a game starts its display
    AFTER line 16 the window is anchored to the game's own start, so no black bar is
    left at the top. The picture is the same width in every game (the declared horizontal range no longer
    changes it). PAL keeps the stock behavior.

Every code site is read first; nothing is written unless the stock words match.
"""
from __future__ import annotations

import argparse
import logging
import re
import struct
import time
import urllib.request

import cave_overclock

logger = logging.getLogger(__name__)

NTSC_STRETCH = 1.0  # only the stock NTSC prologue still loads it; the quad comes from cave_overscan.py
OVERSCAN_START, OVERSCAN_END = 16, 248  # the video lines the quad may show: 16 to 248 (the last 8 are overscan)
PAL_STRETCH = 1.0  # PAL keeps the stock 1:1 window


def f32(x: float) -> int:
    return struct.unpack(">I", struct.pack(">f", x))[0]


# (address, stock word, patched word, what). Order matters only loosely; the sites
# are rewritten between two frames and each frame is consistent once all are done.
CODE = (
    # PAL block: f6 = 2*PAL_STRETCH, f0 = Y1, f7 = 0.0
    (0x115808, 0x79200020, 0xC0C2AA28, "PAL: f6 = 2 (no scaling)"),
    (0x115810, 0xF80100C0, 0xF92100C0, "PAL: std r9"),
    (0x115814, 0xC8E100C0, 0xC80100C0, "PAL: lfd f0"),
    (0x115818, 0xFCE03E9C, 0xFC00069C, "PAL: fcfid f0"),
    (0x11581C, 0xFC003818, 0xC0E287FC, "PAL: f7 = 0.0"),
    # NTSC block: f13 = first line of the window, f6 = 2*s, f0 = Y1, f7 = 0.0
    (0x115AC4, 0xC1A28894, 0xC1A2AA24, "NTSC: f13 = first line (free slot)"),
    (0x115AC8, 0x79200020, 0xC0C2AA20, "NTSC: f6 = 2*s (free slot)"),
    (0x115AD0, 0xF80100C0, 0xF92100C0, "NTSC: std r9"),
    (0x115AD4, 0xC86100C0, 0xC80100C0, "NTSC: lfd f0"),
    (0x115AD8, 0xFC601E9C, 0xFC00069C, "NTSC: fcfid f0"),
    (0x115ADC, 0xFC001818, 0xC0E287FC, "NTSC: f7 = 0.0"),
    # shared tail: the doubling of both quad ends becomes a multiplication by f6 (2 * stretch)
    (0x115894, 0xEC00002A, 0xEC0001B2, "fmuls f0,f0,f6: y1 = 2*s*(Y1-c)/H"),
    (0x1158A0, 0xEDAD682A, 0xEDAD01B2, "fmuls f13,f13,f6: y2 likewise"),
    # width: the stock quad ends came from the horizontal range each game declares (GP1), so a game that declares
    # a narrower range (Final Fantasy VII) was drawn narrower than one that declares the nominal range (Megaman X5).
    # Every game now gets the same ends, +-f10/f11 (640/720 of the 4:3 window, also in the stock 384 mode that used 672/720).
    (0x1158EC, 0xECAD5824, 0xECAA5824, "fdivs f5,f10,f11: right end = nominal, not the declared range"),
    (0x1158F4, 0xECC05824, 0xFCC02850, "fneg f6,f5: left end = the mirror of it"),
    (0x115B48, 0xC1428828, 0xC142881C, "lfs f10,-0x77e4(r2): 640 here too, the stock 384 mode used 672"),
    # vibration: the actuator function (0xeb198) used to send force only through a chain of conditions (the
    # internal block flag at X+0x84 and a strength curve built from settings 50 and 51). The motors now get
    # exactly what the game commands: small motor = r28 (on/off), large motor = r30 (0..255).
    (0x0EB2D8, 0x801B0084, 0x38000000, "li r0,0"),
    (0x0EB2DC, 0x39600000, 0x901B0084, "stw r0,0x84(r27): the emulator's vibration switch stays on"),
    (0x0EB2E0, 0x2F800000, 0x7F8AE378, "mr r10,r28: small motor as the game asked"),
    (0x0EB2E4, 0x409E000C, 0x7FCBF378, "mr r11,r30: large motor as the game asked"),
    (0x0EB2E8, 0x7D2B4B78, 0x48000008, "b 0xeb2f0: skip the block flag and the strength curve"),
    (0x0EC000, 0x38000001, 0x38000000, "li r0,0: the pad thread no longer switches vibration off"),
    # Full Screen (issue #9): in the emulator's mode 2 four places of the primitive set-up (entered from 0xc6560, 0xc6904,
    # 0xc6c64 and 0xc4778) replace an X quantity of the geometry by x/2 + x/4, three quarters of it. The 3D vertices
    # shrink in X while the pre-rendered background, a texture of the same picture, is stretched by the quad, so a
    # character lands at a different place than it should (the picture is not scaled the same way). Comparing the
    # mode with a value it never has sends every mode down the unscaled path, and the quad scales the whole picture.
    (0x03BE50, 0x2F890002, 0x2F897FFF, "cmpwi cr7,r9,0x7fff: no 3/4 scaling of the X in mode 2 (site 0xc6560)"),
    (0x03BE64, 0x2F8A0002, 0x2F8A7FFF, "cmpwi cr7,r10,0x7fff: no 3/4 scaling of the X in mode 2 (site 0xc6904)"),
    (0x03BE78, 0x2F8B0002, 0x2F8B7FFF, "cmpwi cr7,r11,0x7fff: no 3/4 scaling of the X in mode 2 (site 0xc6c64)"),
    (0x03BFA0, 0x2F890002, 0x2F897FFF, "cmpwi cr7,r9,0x7fff: no 3/4 scaling of the X in mode 2 (site 0xc4778)"),
)
# TOC padding slots (zeroed, never referenced by code) used for float constants
CONSTANTS = (
    (0x1BE750, f32(2 * NTSC_STRETCH)),
    (0x1BE754, f32(OVERSCAN_START)),
    (0x1BE758, f32(2 * PAL_STRETCH)),
    (0x1BE75C, f32(OVERSCAN_END)),  # the end of the window, for cave_overscan.py
)



def _get(host: str, path: str) -> str:
    with urllib.request.urlopen(f"http://{host}/{path}", timeout=10) as resp:
        return resp.read().decode("latin1")


def find_process(host: str) -> str:
    """Return the PS3MAPI process id (e.g. '0x1060200') of the running PS1 emulator."""
    page = _get(host, "gameplugin.ps3mapi")
    for pid, name in re.findall(r'option value="(0x[0-9a-fA-F]+)"/>[0-9a-fA-F]*_?([^<]*)', page):
        if "ps1" in name.lower() and "emu" in name.lower():
            return pid
    raise RuntimeError("no running PS1 emulator process found; start the game first")


def read_word(host: str, pid: str, addr: int) -> int:
    page = _get(host, f"getmem.ps3mapi?proc={pid}&addr={addr:x}&len=4")
    return int(re.findall(r"\b[0-9A-Fa-f]{8}\b", re.sub(r"<[^>]*>", " ", page))[-1], 16)


def write_word(host: str, pid: str, addr: int, value: int) -> None:
    _get(host, f"setmem.ps3mapi?proc={pid}&addr={addr:x}&val={value:08X}")
    if read_word(host, pid, addr) != value:
        raise RuntimeError(f"write to 0x{addr:x} did not stick")


def apply(host: str, revert: bool = False) -> None:
    pid = find_process(host)
    logger.info("emulator process %s", pid)
    sites = [(a, new if revert else old, old if revert else new, why) for a, old, new, why in CODE]
    for addr, expect, _, _ in sites:
        found = read_word(host, pid, addr)
        if found != expect:
            raise RuntimeError(f"0x{addr:x}: expected {expect:08x}, found {found:08x}; not patching")
    for addr, value in CONSTANTS:  # constants first, so the new code never reads garbage
        write_word(host, pid, addr, 0 if revert else value)
    for addr, _, new, why in sites:
        write_word(host, pid, addr, new)
        logger.info("0x%x <- %08x  %s", addr, new, why)


OVERCLOCK_CAVE = 0x1A9380 + 0x1100  # in the text segment extension of the running emulator, after the CD speedup cave
PAUSE = 0.8  # seconds between two requests: bursts make webMAN drop out


def overclock(host: str, factor: float, revert: bool = False) -> None:
    """Make the emulated R3000 `factor` times faster in the running emulator (cave_overclock.py), or undo it."""
    pid = find_process(host)
    patches, _ = cave_overclock.build(OVERCLOCK_CAVE)
    hook = next(p for p in patches if p[0] == cave_overclock.HOOK_VA)
    found = read_word(host, pid, hook[0])
    if revert:
        write_word(host, pid, hook[0], hook[1])
        logger.info("overclock removed")
        return
    if found != hook[1]:
        raise RuntimeError(f"0x{hook[0]:x}: expected {hook[1]:08x}, found {found:08x}; not patching")
    for addr, _, new, _ in (p for p in patches if p[0] != hook[0]):
        time.sleep(PAUSE)
        write_word(host, pid, addr, new)
    time.sleep(PAUSE)
    write_word(host, pid, cave_overclock.STEP_VA, cave_overclock.step_for(factor))  # the cost, before the hook is live
    time.sleep(PAUSE)
    write_word(host, pid, hook[0], hook[2])
    logger.info("overclock %.2fx on (step %d/256 cycle per instruction)", factor, cave_overclock.step_for(factor))


def watch(host: str, interval: float = 2.0) -> None:
    """Patch each PS1 game as soon as it starts (a fresh process has the stock words)."""
    logger.info("watching %s; start a PS1 game (Ctrl-C to stop)", host)
    stock = CODE[-1][1]  # stock word of the last patch site
    while True:
        try:
            pid = find_process(host)
            if read_word(host, pid, CODE[-1][0]) == stock:
                time.sleep(3)  # let the emulator finish initializing its pads
                apply(host)
        except (RuntimeError, OSError):
            pass  # no emulator running yet, or the console is busy
        time.sleep(interval)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("host")
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--watch", action="store_true", help="patch every game that starts")
    ap.add_argument("--overclock", type=float, metavar="FACTOR", help="run the emulated CPU FACTOR (1.0 to 4.0) times faster, live only")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.overclock:
        overclock(args.host, args.overclock, args.revert)
    elif args.watch:
        watch(args.host)
    else:
        apply(args.host, args.revert)
