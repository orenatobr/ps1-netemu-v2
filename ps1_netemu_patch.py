"""Build ps1_netemu_v2.00.self from a stock ps1_netemu.self.

Pipeline: decrypt the retail SELF (APP, key revision 0x1C, public keys), apply
the byte patches below (each one checks the original bytes first), then wrap
the ELF in an unsigned "fake" SELF (fself) for custom firmware.

Usage:
    python ps1_netemu_patch.py INPUT.self OUTPUT.self
"""
from __future__ import annotations

import hashlib
import logging
import struct
import sys
import zlib
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger(__name__)

# APP key for revision 0x1C (public, published by the RPCS3 project).
APP_ERK = bytes.fromhex("CFF025375BA0079226BE01F4A31F346D79F62CFB643CA910E16CF60BD9092752")
APP_RIV = bytes.fromhex("FD40664E2EBBA01BF359B0DCDF543DA4")
EXPECTED_KEY_REVISION = 0x1C


@dataclass(frozen=True)
class Patch:
    """One 32-bit instruction replacement at a virtual address."""

    va: int
    old: int
    new: int
    what: str


# PowerPC encodings:
#   li r0,1             = 0x38000001
#   fsubs f12,f0,f0     = 0xED800028   (was fsubs f12,f0,f13)
#   fmr   f13,f10       = 0xFDA05090   (was fadds f13,f12,f1)
CLRLWI_R0_R0_31 = 0x540007FE
LI_R0_1 = 0x38000001

PATCHES = (
    # Analog mode on by default: the three places that seed the per-pad
    # analog flag (pad + 0x9c) with `config & 1` now seed it with 1.
    Patch(0x0D3080, CLRLWI_R0_R0_31, LI_R0_1, "pad reset: analog flag = 1"),
    Patch(0x0D3288, CLRLWI_R0_R0_31, LI_R0_1, "pad slot reset loop: analog flag = 1"),
    Patch(0x0D4EAC, CLRLWI_R0_R0_31, LI_R0_1, "pad init loop: analog flag = 1"),
    # Vertical stretch: display command geometry (function 0x113f0c, label
    # 0x115820). Y offset of the quad forced to 0 and its bottom edge forced to
    # the full frame height; texture V range (f1) is untouched, so only the
    # on-screen quad grows, 4:3 horizontal extent stays as is.
    Patch(0x115824, 0xED806828, 0xED800028, "quad top offset = 0"),
    Patch(0x115874, 0xEDAC082A, 0xFDA05090, "quad bottom = full height"),
)


def _ctr(key: bytes, iv: bytes, data: bytes) -> bytes:
    return Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor().update(data)


def unpack(self_bytes: bytes) -> bytes:
    """Decrypt a retail SELF into a flat ELF file (with section headers)."""
    d = self_bytes
    magic, _ver, key_rev, hdr_type, meta_off, hdr_len, _dlen = struct.unpack(">4sIHHIQQ", d[:32])
    if magic != b"SCE\0" or hdr_type != 1:
        raise ValueError("not a SELF")
    if key_rev != EXPECTED_KEY_REVISION:
        raise ValueError(f"unsupported key revision 0x{key_rev:x}")
    _v, _ai, elf_off, phdr_off, shdr_off, *_ = struct.unpack(">9Q", d[0x20:0x68])
    ehdr = bytearray(d[elf_off:elf_off + 0x40])
    e_phoff, e_shoff = struct.unpack(">QQ", ehdr[0x20:0x30])
    phentsize, phnum, shentsize, shnum = struct.unpack(">HHHH", ehdr[0x36:0x3E])

    cbc = Cipher(algorithms.AES(APP_ERK), modes.CBC(APP_RIV)).decryptor()
    info = cbc.update(d[meta_off + 0x20:meta_off + 0x60])
    meta_key, meta_iv = info[0:16], info[32:48]
    blob = _ctr(meta_key, meta_iv, d[meta_off + 0x60:hdr_len])
    nsec, nkeys = struct.unpack(">II", blob[0x0C:0x14])
    keys = [blob[0x20 + nsec * 0x30 + 16 * i:][:16] for i in range(nkeys)]

    phdrs = d[phdr_off:phdr_off + phnum * phentsize]
    out = bytearray(ehdr)
    out += b"\0" * (e_phoff - len(out)) + phdrs
    for i in range(nsec):
        off, size, typ, idx, _h, _s, enc, kidx, iidx, comp = struct.unpack(
            ">QQ8I", blob[0x20 + i * 0x30:0x20 + (i + 1) * 0x30])
        if typ != 2:  # 2 = segment data
            continue
        data = d[off:off + size]
        if enc == 3:
            data = _ctr(keys[kidx], keys[iidx], data)
        if comp == 2:
            data = zlib.decompress(data)
        p_offset = struct.unpack(">Q", phdrs[idx * phentsize + 8:idx * phentsize + 16])[0]
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


def control_blocks(control: bytes) -> dict[int, bytes]:
    """Split SELF control info into {type: block}; each block starts with (type, size)."""
    blocks, off = {}, 0
    while off + 8 <= len(control):
        typ, size = struct.unpack(">II", control[off:off + 8])
        if size < 8 or off + size > len(control):
            raise ValueError(f"bad control block at +0x{off:x}")
        blocks[typ] = control[off:off + size]
        off += size
    return blocks


def build_fself(elf: bytes, orig_self: bytes, minimal: bool = True) -> bytes:
    """Wrap `elf` in an unsigned SELF, reusing the original app info.

    `minimal` keeps only the digest control block (PSL1GHT layout, no SCE
    version block); otherwise the original capability flags are copied too.
    """
    app_info = orig_self[0x70:0x90]
    sce_version = b"" if minimal else orig_self[0x390:0x3A0]
    control = orig_self[0x3A0:0x410]
    if minimal:
        control = control_blocks(control)[2]  # digest block only; no capability flags
    ehdr = elf[:0x40]
    e_phoff, e_shoff = struct.unpack(">QQ", ehdr[0x20:0x30])
    phentsize, phnum = struct.unpack(">HH", ehdr[0x36:0x3A])
    phdrs = elf[e_phoff:e_phoff + phnum * phentsize]

    app_off, elf_off, phdr_off = 0x70, 0x90, 0x90 + 0x40
    seg_off = phdr_off + len(phdrs)
    ver_off = seg_off + 0x20 * phnum
    ctl_off = ver_off + len(sce_version)
    end = ctl_off + len(control)
    data_off = (end + 0x7F) & ~0x7F

    segs = b""
    for i in range(phnum):
        p_type, _f, p_off, *_r, p_filesz, _ms, _al = struct.unpack(
            ">IIQQQQQQ", phdrs[i * phentsize:(i + 1) * phentsize])
        # (offset, size, compressed=1 none, 0, 0, encrypted=2 none for PT_LOAD)
        segs += struct.pack(">QQ4I", p_off + data_off, p_filesz, 1, 0, 0, 2 if p_type == 1 else 0)

    head = struct.pack(">4sIHHIQQ", b"SCE\0", 2, 0x8000, 1, end - 0x10, data_off, len(elf))
    head += struct.pack(">9Q", 3, app_off, elf_off, phdr_off, data_off + e_shoff,
                        seg_off, ver_off if sce_version else 0, ctl_off, len(control))
    blob = bytearray(data_off)
    for off, chunk in ((0, head), (app_off, app_info), (elf_off, ehdr), (phdr_off, phdrs),
                       (seg_off, segs), (ver_off, sce_version), (ctl_off, control)):
        blob[off:off + len(chunk)] = chunk
    return bytes(blob) + elf


def main(src: str, dst: str) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with open(src, "rb") as fh:
        orig = fh.read()
    elf = apply_patches(unpack(orig))
    out = build_fself(elf, orig)
    with open(dst, "wb") as fh:
        fh.write(out)
    logger.info("wrote %s (%d bytes) sha256=%s", dst, len(out), hashlib.sha256(out).hexdigest())


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
