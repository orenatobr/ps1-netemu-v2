"""Apply the ps1_netemu v2 patches to the RUNNING emulator through webMAN PS3MAPI.

No file on the console is modified: the patches live in memory until the game
is closed. Start the PS1 game first, then run:

    python ps3mapi_patch.py 192.168.1.85            # apply
    python ps3mapi_patch.py 192.168.1.85 --revert   # restore original words

Every site is read first and the script refuses to write if the bytes are not
the expected stock ps1_netemu ones (so it never patches another emulator).
"""
from __future__ import annotations

import argparse
import logging
import re
import urllib.request

from ps1_netemu_patch import PATCHES

logger = logging.getLogger(__name__)

PAD_FLAG_BASE = 0x2E7CD8  # pad 0 analog flag; stride 0xd8, 8 pads
PAD_STRIDE, PAD_COUNT = 0xD8, 8
ANALOG_PATCHES = {0xD3080, 0xD3288, 0xD4EAC}  # init-time; pad flags are written directly


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
    words = re.findall(r"\b[0-9A-Fa-f]{8}\b", re.sub(r"<[^>]*>", " ", page))
    return int(words[-1], 16)


def write_word(host: str, pid: str, addr: int, value: int) -> None:
    _get(host, f"setmem.ps3mapi?proc={pid}&addr={addr:x}&val={value:08X}")
    if read_word(host, pid, addr) != value:
        raise RuntimeError(f"write to 0x{addr:x} did not stick")


def apply(host: str, revert: bool = False) -> None:
    pid = find_process(host)
    logger.info("emulator process %s", pid)
    sites = [(p.va, p.new if revert else p.old, p.old if revert else p.new)
             for p in PATCHES if p.va not in ANALOG_PATCHES]
    for va, expected, _ in sites:
        found = read_word(host, pid, va)
        if found != expected:
            raise RuntimeError(f"0x{va:x}: expected {expected:08x}, found {found:08x}; not patching")
    for va, _, new in sites:
        write_word(host, pid, va, new)
        logger.info("0x%x <- %08x", va, new)
    flag = 0 if revert else 1
    for k in range(PAD_COUNT):
        write_word(host, pid, PAD_FLAG_BASE + k * PAD_STRIDE, flag)
    logger.info("analog flag of %d pads = %d", PAD_COUNT, flag)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("host")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    apply(args.host, args.revert)
