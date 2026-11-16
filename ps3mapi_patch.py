"""Patch the RUNNING ps1_netemu through webMAN PS3MAPI (no file is modified).

Start a PS1 game first (with the webMAN netemu option the process is named
ps1_emu.self but runs ps1_netemu code), then:

    python ps3mapi_patch.py 192.168.1.85            # apply
    python ps3mapi_patch.py 192.168.1.85 --revert   # restore

What it does:
  * analog mode on for the 8 pads;
  * vertical "overscan" like DuckStation: the picture is placed in a fixed NTSC
    window of video lines 24..248 instead of fitting the game's own display
    range, so the black bars above/below disappear. 4:3 width is untouched.
    NTSC only; PAL keeps the stock behavior.

Every code site is read first; nothing is written unless the stock words match.
"""
from __future__ import annotations

import argparse
import logging
import re
import struct
import urllib.request

logger = logging.getLogger(__name__)

NTSC_STRETCH = 240 / 224  # nominal NTSC lines / DuckStation overscan window lines (24..248)
OVERSCAN_START = 24


def f32(x: float) -> int:
    return struct.unpack(">I", struct.pack(">f", x))[0]


# (address, stock word, patched word, what)
CODE = (
    (0x115808, 0x79200020, 0xC0C287F8, "PAL: f6 = 1.0 (no scaling)"),
    (0x115810, 0xF80100C0, 0xF92100C0, "PAL: std r9 (r0 no longer needed)"),
    (0x115AC4, 0xC1A28894, 0xC1A2AA24, "NTSC: c1 = 24*s from free slot"),
    (0x115AC8, 0x79200020, 0xC0C2AA20, "NTSC: f6 = s"),
    (0x115AD0, 0xF80100C0, 0xF92100C0, "NTSC: std r9"),
    (0x115824, 0xED806828, 0xED8069B8, "top    = s * (Y1 - c1)"),
    (0x115874, 0xEDAC082A, 0xEDA161BA, "bottom = top + s * rows"),
)
# TOC padding slots (zeroed, never referenced by code) used for float constants
CONSTANTS = (
    (0x1BE750, f32(NTSC_STRETCH)),
    (0x1BE754, f32(OVERSCAN_START * NTSC_STRETCH)),
)
PAD_FLAG_BASE, PAD_STRIDE, PAD_COUNT = 0x2E7CD8, 0xD8, 8


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
    for k in range(PAD_COUNT):
        write_word(host, pid, PAD_FLAG_BASE + k * PAD_STRIDE, 0 if revert else 1)
    logger.info("analog flag of %d pads = %d", PAD_COUNT, 0 if revert else 1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("host")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    apply(args.host, args.revert)
