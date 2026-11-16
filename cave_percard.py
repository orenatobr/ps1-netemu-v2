"""Code cave: one memory card per game, named after the game's serial and put in slot 1.

`percard` runs at the start of the memory card hook (see cave_memcard). It

  1. opens the disc image whose path the emulator keeps in a global buffer (TOC slot -0x7fac, copied there
     from argv[5] before the hook) and finds the ISO9660 volume descriptor in any of the three layouts
     (raw 2352-byte sectors with a 24 or 16 byte header, or 2048-byte sectors);
  2. reads the root directory, finds SYSTEM.CNF, finds "cdrom:" in it and turns the executable name that
     follows (SLUS_006.62;1) into the serial "SLUS-00662" (underscore to hyphen, dot dropped, upper case),
     kept in a 32-byte buffer in the data segment (NAME_VA) and finally given its extension (`SLUS-00662.VM1`), the
     form the slots and the Assign Slots menu use for a card (slot 2 holds `PS1.VM1`);
  3. opens /dev_hdd0/savedata/vmc/<serial>.VM1; when it does not exist it writes a blank, formatted
     128 KB card (header and write-test frames "MC", 15 free directory frames, 20 empty broken-sector
     frames, 0xFF filler frames, empty data frames), the layout of any standard PS1 card.

It returns r3 = 1 when the card exists and NAME_VA holds its name, else 0 (no disc path, unknown disc, a
failed open or write); then the hook puts NAME_VA in slot 1 (r30 and r28) and everything else follows the
original code. A card is never touched when it already exists. A later disc of a multi-disc game (a path
with "(Disc 2)" to "(Disc 9)") takes the serial of its "(Disc 1)" image when that file opens, so all the
discs share one card; "[Disc 2]" and "(Disc 2 of 3)" work too. Without that file the disc's own serial is used.

The functions called are the emulator's own: open 0x15532c (path, flags, &fd, 0, 0), lseek 0x14f9f4
(fd, offset, whence, &pos), read 0x14f75c and write 0x14f7b4 (fd, buffer, size, &done), close 0x14f80c and
sprintf 0xa5420; the stock format "/dev_hdd0/savedata/vmc/%s.VM1" is in TOC slot -0x7b04.
"""
from __future__ import annotations

OPEN, LSEEK, READ, WRITE, CLOSE, SPRINTF = 0x15532C, 0x14F9F4, 0x14F75C, 0x14F7B4, 0x14F80C, 0xA5420
PATH_TOC, FMT_TOC = -0x7FAC, -0x7B04
NAME_VA = 0x1BE780  # 32 free bytes in the writable data segment
O_CREAT_RDWR = 0x42  # CELL_FS_O_CREAT 0100 | CELL_FS_O_RDWR 02, in octal like the PS3 headers (0x200 is O_TRUNC)
FRAME = 0xC10
SAVE = 0xB00  # r20..r26 are saved at SAVE + 8 * k
FD, DONE, PATH, SECTOR, CARD, DISCPATH, LASTBUF = 0x70, 0x78, 0xC0, 0x200, 0xA00, 0xB80, 0xA80  # 0x80 bytes each
LAST_GAME = b"/dev_hdd0/tmp/wmtmp/last_game.txt\0"  # webMAN writes the path of the game it just launched here



def program() -> list[tuple]:
    hi, lo = (NAME_VA >> 16) & 0xFFFF, NAME_VA & 0xFFFF
    p: list[tuple] = [
        ("label", "percard"),
        ("mflr", "r0"), ("std", "r0", 0x10, "r1"), ("stdu", "r1", -FRAME, "r1"),
    ]
    p += [("std", f"r{20 + k}", SAVE + 8 * k, "r1") for k in range(7)]
    p += [
        ("lwz", "r25", PATH_TOC, "r2"), ("lbz", "r0", 0, "r25"), ("cmpwi", "r0", 0), ("bne", "havepath"),
        # the emulator was started without a path (webMAN boots "from disc"): take it from webMAN's record
        ("bl", "getpc"), ("b", "afterstr"),
        *[("word", int.from_bytes(s[i:i + 4], "big")) for s in [LAST_GAME.ljust(-(-len(LAST_GAME) // 4) * 4, b"\0")]
          for i in range(0, len(s), 4)],
        ("label", "afterstr"),
        ("addi", "r3", "r3", 4), ("li", "r4", 0), ("addi", "r5", "r1", FD), ("li", "r6", 0), ("li", "r7", 0),
        ("bl", OPEN), ("cmpwi", "r3", 0), ("bne", "fail"),
        ("lwz", "r20", FD, "r1"),
        ("mr", "r3", "r20"), ("addi", "r4", "r1", LASTBUF), ("li", "r5", 0x7E), ("addi", "r6", "r1", DONE),
        ("bl", READ), ("cmpwi", "r3", 0), ("bne", "closefail"),
        ("mr", "r3", "r20"), ("bl", CLOSE),
        ("ld", "r9", DONE, "r1"), ("addi", "r25", "r1", LASTBUF),
        ("label", "trim"),  # drop trailing spaces and line ends
        ("cmpwi", "r9", 0), ("beq", "trimmed"), ("add", "r10", "r25", "r9"), ("lbz", "r11", -1, "r10"),
        ("cmpwi", "r11", 0x20), ("bgt", "trimmed"), ("addi", "r9", "r9", -1), ("b", "trim"),
        ("label", "trimmed"),
        ("add", "r10", "r25", "r9"), ("li", "r11", 0), ("stb", "r11", 0, "r10"),
        ("lbz", "r0", 0, "r25"), ("cmpwi", "r0", 0), ("beq", "fail"),
        ("label", "havepath"),
        # a later disc of a multi-disc game ("(Disc 2)" and up) uses the serial of "(Disc 1)", so every disc
        # shares one card; the path is copied (the emulator's own buffer is left alone) and the digit changed
        ("addi", "r26", "r1", DISCPATH), ("li", "r9", 0),
        ("label", "cpy0"),
        ("add", "r11", "r25", "r9"), ("lbz", "r10", 0, "r11"), ("add", "r12", "r26", "r9"), ("stb", "r10", 0, "r12"),
        ("cmpwi", "r10", 0), ("beq", "copied"), ("addi", "r9", "r9", 1), ("cmpwi", "r9", 0x7E), ("blt", "cpy0"),
        ("b", "noalt"),
        ("label", "copied"),
        ("mr", "r11", "r26"),
        ("label", "fnd"),
        ("lbz", "r10", 0, "r11"), ("cmpwi", "r10", 0), ("beq", "noalt"),
        ("cmpwi", "r10", 0x28), ("beq", "open1"), ("cmpwi", "r10", 0x5B), ("bne", "fnext"),  # "(" or "["
        ("label", "open1"),
    ]
    for k, ch in enumerate("Disc ", 1):
        p += [("lbz", "r12", k, "r11"), ("cmpwi", "r12", ord(ch)), ("bne", "fnext")]
    p += [
        ("lbz", "r12", 6, "r11"), ("cmpwi", "r12", 0x31), ("beq", "noalt"),
        ("cmpwi", "r12", 0x32), ("blt", "fnext"), ("cmpwi", "r12", 0x39), ("bgt", "fnext"),
        ("lbz", "r12", 7, "r11"), ("cmpwi", "r12", 0x29), ("beq", "close1"), ("cmpwi", "r12", 0x5D), ("beq", "close1"),
        ("cmpwi", "r12", 0x20), ("bne", "fnext"),  # ")", "]" or the space of "(Disc 2 of 3)"
        ("label", "close1"),
        ("li", "r12", 0x31), ("stb", "r12", 6, "r11"),
        ("mr", "r3", "r26"), ("li", "r4", 0), ("addi", "r5", "r1", FD), ("li", "r6", 0), ("li", "r7", 0),
        ("bl", OPEN), ("cmpwi", "r3", 0), ("beq", "opened"), ("b", "noalt"),
        ("label", "fnext"),
        ("addi", "r11", "r11", 1), ("b", "fnd"),
        ("label", "noalt"),
        ("mr", "r3", "r25"),
        ("li", "r4", 0), ("addi", "r5", "r1", FD), ("li", "r6", 0), ("li", "r7", 0), ("bl", OPEN),
        ("cmpwi", "r3", 0), ("bne", "fail"),
        ("label", "opened"),
        ("lwz", "r20", FD, "r1"), ("addi", "r23", "r1", SECTOR),
        # the volume descriptor, in one of three layouts
        ("li", "r21", 2352), ("li", "r22", 24), ("bl", "hdr"), ("cmpwi", "r3", 0), ("beq", "lay"),
        ("li", "r22", 16), ("bl", "hdr"), ("cmpwi", "r3", 0), ("beq", "lay"),
        ("li", "r21", 2048), ("li", "r22", 0), ("bl", "hdr"), ("cmpwi", "r3", 0), ("bne", "closefail"),
        ("label", "lay"),
        ("addi", "r9", "r23", 158), ("lwbrx", "r24", "r0", "r9"),  # extent of the root directory
        ("mr", "r3", "r20"), ("mullw", "r4", "r24", "r21"), ("add", "r4", "r4", "r22"),
        ("mr", "r5", "r23"), ("li", "r6", 0x800), ("bl", "readat"), ("cmpwi", "r3", 0), ("bne", "closefail"),
        ("mr", "r24", "r23"), ("addi", "r25", "r23", 0x7F0),
        ("label", "recloop"),
        ("lbz", "r9", 0, "r24"), ("cmpwi", "r9", 0), ("beq", "closefail"),
        ("lbz", "r10", 32, "r24"), ("cmpwi", "r10", 12), ("blt", "recnext"),
        ("lwz", "r11", 33, "r24"), ("lis", "r12", 0x5359), ("ori", "r12", "r12", 0x5354),  # "SYST"
        ("cmpw", "r11", "r12"), ("bne", "recnext"),
        ("lwz", "r11", 37, "r24"), ("lis", "r12", 0x454D), ("ori", "r12", "r12", 0x2E43),  # "EM.C"
        ("cmpw", "r11", "r12"), ("bne", "recnext"),
        ("lhz", "r11", 41, "r24"), ("cmpwi", "r11", 0x4E46), ("bne", "recnext"),  # "NF"
        ("b", "found"),
        ("label", "recnext"),
        ("add", "r24", "r24", "r9"), ("cmpw", "r24", "r25"), ("blt", "recloop"), ("b", "closefail"),
        ("label", "found"),
        ("addi", "r9", "r24", 2), ("lwbrx", "r24", "r0", "r9"),  # extent of SYSTEM.CNF
        ("mr", "r3", "r20"), ("mullw", "r4", "r24", "r21"), ("add", "r4", "r4", "r22"),
        ("mr", "r5", "r23"), ("li", "r6", 0x800), ("bl", "readat"), ("cmpwi", "r3", 0), ("bne", "closefail"),
        # find "cdrom:" (any case)
        ("mr", "r24", "r23"), ("addi", "r25", "r23", 0x7F0),
        ("label", "cdloop"),
    ]
    for k, ch in enumerate("cdrom:"):
        p += [("lbz", "r9", k, "r24"), ("ori", "r9", "r9", 0x20), ("cmpwi", "r9", ord(ch)), ("bne", "cdnext")]
    p += [
        ("b", "cdfound"),
        ("label", "cdnext"),
        ("addi", "r24", "r24", 1), ("cmpw", "r24", "r25"), ("blt", "cdloop"), ("b", "closefail"),
        ("label", "cdfound"),
        ("addi", "r24", "r24", 6),
        ("label", "skip"),
        ("lbz", "r9", 0, "r24"), ("cmpwi", "r9", 0x5C), ("beq", "skipn"), ("cmpwi", "r9", 0x2F), ("beq", "skipn"),
        ("cmpwi", "r9", 0x20), ("beq", "skipn"), ("cmpwi", "r9", 9), ("beq", "skipn"), ("b", "copy"),
        ("label", "skipn"),
        ("addi", "r24", "r24", 1), ("b", "skip"),
        ("label", "copy"),
        ("lis", "r26", hi), ("ori", "r26", "r26", lo), ("li", "r25", 0),
        ("label", "cploop"),
        ("lbz", "r9", 0, "r24"), ("addi", "r24", "r24", 1),
        ("cmpwi", "r9", 0x3B), ("beq", "cpdone"), ("cmpwi", "r9", 0x21), ("blt", "cpdone"),
        ("cmpwi", "r9", 0x2E), ("beq", "cploop"),
        ("cmpwi", "r9", 0x5F), ("bne", "notus"), ("li", "r9", 0x2D),
        ("label", "notus"),
        ("cmpwi", "r9", 0x61), ("blt", "upok"), ("cmpwi", "r9", 0x7A), ("bgt", "upok"), ("addi", "r9", "r9", -0x20),
        ("label", "upok"),
        ("cmpwi", "r25", 15), ("bge", "cpdone"),
        ("add", "r10", "r26", "r25"), ("stb", "r9", 0, "r10"), ("addi", "r25", "r25", 1), ("b", "cploop"),
        ("label", "cpdone"),
        ("li", "r9", 0), ("add", "r10", "r26", "r25"), ("stb", "r9", 0, "r10"),
        ("cmpwi", "r25", 4), ("blt", "closefail"),
        ("mr", "r22", "r25"),  # the serial's length, for the extension appended at the end
        ("mr", "r3", "r20"), ("bl", CLOSE),
        # the card file
        ("addi", "r3", "r1", PATH), ("lwz", "r4", FMT_TOC, "r2"), ("mr", "r5", "r26"), ("bl", SPRINTF),
        ("addi", "r3", "r1", PATH), ("li", "r4", 0), ("addi", "r5", "r1", FD), ("li", "r6", 0), ("li", "r7", 0),
        ("bl", OPEN), ("cmpwi", "r3", 0), ("bne", "create"),
        ("lwz", "r3", FD, "r1"), ("bl", CLOSE), ("b", "ok"),
        ("label", "create"),
        ("addi", "r3", "r1", PATH), ("li", "r4", O_CREAT_RDWR), ("addi", "r5", "r1", FD), ("li", "r6", 0),
        ("li", "r7", 0), ("bl", OPEN), ("cmpwi", "r3", 0), ("bne", "fail"),
        ("lwz", "r20", FD, "r1"), ("li", "r24", 0),
        ("label", "frloop"),
        ("addi", "r25", "r1", CARD), ("li", "r9", 0),
        ("cmpwi", "r24", 36), ("blt", "fill"), ("cmpwi", "r24", 62), ("bgt", "fill"), ("li", "r9", -1),
        ("label", "fill"),
    ]
    p += [("std", "r9", 8 * k, "r25") for k in range(16)]
    p += [
        ("cmpwi", "r24", 0), ("beq", "mc"), ("cmpwi", "r24", 63), ("beq", "mc"),
        ("cmpwi", "r24", 15), ("ble", "dirf"), ("cmpwi", "r24", 35), ("ble", "brkf"), ("b", "wr"),
        ("label", "mc"),
        ("li", "r9", 0x4D), ("stb", "r9", 0, "r25"), ("li", "r9", 0x43), ("stb", "r9", 1, "r25"),
        ("li", "r9", 0x0E), ("stb", "r9", 127, "r25"), ("b", "wr"),
        ("label", "dirf"),
        ("li", "r9", 0xA0), ("stb", "r9", 0, "r25"), ("stb", "r9", 127, "r25"),
        ("li", "r9", 0xFF), ("stb", "r9", 8, "r25"), ("stb", "r9", 9, "r25"), ("b", "wr"),
        ("label", "brkf"),
        ("li", "r9", 0xFF),
    ]
    p += [("stb", "r9", k, "r25") for k in (0, 1, 2, 3, 8, 9)]
    p += [
        ("label", "wr"),
        ("mr", "r3", "r20"), ("addi", "r4", "r1", CARD), ("li", "r5", 0x80), ("addi", "r6", "r1", DONE),
        ("bl", WRITE), ("cmpwi", "r3", 0), ("bne", "closefail"),
        ("addi", "r24", "r24", 1), ("cmpwi", "r24", 1024), ("blt", "frloop"),
        ("mr", "r3", "r20"), ("bl", CLOSE),
        ("label", "ok"),
        # the slots and the Assign Slots menu name a card with its extension ("PS1.VM1"); the card path above used
        # the bare serial, so ".VM1" goes on now
        ("add", "r10", "r26", "r22"), ("lis", "r9", 0x2E56), ("ori", "r9", "r9", 0x4D31), ("stw", "r9", 0, "r10"),
        ("li", "r9", 0), ("stb", "r9", 4, "r10"),
        ("li", "r3", 1), ("b", "out"),
        ("label", "closefail"),
        ("mr", "r3", "r20"), ("bl", CLOSE),
        ("label", "fail"),
        ("li", "r3", 0),
        ("label", "out"),
    ]
    p += [("ld", f"r{20 + k}", SAVE + 8 * k, "r1") for k in range(7)]
    p += [("addi", "r1", "r1", FRAME), ("ld", "r0", 0x10, "r1"), ("mtlr", "r0"), ("blr",)]
    p += [  # hdr: is there an ISO9660 volume descriptor at sector 16 for sector size r21 and header r22?
        ("label", "getpc"),
        ("mflr", "r3"), ("blr",),
        ("label", "hdr"),
        ("mflr", "r0"), ("std", "r0", 0x10, "r1"), ("stdu", "r1", -0x40, "r1"),
        ("mr", "r3", "r20"), ("mulli", "r4", "r21", 16), ("add", "r4", "r4", "r22"),
        ("mr", "r5", "r23"), ("li", "r6", 0x800), ("bl", "readat"), ("cmpwi", "r3", 0), ("bne", "hno"),
        ("lbz", "r9", 0, "r23"), ("cmpwi", "r9", 1), ("bne", "hno"),
        ("lwz", "r9", 1, "r23"), ("lis", "r10", 0x4344), ("ori", "r10", "r10", 0x3030), ("cmpw", "r9", "r10"),
        ("bne", "hno"),
        ("lbz", "r9", 5, "r23"), ("cmpwi", "r9", 0x31), ("bne", "hno"),
        ("li", "r3", 0), ("b", "hout"),
        ("label", "hno"), ("li", "r3", 1),
        ("label", "hout"),
        ("addi", "r1", "r1", 0x40), ("ld", "r0", 0x10, "r1"), ("mtlr", "r0"), ("blr",),
        # readat(r3 fd, r4 offset, r5 buffer, r6 size) -> r3 = 0 when all `size` bytes were read
        ("label", "readat"),
        ("mflr", "r0"), ("std", "r0", 0x10, "r1"), ("stdu", "r1", -0x60, "r1"),
        ("std", "r3", 0x40, "r1"), ("std", "r5", 0x48, "r1"), ("std", "r6", 0x50, "r1"),
        ("li", "r5", 0), ("addi", "r6", "r1", 0x38), ("bl", LSEEK), ("cmpwi", "r3", 0), ("bne", "rfail"),
        ("ld", "r3", 0x40, "r1"), ("ld", "r4", 0x48, "r1"), ("ld", "r5", 0x50, "r1"), ("addi", "r6", "r1", 0x38),
        ("bl", READ), ("cmpwi", "r3", 0), ("bne", "rfail"),
        ("ld", "r9", 0x38, "r1"), ("ld", "r10", 0x50, "r1"), ("cmpw", "r9", "r10"), ("bne", "rfail"),
        ("li", "r3", 0), ("b", "rout"),
        ("label", "rfail"), ("li", "r3", 1),
        ("label", "rout"),
        ("addi", "r1", "r1", 0x60), ("ld", "r0", 0x10, "r1"), ("mtlr", "r0"), ("blr",),
    ]
    return p
