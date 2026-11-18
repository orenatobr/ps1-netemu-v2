"""Patch the RUNNING ps1_netemu through webMAN PS3MAPI (no file is modified).

Start a PS1 game first (with the webMAN netemu option the process is named
ps1_emu.self but runs ps1_netemu code), then:

    python ps3mapi_patch.py 192.168.1.85            # apply
    python ps3mapi_patch.py 192.168.1.85 --revert   # restore
    python ps3mapi_patch.py 192.168.1.85 --watch    # keep running; patch every game you start

What it does:
  * vertical "overscan" (NTSC): the picture is shown in a window of 240 video lines
    starting at line 16, so a game that uses all of them loses none (a 224 line window
    cut 8 lines at the top and the bottom of Megaman X5). When a game starts its display
    AFTER line 16 the window is anchored to the game's own start, so no black bar is
    left at the top. 4:3 width is untouched. PAL keeps the stock behavior.

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

OVERSCAN_FIRST, OVERSCAN_LAST = 24, 248  # DuckStation's fixed window (gpu.cpp), see cave_overscan.py


def f32(x: float) -> int:
    return struct.unpack(">I", struct.pack(">f", x))[0]


# (address, stock word, patched word, what). Order matters only loosely; the sites
# are rewritten between two frames and each frame is consistent once all are done.
CODE = (
    # vibration: the actuator function (0xeb198) used to send force only through a chain of conditions (the
    # internal block flag at X+0x84 and a strength curve built from settings 50 and 51). The motors now get
    # exactly what the game commands: small motor = r28 (on/off), large motor = r30 (0..255).
    (0x0EB2D8, 0x801B0084, 0x7F8AE378, "mr r10,r28: small motor as the game asked"),
    (0x0EB2DC, 0x39600000, 0x7FCBF378, "mr r11,r30: large motor as the game asked"),
    (0x0EB2E0, 0x2F800000, 0x48000010, "b 0xeb2f0: skip the block flag and the strength curve"),
)
# TOC padding slots (zeroed, never referenced by code) used for float constants
CONSTANTS = (
    (0x1BE750, f32(OVERSCAN_FIRST)),
    (0x1BE754, f32(OVERSCAN_LAST)),
    (0x1BE758, f32(2.0)),
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
