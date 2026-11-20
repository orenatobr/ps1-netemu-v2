"""Clear the ISO9660 "hidden" flag of the root directory of a PS1 disc image, so webMAN can mount it.

Some discs (Legend of Mana is one) mark every entry of the root directory as hidden (flag bit 0), including
`SYSTEM.CNF` and the boot executable. The PS3 does not show hidden entries of a mounted disc: webMAN leaves
`/dev_bdvd` empty, the emulator is started without a disc and dies in under 4 seconds with error 80028F17, the same
with the stock and the patched emulator. A real console reads the disc by sector and ignores the flag.

Only the sector of the root directory is written, one byte per entry. The sector checksum (EDC/ECC) of a raw image
is not recomputed; neither the PS3 nor the emulator checks it.

    python unhide_iso.py "Legend of Mana (USA).bin"            # back the image up first
    python unhide_iso.py "Legend of Mana (USA).bin" --dry-run  # only count
"""
from __future__ import annotations

import argparse
import logging
import struct
from pathlib import Path

logger = logging.getLogger(__name__)

HIDDEN = 0x01  # ISO9660 file flags, bit 0: existence (hidden)
LAYOUTS = ((2352, 24), (2352, 16), (2048, 0))  # sector size and offset of the user data: raw mode 2, raw mode 1, cooked
PVD_SECTOR = 16
SECTOR_DATA = 2048


def _layout(f) -> tuple[int, int]:
    """Return the (sector size, data offset) under which a volume descriptor sits at sector 16."""
    for size, head in LAYOUTS:
        f.seek(PVD_SECTOR * size + head)
        pvd = f.read(7)
        if pvd[:1] == b"\x01" and pvd[1:6] == b"CD001":
            return size, head
    raise ValueError("no ISO9660 volume descriptor at sector 16")


def unhide(path: str | Path, dry_run: bool = False) -> int:
    """Clear the hidden flag of every entry of the root directory of the image.

    Args:
        path: The disc image (`.bin`, raw 2352 or cooked 2048 byte sectors).
        dry_run: Count the hidden entries without writing.

    Returns:
        The number of entries that were hidden.

    Raises:
        ValueError: The file has no ISO9660 volume descriptor.
    """
    with open(path, "rb" if dry_run else "r+b") as f:
        size, head = _layout(f)
        f.seek(PVD_SECTOR * size + head + 156)  # the root directory record inside the volume descriptor
        root = f.read(34)
        lba, length = struct.unpack("<I", root[2:6])[0], struct.unpack("<I", root[10:14])[0]
        cleared = 0
        for sector in range(lba, lba + max(1, -(-length // SECTOR_DATA))):  # a directory is a run of whole sectors
            at = sector * size + head
            f.seek(at)
            data = bytearray(f.read(SECTOR_DATA))
            i, n = 0, 0
            while i < len(data) and data[i]:
                if data[i + 25] & HIDDEN:
                    data[i + 25] &= ~HIDDEN & 0xFF
                    n += 1
                i += data[i]
            if n and not dry_run:
                f.seek(at)
                f.write(data)
            cleared += n
    logger.info("%s: %d hidden entries %s", path, cleared, "found" if dry_run else "cleared")
    return cleared


def main() -> None:
    """Command line entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("image", help="the .bin disc image")
    ap.add_argument("--dry-run", action="store_true", help="count the hidden entries without writing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    unhide(args.image, args.dry_run)


if __name__ == "__main__":
    main()
