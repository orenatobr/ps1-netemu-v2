"""Read, from the running emulator, what the overscan code works with: the picture quad, the remembered
window and every draw area (GP0 E3/E4 pair) sitting in the GPU packet buffer.

    python tools/probe_overscan.py 192.168.1.85

Start a PS1 game first. Reads are 256 bytes each with a short pause (webMAN drops a burst of requests).
"""
from __future__ import annotations

import logging
import re
import struct
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ps3mapi_patch import find_process

logger = logging.getLogger("probe_overscan")
TOC = 0x1C3D30  # r2 of the emulator
OBJ_SLOT, GEOM_SLOT = TOC - 0x77F8, TOC - 0x7834  # TOC slots: the GPU packet object, the geometry record
STATE_VA = 0x1BE75C  # window a, b, candidate a, b, count (cave_overscan)
BUF_OFF, BUF_LEN = 0x60, 0x17A0


def _read(host: str, pid: str, addr: int, n: int) -> bytes:
    """`n` bytes (at most 256) at `addr`."""
    with urllib.request.urlopen(f"http://{host}/getmem.ps3mapi?proc={pid}&addr={addr:x}&len={n:x}", timeout=10) as r:
        page = r.read().decode("latin1")
    hexes = re.findall(r"\b[0-9A-Fa-f]{2}\b", re.sub(r"<[^>]*>", " ", page.split("</form>")[-1]))
    return bytes(int(h, 16) for h in hexes[-n:])


def read_block(host: str, pid: str, addr: int, n: int) -> bytes:
    out = b""
    while len(out) < n:
        out += _read(host, pid, addr + len(out), min(256, n - len(out)))
        time.sleep(0.15)
    return out


def draw_areas(words: list[int]) -> list[tuple[int, int, int, int]]:
    """(x1, y1, x2, y2) of every E3 followed by an E4 in the words."""
    out, top = [], None
    for w in words:
        cmd = w >> 24
        if cmd == 0xE3:
            top = ((w & 0x3FF), (w >> 10) & 0x3FF)
        elif cmd == 0xE4 and top:
            out.append((top[0], top[1], w & 0x3FF, (w >> 10) & 0x3FF))
            top = None
    return out


def probe(host: str) -> None:
    pid = find_process(host)
    obj = struct.unpack(">I", read_block(host, pid, OBJ_SLOT, 4))[0]
    geom = struct.unpack(">I", read_block(host, pid, GEOM_SLOT, 4))[0]
    quad = struct.unpack(">5f", read_block(host, pid, geom + 0x1AC, 20))
    state = struct.unpack(">5I", read_block(host, pid, STATE_VA, 20))
    words = list(struct.unpack(f">{BUF_LEN // 4}I", read_block(host, pid, obj + BUF_OFF, BUF_LEN)))
    logger.info("quad x1=%.4f x2=%.4f y1=%.4f y2=%.4f (+0x1b8 %.4f)", quad[0], quad[1], quad[2], quad[3], quad[4])
    logger.info("window in use [%d,%d) candidate [%d,%d) count %d", *state)
    areas = draw_areas(words)
    for x1, y1, x2, y2 in sorted(set(areas), key=lambda a: -(a[3] - a[1])):
        logger.info("draw area x %3d..%3d  y %3d..%3d  (%dx%d)  seen %d times", x1, x2, y1, y2, x2 - x1 + 1, y2 - y1 + 1,
                    areas.count((x1, y1, x2, y2)))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    probe(sys.argv[1] if len(sys.argv) > 1 else "192.168.1.85")
