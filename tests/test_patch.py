"""Tests for ps1_netemu_patch using a synthetic ELF (no Sony binary needed).

Set PS1_NETEMU_SELF to a stock ps1_netemu.self to also run the real-file checks.
"""
import hashlib
import hmac
import os
import random
import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ps1_netemu_patch as P

LOAD_VA, LOAD_SIZE = 0x10000, 0x1B0000  # covers every patched address (up to 0x1be754)


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
    assert diff == sorted(0x200 + p.va - LOAD_VA for p in P.PATCHES)
    for p in P.PATCHES:
        assert struct.unpack(">I", out[0x200 + p.va - LOAD_VA:][:4])[0] == p.new


def test_patch_refuses_unexpected_bytes():
    elf = bytearray(synthetic_elf())
    elf[0x200 + P.PATCHES[0].va - LOAD_VA] ^= 0xFF
    with pytest.raises(ValueError, match="expected"):
        P.apply_patches(bytes(elf))


@pytest.mark.parametrize("delta", [0, 5, 100, 1234])
def test_deflate_to_size_is_exact_and_lossless(delta):
    rng = random.Random(7)
    data = bytes(rng.choice(b"abcdefgh") for _ in range(50_000))
    size = len(zlib.compress(data, 6)) + delta
    out = P.deflate_to_size(data, size)
    assert len(out) == size and zlib.decompress(out) == data


def test_deflate_to_size_refuses_when_it_cannot_fit():
    with pytest.raises(ValueError, match="cannot fit"):
        P.deflate_to_size(os.urandom(5000), 100)


@pytest.mark.skipif("PS1_NETEMU_SELF" not in os.environ, reason="stock SELF not provided")
def test_real_file_repack_keeps_layout_and_recomputes_integrity():
    stock = Path(os.environ["PS1_NETEMU_SELF"]).read_bytes()
    patched = P.apply_patches(P.unpack(stock))
    out = P.repack(stock, patched)
    assert len(out) == len(stock)
    sections, _, _, meta = P.parse_all(out)
    changeable = bytearray(len(stock))  # segments, the encrypted hash table and the ELF digest
    for s in sections:
        changeable[s.off:s.off + s.size] = b"\x01" * s.size
    changeable[meta.file_off:meta.file_off + len(meta.blob)] = b"\x01" * len(meta.blob)
    changeable[P.ELF_DIGEST_OFF:P.ELF_DIGEST_OFF + 20] = b"\x01" * 20
    assert all(stock[i] == out[i] for i in range(len(stock)) if not changeable[i])
    assert P.unpack(out) == patched  # decrypts back to exactly the patched ELF
    assert hashlib.sha1(patched).digest() == out[P.ELF_DIGEST_OFF:P.ELF_DIGEST_OFF + 20]
    for s in sections:
        raw = out[s.off:s.off + s.size]
        data = P._ctr(s.key, s.iv, raw) if s.encrypted else raw
        assert hmac.new(s.hmac_key, data, hashlib.sha1).digest() == meta.blob[s.hash_pos:s.hash_pos + 20]
