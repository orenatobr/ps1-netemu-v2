"""Tests for ps1_netemu_patch using a synthetic ELF (no Sony binary needed).

Set PS1_NETEMU_SELF to a stock ps1_netemu.self to also run the real-file check.
"""
import os
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ps1_netemu_patch as P

LOAD_VA, LOAD_SIZE = 0x10000, 0x110000


def synthetic_elf() -> bytes:
    """One PT_LOAD at va 0x10000 (file offset 0x200) holding the original words."""
    ehdr = bytearray(0x40)
    ehdr[:4] = b"\x7fELF"
    struct.pack_into(">QQHHHH", ehdr, 0x20, 0x40, 0, 0x40, 0x38, 1, 0)
    struct.pack_into(">HH", ehdr, 0x36, 0x38, 1)
    phdr = struct.pack(">IIQQQQQQ", 1, 5, 0x200, LOAD_VA, LOAD_VA, LOAD_SIZE, LOAD_SIZE, 0x10000)
    body = bytearray(LOAD_SIZE)
    for p in P.PATCHES:
        struct.pack_into(">I", body, p.va - LOAD_VA, p.old)
    return bytes(ehdr) + phdr + b"\0" * (0x200 - 0x40 - 0x38) + bytes(body)


def test_patches_change_only_target_words():
    elf = synthetic_elf()
    out = P.apply_patches(elf)
    diff = [i for i in range(0, len(elf), 4) if elf[i:i + 4] != out[i:i + 4]]
    assert diff == [0x200 + p.va - LOAD_VA for p in P.PATCHES]
    for p in P.PATCHES:
        assert struct.unpack(">I", out[0x200 + p.va - LOAD_VA:][:4])[0] == p.new


def test_patch_refuses_unexpected_bytes():
    elf = bytearray(synthetic_elf())
    elf[0x200 + P.PATCHES[0].va - LOAD_VA] ^= 0xFF
    with pytest.raises(ValueError, match="expected"):
        P.apply_patches(bytes(elf))


def fake_orig_self() -> bytes:
    """Header-shaped bytes with a valid flags block and digest block at 0x3a0."""
    flags = struct.pack(">IIQ", 1, 0x30, 1) + bytes(0x20)
    digest = struct.pack(">IIQ", 2, 0x40, 0) + bytes(range(0x30))
    return bytes(0x3A0) + flags + digest


def test_fself_roundtrip():
    elf = P.apply_patches(synthetic_elf())
    out = P.build_fself(elf, fake_orig_self())
    magic, _v, flags, typ, _meta, hdr_len, data_len = struct.unpack(">4sIHHIQQ", out[:32])
    assert (magic, flags, typ) == (b"SCE\0", 0x8000, 1)
    assert out[hdr_len:] == elf and data_len == len(elf) and hdr_len % 0x80 == 0
    seg_off = struct.unpack(">9Q", out[0x20:0x68])[5]
    off, size, comp, _, _, enc = struct.unpack(">QQ4I", out[seg_off:seg_off + 0x20])
    assert (off, size, comp, enc) == (0x200 + hdr_len, LOAD_SIZE, 1, 2)


def test_fself_minimal_header_roundtrip():
    orig = fake_orig_self()
    flags, digest = orig[0x3A0:0x3D0], orig[0x3D0:0x410]
    out = P.build_fself(P.apply_patches(synthetic_elf()), orig)
    ext = struct.unpack(">9Q", out[0x20:0x68])
    ctl_off, ctl_size = ext[7], ext[8]
    assert out[ctl_off:ctl_off + ctl_size] == digest  # only the digest block survives
    assert ext[6] == 0  # no SCE version block
    assert P.control_blocks(flags + digest).keys() == {1, 2}


@pytest.mark.skipif("PS1_NETEMU_SELF" not in os.environ, reason="stock SELF not provided")
def test_real_file_only_five_words_differ():
    stock = Path(os.environ["PS1_NETEMU_SELF"]).read_bytes()
    elf = P.unpack(stock)
    out = P.build_fself(P.apply_patches(elf), stock)
    embedded = out[struct.unpack(">Q", out[0x10:0x18])[0]:]
    assert len(embedded) == len(elf)
    diff = [i for i in range(0, len(elf), 4) if elf[i:i + 4] != embedded[i:i + 4]]
    assert len(diff) == len(P.PATCHES)
