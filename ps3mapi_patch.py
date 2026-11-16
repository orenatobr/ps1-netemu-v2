"""Patch the RUNNING ps1_netemu through webMAN PS3MAPI (no file is modified).

Start a PS1 game first (with the webMAN netemu option the process is named
ps1_emu.self but runs ps1_netemu code), then:

    python ps3mapi_patch.py 192.168.1.85            # apply
    python ps3mapi_patch.py 192.168.1.85 --revert   # restore
    python ps3mapi_patch.py 192.168.1.85 --watch    # keep running; patch every game you start

What it does:
  * vertical "overscan" like DuckStation (NTSC): the picture is shown in a window
    of 224 video lines starting at line 24, cropping what falls outside. Unlike a
    fixed window, when a game starts its display AFTER line 24 the window is
    anchored to the game's own start, so no black bar is left at the top.
    4:3 width is untouched. PAL keeps the stock behavior.

Every code site is read first; nothing is written unless the stock words match.
"""
from __future__ import annotations

import argparse
import logging
import re
import struct
import time
import urllib.request

logger = logging.getLogger(__name__)

NTSC_STRETCH = 240 / 224  # nominal NTSC lines / DuckStation overscan window lines (24..248)
OVERSCAN_START = 24  # first line of the DuckStation NTSC overscan window


def f32(x: float) -> int:
    return struct.unpack(">I", struct.pack(">f", x))[0]


# (address, stock word, patched word, what). Order matters only loosely; the sites
# are rewritten between two frames and each frame is consistent once all are done.
CODE = (
    # PAL block: f6 = 1.0, f0 = Y1, f7 = 0.0 (PAL is anchored like NTSC, without the 224-line window)
    (0x115808, 0x79200020, 0xC0C287F8, "PAL: f6 = 1.0 (no scaling)"),
    (0x115810, 0xF80100C0, 0xF92100C0, "PAL: std r9"),
    (0x115814, 0xC8E100C0, 0xC80100C0, "PAL: lfd f0"),
    (0x115818, 0xFCE03E9C, 0xFC00069C, "PAL: fcfid f0"),
    (0x11581C, 0xFC003818, 0xC0E287FC, "PAL: f7 = 0.0"),
    # NTSC block: f13 = 24*s, f6 = s, f0 = Y1, f7 = 0.0
    (0x115AC4, 0xC1A28894, 0xC1A2AA24, "NTSC: f13 = 24*s (free slot)"),
    (0x115AC8, 0x79200020, 0xC0C2AA20, "NTSC: f6 = s (free slot)"),
    (0x115AD0, 0xF80100C0, 0xF92100C0, "NTSC: std r9"),
    (0x115AD4, 0xC86100C0, 0xC80100C0, "NTSC: lfd f0"),
    (0x115AD8, 0xFC601E9C, 0xFC00069C, "NTSC: fcfid f0"),
    (0x115ADC, 0xFC001818, 0xC0E287FC, "NTSC: f7 = 0.0"),
    # shared: top = s*(Y1-24); clamp to <= 0 (anchor to the game's start); bottom = top + s*rows
    (0x115824, 0xED806828, 0xED8069B8, "top    = s * (Y1 - c1)"),
    (0x115860, 0xFD605E9C, 0xFD405E9C, "f10 = frame height, converted directly"),
    (0x115864, 0xFD405818, 0x60000000, "nop (the frsp was redundant)"),
    (0x11586C, 0x419A0008, 0xEDA151B8, "f13 = s*rows - height (r30 is always 0 here: branch was always taken)"),
    (0x115870, 0xED8C602A, 0xFD8C61EE, "top    = min(top, 0)"),
    (0x115874, 0xEDAC082A, 0xEDAD602A, "f13 = (top + s*rows) - height"),
    (0x115890, 0xFC601E9C, 0xFD801E9C, "f12 = width, converted directly"),
    (0x11589C, 0xFD801818, 0xFDAD3B6E, "bottom overshoot = max(overshoot, 0): fill the screen"),
    (0x1158AC, 0xEC8D1028, 0xEC8D102A, "y1 = 1 + overshoot"),
)
# TOC padding slots (zeroed, never referenced by code) used for float constants
CONSTANTS = (
    (0x1BE750, f32(NTSC_STRETCH)),
    (0x1BE754, f32(OVERSCAN_START * NTSC_STRETCH)),
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
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.watch:
        watch(args.host)
    else:
        apply(args.host, args.revert)
