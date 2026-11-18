"""Build ps1_netemu_v2.00.self from a stock ps1_netemu.self.

The stock SELF container is kept: header, offsets and sizes do not move. Each segment
is decrypted, patched, recompressed to the SAME stored size (zlib, padded with empty
stored blocks) and encrypted again with the original keys. The section HMAC-SHA1s and
the ELF SHA-1 are recomputed; the ECDSA signature is the only thing left stale.

Usage:
    python ps1_netemu_patch.py INPUT.self OUTPUT.self
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import struct
import sys
import zlib
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from cave_analog import build as _analog_cave
from cave_dpad import build as _dpad_cave
from cave_memcard import build as _memcard_cave
from cave_overscan import build as _overscan_cave
from ps3mapi_patch import CODE, CONSTANTS

logger = logging.getLogger(__name__)

# APP key for revision 0x1C (public, published by the RPCS3 project).
APP_ERK = bytes.fromhex("CFF025375BA0079226BE01F4A31F346D79F62CFB643CA910E16CF60BD9092752")
APP_RIV = bytes.fromhex("FD40664E2EBBA01BF359B0DCDF543DA4")
EXPECTED_KEY_REVISION = 0x1C
EMPTY_STORED_BLOCK = b"\x00\x00\x00\xff\xff"  # non-final, zero-length deflate block
CONTROL_OFF = 0x3A0  # control info; its last block holds the SHA-1 of the whole ELF
CAVE_VA = 0x1A9380  # first aligned address after the stock text segment (ends 0x1a9368)
CAVE_SIZE = 0x1000
ELF_DIGEST_OFF = CONTROL_OFF + 0x30 + 0x10 + 0x14  # type-1 block (0x30) + header (0x10) + magic (0x14)


@dataclass(frozen=True)
class Patch:
    """One 32-bit word replacement at a virtual address."""

    va: int
    old: int
    new: int
    what: str


# Every change lives in ps3mapi_patch (single source of truth).
ANALOG_VA = CAVE_VA + 0xE00
DPAD_VA = CAVE_VA + 0xA00
OVERSCAN_VA = CAVE_VA + 0xC00
_MEMCARD, _MEMCARD_BYTES = _memcard_cave(CAVE_VA)
_ANALOG, _ANALOG_BYTES = _analog_cave(ANALOG_VA)
_DPAD, _DPAD_BYTES = _dpad_cave(DPAD_VA)
_OVERSCAN, _OVERSCAN_BYTES = _overscan_cave(OVERSCAN_VA)
assert _MEMCARD_BYTES <= DPAD_VA - CAVE_VA and _DPAD_BYTES <= OVERSCAN_VA - DPAD_VA
assert _OVERSCAN_BYTES <= ANALOG_VA - OVERSCAN_VA
assert ANALOG_VA - CAVE_VA + _ANALOG_BYTES <= CAVE_SIZE
PATCHES = (
    *(Patch(a, old, new, why) for a, old, new, why in CODE),
    *(Patch(a, 0, value, "float constant in a free TOC slot") for a, value in CONSTANTS),
    *(Patch(a, old, new, why) for a, old, new, why in _MEMCARD),
    *(Patch(a, old, new, why) for a, old, new, why in _ANALOG),
    *(Patch(a, old, new, why) for a, old, new, why in _DPAD),
    *(Patch(a, old, new, why) for a, old, new, why in _OVERSCAN),
)


def _ctr(key: bytes, iv: bytes, data: bytes) -> bytes:
    return Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor().update(data)


@dataclass(frozen=True)
class Section:
    """A SELF data section: where it lives in the file and how it is stored."""

    off: int
    size: int
    phdr_index: int
    encrypted: bool
    compressed: bool
    key: bytes
    iv: bytes
    hash_pos: int  # where this section's HMAC-SHA1 sits in the decrypted metadata blob
    hmac_key: bytes


@dataclass(frozen=True)
class Metadata:
    """The decrypted metadata blob and what is needed to encrypt it back in place."""

    key: bytes
    iv: bytes
    blob: bytes
    file_off: int


def parse_sections(d: bytes) -> tuple[list[Section], bytes, int]:
    """Return the segment sections, the program headers and e_phoff of a retail SELF."""
    sections, phdrs, e_phoff, _ = parse_all(d)
    return sections, phdrs, e_phoff


def parse_all(d: bytes) -> tuple[list[Section], bytes, int, Metadata]:
    """Like `parse_sections`, plus the decrypted metadata."""
    magic, _ver, key_rev, hdr_type, meta_off, hdr_len, _ = struct.unpack(">4sIHHIQQ", d[:32])
    if magic != b"SCE\0" or hdr_type != 1:
        raise ValueError("not a SELF")
    if key_rev != EXPECTED_KEY_REVISION:
        raise ValueError(f"unsupported key revision 0x{key_rev:x}")
    _v, _ai, elf_off, phdr_off, *_ = struct.unpack(">9Q", d[0x20:0x68])
    e_phoff = struct.unpack(">Q", d[elf_off + 0x20:elf_off + 0x28])[0]
    phentsize, phnum = struct.unpack(">HH", d[elf_off + 0x36:elf_off + 0x3A])
    cbc = Cipher(algorithms.AES(APP_ERK), modes.CBC(APP_RIV)).decryptor()
    info = cbc.update(d[meta_off + 0x20:meta_off + 0x60])
    blob = _ctr(info[0:16], info[32:48], d[meta_off + 0x60:hdr_len])
    nsec, nkeys = struct.unpack(">II", blob[0x0C:0x14])
    keys = [blob[0x20 + nsec * 0x30 + 16 * i:][:16] for i in range(nkeys)]
    keys_off = 0x20 + nsec * 0x30
    sections = []
    for i in range(nsec):
        off, size, typ, idx, _h, sha_idx, enc, kidx, iidx, comp = struct.unpack(
            ">QQ8I", blob[0x20 + i * 0x30:0x20 + (i + 1) * 0x30])
        if typ == 2:  # 2 = segment data
            sections.append(Section(
                off, size, idx, enc == 3, comp == 2,
                keys[kidx] if enc == 3 else b"", keys[iidx] if enc == 3 else b"",
                keys_off + 16 * sha_idx, b"".join(keys[sha_idx + 2:sha_idx + 6])))
    meta = Metadata(info[0:16], info[32:48], blob, meta_off + 0x60)
    return sections, d[phdr_off:phdr_off + phnum * phentsize], e_phoff, meta


def _phdr(phdrs: bytes, index: int) -> tuple[int, int]:
    """(p_offset, p_filesz) of program header `index`."""
    p_offset, = struct.unpack(">Q", phdrs[index * 56 + 8:index * 56 + 16])
    p_filesz, = struct.unpack(">Q", phdrs[index * 56 + 32:index * 56 + 40])
    return p_offset, p_filesz


def unpack(self_bytes: bytes) -> bytes:
    """Decrypt a retail SELF into a flat ELF file (with section headers)."""
    d = self_bytes
    sections, phdrs, e_phoff = parse_sections(d)
    _v, _ai, elf_off, _po, shdr_off, *_ = struct.unpack(">9Q", d[0x20:0x68])
    ehdr = bytearray(d[elf_off:elf_off + 0x40])
    e_shoff = struct.unpack(">Q", ehdr[0x28:0x30])[0]
    shentsize, shnum = struct.unpack(">HH", ehdr[0x3A:0x3E])
    out = bytearray(ehdr)
    out += b"\0" * (e_phoff - len(out)) + phdrs
    for sec in sections:
        data = d[sec.off:sec.off + sec.size]
        if sec.encrypted:
            data = _ctr(sec.key, sec.iv, data)
        if sec.compressed:
            data = zlib.decompress(data)
        p_offset, _ = _phdr(phdrs, sec.phdr_index)
        out += b"\0" * (p_offset - len(out))
        out[p_offset:p_offset + len(data)] = data
    if shnum:
        out += b"\0" * (e_shoff - len(out))
        out += d[shdr_off:shdr_off + shnum * shentsize]
    return bytes(out)


def _va_to_off(elf: bytes, va: int) -> int:
    e_phoff, = struct.unpack(">Q", elf[0x20:0x28])
    phentsize, phnum = struct.unpack(">HH", elf[0x36:0x3A])
    for i in range(phnum):
        p_type, _f, p_off, p_va, _pa, p_filesz, _ms, _al = struct.unpack(
            ">IIQQQQQQ", elf[e_phoff + i * phentsize:e_phoff + (i + 1) * phentsize])
        if p_type == 1 and p_va <= va < p_va + p_filesz:
            return p_off + va - p_va
    raise ValueError(f"va 0x{va:x} not in a loadable segment")


def extend_text(elf: bytes, extra: int = CAVE_SIZE) -> bytes:
    """Grow the first loadable segment (text) by `extra` zero bytes and return the new ELF.

    The stock text segment ends at 0x1a9368 and the loader already maps its last page up to
    0x1b0000, so the new bytes (a code cave) need no extra address space. p_filesz and p_memsz
    of program header 0 are updated; the ELF header copy lives inside that segment, so the
    change travels with it.
    """
    e_phoff, = struct.unpack(">Q", elf[0x20:0x28])
    phentsize, phnum = struct.unpack(">HH", elf[0x36:0x3A])
    p_type, _f, p_off, p_va, _pa, p_filesz, p_memsz, _al = struct.unpack(">IIQQQQQQ", elf[e_phoff:e_phoff + 56])
    if p_type != 1 or p_off != 0 or p_filesz != p_memsz:
        raise ValueError("program header 0 is not the plain text segment")
    new = p_filesz + extra
    for i in range(1, phnum):
        t, _f2, o, v, *_ = struct.unpack(">IIQQQQQQ", elf[e_phoff + i * phentsize:e_phoff + (i + 1) * phentsize])
        if t == 1 and o and o < new:
            raise ValueError("the extended text segment would overlap the next file segment")
        if t == 1 and v and v < p_va + new and v > p_va:
            raise ValueError("the extended text segment would overlap the next memory segment")
    out = bytearray(elf)
    struct.pack_into(">QQ", out, e_phoff + 32, new, new)
    return bytes(out)


def apply_patches(elf: bytes, patches: tuple[Patch, ...] = PATCHES) -> bytes:
    """Return a patched copy of `elf`; refuses to touch unexpected bytes."""
    out = bytearray(elf)
    for p in patches:
        off = _va_to_off(elf, p.va)
        found, = struct.unpack(">I", out[off:off + 4])
        if found != p.old:
            raise ValueError(f"0x{p.va:x}: expected 0x{p.old:08x}, found 0x{found:08x}")
        out[off:off + 4] = struct.pack(">I", p.new)
        logger.info("patched 0x%x: %08x -> %08x (%s)", p.va, p.old, p.new, p.what)
    return bytes(out)


def deflate_to_size(data: bytes, size: int) -> bytes:
    """Compress `data` as a zlib stream of exactly `size` bytes.

    Tries zlib settings (level, memLevel, strategy) until the stream fits and the leftover is a multiple of 5,
    then pads with empty non-final stored blocks right after the 2-byte zlib header.
    """
    for level in (6, 9, 8, 7, 5, 4, 3, 2, 1):
        for mem in range(9, 0, -1):
            for strategy in (zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED):
                comp = zlib.compressobj(level, zlib.DEFLATED, 15, mem, strategy)
                stream = comp.compress(data) + comp.flush()
                pad = size - len(stream)
                if pad >= 0 and pad % len(EMPTY_STORED_BLOCK) == 0:
                    out = stream[:2] + EMPTY_STORED_BLOCK * (pad // len(EMPTY_STORED_BLOCK)) + stream[2:]
                    if len(out) != size or zlib.decompress(out) != data:
                        raise AssertionError("padded stream does not round-trip")
                    return out
    # No whole-stream setting lands on a multiple of 5: add one sync flush at a varying point,
    # which keeps the dictionary but shifts the stream length by a few bytes.
    for level in (9, 8, 7, 6):
        for mem in (9, 8):
            for cut in range(4096, len(data), 4096):
                comp = zlib.compressobj(level, zlib.DEFLATED, 15, mem)
                stream = comp.compress(data[:cut]) + comp.flush(zlib.Z_SYNC_FLUSH) + comp.compress(data[cut:]) + comp.flush()
                pad = size - len(stream)
                if pad >= 0 and pad % len(EMPTY_STORED_BLOCK) == 0:
                    out = stream[:2] + EMPTY_STORED_BLOCK * (pad // len(EMPTY_STORED_BLOCK)) + stream[2:]
                    if len(out) != size or zlib.decompress(out) != data:
                        raise AssertionError("padded stream does not round-trip")
                    return out
    raise ValueError(f"cannot fit a {len(data)} byte segment into {size} compressed bytes")


def repack(orig_self: bytes, patched_elf: bytes) -> bytes:
    """Write the patched segments back into the stock SELF, keeping its layout.

    Section HMACs (inside the encrypted metadata) and the ELF SHA-1 in the control
    info are recomputed. The ECDSA signature cannot be, and is left as it was.
    """
    sections, phdrs, e_phoff, meta = parse_all(orig_self)
    phdr_off = struct.unpack(">Q", orig_self[0x38:0x40])[0]
    phdrs = patched_elf[e_phoff:e_phoff + len(phdrs)]  # sizes may have grown (extend_text)
    out = bytearray(orig_self)
    out[phdr_off:phdr_off + len(phdrs)] = phdrs
    blob = bytearray(meta.blob)
    for sec in sections:
        p_offset, p_filesz = _phdr(phdrs, sec.phdr_index)
        data = patched_elf[p_offset:p_offset + p_filesz]
        if sec.compressed:
            data = deflate_to_size(data, sec.size)
        if len(data) != sec.size:
            raise ValueError("segment size changed; the container would need new metadata")
        blob[sec.hash_pos:sec.hash_pos + 20] = hmac.new(sec.hmac_key, data, hashlib.sha1).digest()
        out[sec.off:sec.off + sec.size] = _ctr(sec.key, sec.iv, data) if sec.encrypted else data
    out[meta.file_off:meta.file_off + len(blob)] = _ctr(meta.key, meta.iv, bytes(blob))
    out[ELF_DIGEST_OFF:ELF_DIGEST_OFF + 20] = hashlib.sha1(patched_elf).digest()
    return bytes(out)


def main(src: str, dst: str) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with open(src, "rb") as fh:
        orig = fh.read()
    elf = unpack(orig)
    if any(p.va >= CAVE_VA for p in PATCHES):  # only grow the segment when code lives in the cave
        elf = extend_text(elf)
    out = repack(orig, apply_patches(elf))
    with open(dst, "wb") as fh:
        fh.write(out)
    logger.info("wrote %s (%d bytes) sha256=%s", dst, len(out), hashlib.sha256(out).hexdigest())


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
