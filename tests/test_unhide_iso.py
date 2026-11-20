"""unhide_iso: clears the ISO9660 hidden flag of the root directory so webMAN can mount the disc."""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

import unhide_iso as U

LAYOUTS = [(2352, 24), (2352, 16), (2048, 0)]  # sector size and offset of the user data


def _record(name: bytes, lba: int, length: int, flags: int) -> bytes:
    n = 33 + len(name) + (0 if len(name) % 2 else 1)
    r = bytearray(n)
    r[0], r[2:6], r[10:14], r[25], r[32] = n, struct.pack("<I", lba), struct.pack("<I", length), flags, len(name)
    r[33:33 + len(name)] = name
    return bytes(r)


def _image(layout: tuple[int, int], flags: dict[bytes, int], root_lba: int = 22) -> bytes:
    size, head = layout
    img = bytearray(size * 40)
    pvd = bytearray(2048)
    pvd[0], pvd[1:6] = 1, b"CD001"
    root = bytearray(34)
    root[0], root[2:6], root[10:14], root[25] = 34, struct.pack("<I", root_lba), struct.pack("<I", 2048), 2
    pvd[156:190] = root
    img[16 * size + head:16 * size + head + 2048] = pvd
    d = b"".join(_record(n, 30 + i, 2048, f) for i, (n, f) in enumerate(flags.items()))
    img[root_lba * size + head:root_lba * size + head + len(d)] = d
    return bytes(img)


def _flags(img: bytes, layout: tuple[int, int], root_lba: int = 22) -> list[int]:
    size, head = layout
    d, i, out = img[root_lba * size + head:root_lba * size + head + 2048], 0, []
    while i < 2048 and d[i]:
        out.append(d[i + 25])
        i += d[i]
    return out


HIDDEN = {b"\x00": 3, b"\x01": 3, b"ANA": 3, b"SYSTEM.CNF;1": 1, b"SLUS_010.13;1": 1}


@pytest.mark.parametrize("layout", LAYOUTS)
def test_the_hidden_bit_is_cleared_and_the_other_bits_stay(tmp_path: Path, layout):
    path = tmp_path / "g.bin"
    path.write_bytes(_image(layout, HIDDEN))
    assert U.unhide(path) == 5
    assert _flags(path.read_bytes(), layout) == [2, 2, 2, 0, 0]


@pytest.mark.parametrize("layout", LAYOUTS)
def test_only_the_root_directory_sector_changes(tmp_path: Path, layout):
    before = _image(layout, HIDDEN)
    path = tmp_path / "g.bin"
    path.write_bytes(before)
    U.unhide(path)
    after = path.read_bytes()
    size, _ = layout
    changed = {i // size for i in range(len(before)) if before[i] != after[i]}
    assert changed == {22} and len(after) == len(before)


def test_an_image_with_nothing_hidden_is_left_untouched(tmp_path: Path):
    before = _image((2352, 24), {b"\x00": 2, b"SYSTEM.CNF;1": 0})
    path = tmp_path / "g.bin"
    path.write_bytes(before)
    assert U.unhide(path) == 0 and path.read_bytes() == before


def test_dry_run_counts_but_does_not_write(tmp_path: Path):
    before = _image((2352, 24), HIDDEN)
    path = tmp_path / "g.bin"
    path.write_bytes(before)
    assert U.unhide(path, dry_run=True) == 5 and path.read_bytes() == before


def test_a_file_that_is_not_an_iso_is_refused(tmp_path: Path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"\x01" * 100000)
    with pytest.raises(ValueError):
        U.unhide(path)
