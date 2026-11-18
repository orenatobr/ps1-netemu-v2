# ps1_netemu v2.00

A patched `ps1_netemu.self` (the PS3's PS1 emulator) and an installer package for it. The package is always
`ps1_netemu_v2.00.pkg` (patch version 2.00); the release tag counts the published builds (see `CHANGELOG.md`).

## Features

| | Feature | In one line |
|---|---|---|
| 1 | **Overscan 4:3, DuckStation style** | A fixed 240-line vertical window: no line is lost and nothing adjusts while a game runs. |
| 2 | **Automatic analog mode** | Switches on when a game talks DualShock; the pad starts digital and digital-only games stay digital. |
| 3 | **Vibration** | The motors get exactly what the game commands. |
| 4 | **One memory card per game** | Named after the game's serial, created blank when missing, shown in slot 1 and registered with the system. |
| 5 | **Multi-disc games share a card** | Disc 2, 3, ... use the card of disc 1. |
| 6 | **Slot 2 to slot 1 fallback** | A PS1 card in slot 2 moves to slot 1 when slot 1 has none (PS2 cards never count). |
| 7 | **Safe installer** | Backs up the original, verifies every write by hash, refuses unknown builds, restores with one button. |
| 8 | **Right stick as d-pad** | In digital mode the right analog stick presses the d-pad, so digital-only games can use either stick. |

Confirmed on a console (Castlevania: Symphony of the Night and Chrono Cross, NTSC, webMAN MOD 1.47.48): features
1, 2, 3, 4 and the slot 1 display of 4 (the Assign Slots menu shows `SLUS-01041.VM1` in slot 1 and `PS1.VM1` in slot 2).
Not tested: PAL and 480i modes (they keep the stock behavior) and features 5 and 6 on a console (they run
in a PowerPC interpreter against the real emulator code).

### 1. Overscan 4:3, DuckStation style

DuckStation shows a fixed vertical window and never changes it while a game runs; this does the same. The stock formula
showed 224 video lines (24 to 248) of the display the game declares, which cut the top and the bottom of games that use
all 240 lines. The patched formula (`ps3mapi_patch.py`, NTSC block at `0x115ac0` and the shared code up to `0x1158b0`)
shows the 240 lines from video line 16 (`s = 1.0`, `c1 = 16`): a game that declares 240 lines loses none, a game
that declares fewer (224 for Final Fantasy VII outside battle) is stretched over the frame, and a game that starts
after line 16 is anchored to its own first line so no black bar is left on top. The result depends only on the lines
the game declares (`GP1` display range), never on what it draws, so nothing adjusts while you play. The width is the
emulator's own: the image stays 4:3. PAL, 480i and the lines above 16 keep the stock behavior.

A black band the game draws itself (the bottom of Final Fantasy VII battles) stays, as it does in DuckStation. An
earlier version (v2.08 to v2.13) cropped the picture to the draw area (`GP0 E3/E4`) with a remembered window; it
adjusted the picture in real time and cut Xenogears and Final Fantasy VII battles, so it was removed.

### 2. Automatic analog mode

A game that enters DualShock configuration (command `0x43`, data byte 1) supports analog sticks. The hook at
`0xd6b7c` jumps to `cave_analog.py`, which sets the pad's analog flag (record + `0x9c`). The pad starts digital
(setting 28 reads 0); the game's own `0x44` (set mode) still has the last word, and the menu toggle works both
ways. Digital-only games such as Castlevania never send `0x43` and stay digital. Two earlier approaches broke
Castlevania and were dropped: analog forced at boot, and analog whenever a game clocked extra pad bytes.

### 3. Vibration

Three words in the actuator function (`0xeb2d8..0xeb2e0`) replace the stock chain (an internal block flag at
`X + 0x84` that the pad thread resets every cycle, and a strength curve built from settings 50 and 51) with a
direct pass: small motor = what the game sent (on/off), large motor = what it sent (0 to 255).

**This ignores the emulator menu option "Vibration Function".** That option could not be located in memory
(toggling it changed no setting, pad record or pad state), so vibration is always on for games that ask for it.

### 4. One memory card per game

A hook at `0xb3b90` (inside `xMcInsert`) first calls `percard` (`cave_percard.py`):

1. It needs the disc image path. The emulator keeps it in a global buffer (from `argv[5]`), but webMAN starts the
   emulator without it ("boot from disc"), so the path is then read from webMAN's own record of the game it just
   launched, `/dev_hdd0/tmp/wmtmp/last_game.txt`.
2. It opens the image and finds the ISO9660 volume descriptor in any of three layouts (raw 2352-byte sectors with a
   24 or 16 byte header, or 2048-byte sectors), reads the root directory and `SYSTEM.CNF`, takes the executable
   name after `cdrom:` (`SLUS_006.62;1`) and turns it into the serial `SLUS-00662` (underscore to hyphen, dot
   dropped, upper case).
3. It opens `/dev_hdd0/savedata/vmc/SLUS-00662.VM1`. If the file does not exist it writes a blank formatted 128 KB
   card (the layout of any PS1 card: `MC` header, 15 free directory frames, 20 empty broken-sector frames, `0xFF`
   filler frames, empty blocks). An existing card is only opened and closed, never rewritten.
4. The card is handed to slot 1 as `SLUS-00662.VM1`, with the extension: that is the form slot 2 uses (`PS1.VM1`)
   and the one the *Assign Slots* menu matches against its list of cards. It is also registered with the system
   through `cellSysutilGameDataAssignVmc(0, name)`, which is what that menu does. Slot 2 is untouched.

If the serial cannot be found (no disc path, unknown format, no `BOOT` line, a failed open or write) the original
card logic runs, including the rule of feature 6.

Your saves kept in the old shared `PS1.VM1` do not appear in the per-game cards: assign `PS1.VM1` to slot 2 in the
emulator menu to copy them.

### 5. Multi-disc games

When the image path has `(Disc 2)` to `(Disc 9)` (also `[Disc 2]` and `(Disc 2 of 3)`), the code reads the serial
from the `(Disc 1)` image with the same name instead, so every disc of the set uses the card named after disc 1.
Nothing is specific to one title: the serial comes from each disc's `SYSTEM.CNF` and the grouping from the file
name, so it works for any game named in the usual Redump style (Final Fantasy VII, Metal Gear Solid, Xenogears,
Vagrant Story and Chrono Cross are in the tests). Without the `(Disc 1)` file the disc's own serial is used, and
other disc naming (`CD2`, `Disc2`) gets one card per disc.

### 6. Slot 2 to slot 1 fallback

The emulator receives the cards as `argv[1]` (slot 1) and `argv[2]` (slot 2). When the per-game card cannot be made
and slot 2 holds a PS1 card while slot 1 has none (or has something that is not a PS1 card), slot 2's card is
inserted into slot 1 through the emulator's own path at `0xb4124`. A card counts as PS1 when
`/dev_hdd0/savedata/vmc/<name>.VM1` can be opened, so a PS2 card (`.VM2`) or a missing file never counts.

### 7. Installer

`ps1_netemu_v2.00.pkg` installs the app **PS1 Emulator Patch** (English and Portuguese). It saves the original to
`/dev_hdd0/ps1_netemu_stock.self` first, checks every write by hash, refuses any `ps1_netemu.self` that is not the
one build the patch was made for (SHA-256 `27d4121e...ebdd5d`), restores the original with Triangle, and warns on
screen when the patch is installed without a backup.

### 8. Right stick as d-pad

The pad thread stores the button halfword (active low) at `0xec16c`, with the PS3 right stick (a halfword per axis,
128 at rest) in the copy it reads from. The hook jumps to `cave_dpad.py`, which, only while the pad is in digital
mode (the analog flag the protocol handler tests, `+0x9c` of the pad record), clears the UP / RIGHT / DOWN / LEFT bit
when the stick passes `0x40` or `0xC0`. In analog mode nothing changes: the game reads the sticks itself. Pads 0 and 1
are told apart; a multitap pad 2 or 3 reads the flag of pad 1.

## Install

1. Copy `ps1_netemu_v2.00.pkg` to the console (USB drive or `/dev_hdd0/packages`) and install it from the XMB
   (Game > Package Manager > Install Package Files).
2. Open **PS1 Emulator Patch**. Press **Cross** (or Circle with the Japanese layout) to install, **Triangle** to
   restore the original, the other face button to exit.
3. Close the game and open it again: the emulator reads its file at launch.

The backup is only made when the app finds the original on the console. If the patched file was put there by
another route first, there is no backup and **Triangle** answers "no backup": copy the original (SHA-256
`27d4121e...`) to `/dev_hdd0/ps1_netemu_stock.self` yourself. If a game fails to start, restore the original.

The `.self` is embedded in the package and is not offered as a separate download: a deliberate deviation from the
original goal (a downloadable `ps1_netemu_v2.00.self`), made at the owner's request ("então não precisa ter o .self
no release latest"). To use the file directly, build it (below) and copy `ps1_netemu_v2.00.self` to
`/dev_flash/ps1emu/ps1_netemu.self`: the flash must be mounted writable (webMAN: open `http://<ps3-ip>/dev_blind`),
webMAN's FTP cannot rename over an existing file (delete the old one first), and a copy of the original must be kept.

## Limitations

- Vibration ignores the "Vibration Function" menu option (feature 3).
- PAL and 480i keep the stock overscan and were not tested.
- The right stick as d-pad is tested in a PowerPC interpreter against the real emulator code, not yet on a console.
- The per-game card needs the disc path: it works under webMAN (`last_game.txt`) or when the emulator is given
  `argv[5]`. Another launcher would fall back to feature 6.
- Multi-disc grouping depends on the `(Disc N)` naming; `CD2` or `Disc2` get one card per disc.
- Analog is not forced at boot: a game that never talks DualShock cannot be told apart from a digital-only game.
- The patched binary is Sony's code with a few dozen words changed plus about 2.5 KB of new code. Use it on your
  own console only; this repository is private for that reason, so downloads need the owner's login.

## Code map

| File | What |
|---|---|
| `ps1_netemu_patch.py` | Unpacks the SELF, applies every patch, grows the text segment (`extend_text`), repacks, recomputes the hashes |
| `ps3mapi_patch.py` | The in-place word patches (`CODE`, `CONSTANTS`) and the live patcher |
| `cave_dpad.py`, `cave_analog.py`, `cave_memcard.py`, `cave_percard.py` | New code placed in the 4 KB text segment extension (`0x1a9380`) |
| `ppcasm.py` | Tiny PowerPC assembler with labels, only what the caves use |
| `tests/` | `test_patch.py` (container), `test_emulation.py` (runs the real emulator code in a PowerPC interpreter) |
| `installer/` | The PS3 app (PSL1GHT), its tests and the packaging tools |

## How the `.self` is made

The stock SELF container is kept: header, offsets and sizes do not move. Each segment is decrypted with the public
APP key for revision 0x1C, patched, recompressed to the **same stored size** (zlib padded with empty blocks) and
encrypted again. The section HMAC-SHA1s inside the metadata and the ELF SHA-1 in the control info are recomputed.
The ECDSA signature stays as it was: this console does not check it, but it does check the hashes (it rejects a file
whose hashes are stale with error `80010017`).

The text segment is extended by 4 KB (`p_filesz` and `p_memsz` of program header 0, in the ELF and in the SELF's
copy); the page was already mapped up to `0x1b0000`, so no address space is added. That space holds the new code.
The code cave is read-only memory, so state that changes at run time lives in free words of the data segment
(`0x1be780` for the card name).

The patcher checks the original bytes at every site and refuses anything else.

```
pip install -r requirements-dev.txt
python ps1_netemu_patch.py ps1_netemu.self ps1_netemu_v2.00.self
PS1_NETEMU_SELF=ps1_netemu.self python -m pytest -q tests
```

You need the stock `ps1_netemu.self` (APP, key revision 0x1C); it is not in this repository.

### Tests

The tests run the **real emulator code** (the original and the patched words) in a small PowerPC interpreter that
raises on any instruction it does not know, with simulated files and synthetic ISO9660 discs: three layouts, many
`BOOT` line spellings, blank card format, existing cards untouched, every failure falling back, every opened file
closed, several multi-disc titles, and register and stack preservation. The assembler refuses `addi` with `r0` as the
source (it reads the literal 0), a mistake the tests caught once.

## Build the package

Needs the [ps3dev](https://github.com/ps3dev/ps3dev) toolchain (`PS3DEV` and `PSL1GHT` set).

```
python ps1_netemu_patch.py ps1_netemu.self installer/payload/ps1_netemu_v2.bin
cd installer
make pkg            # PS1NetemuPatch.gnpdrm.pkg
tools/dist.sh       # ../dist/ps1_netemu_v2.00.pkg, ps1_netemu_v2.00.self, SHA256SUMS
tests/run.sh        # install/restore logic, runs on the Mac
tools/preview.sh    # renders the app screens to PNG on the Mac
```

`installer/` reuses the canvas, fonts and file helpers of
[HomeTidy](https://github.com/orenatobr/hometidy-ps3) and is licensed GPL-2.0 like it.

## Live patch (no install)

`ps3mapi_patch.py` writes the in-place changes (the overscan formulas and the vibration pass) into the running
emulator through webMAN PS3MAPI. Nothing on the console changes and it is undone when the game closes. It checks the
stock bytes before writing. The new code of features 1 (content crop), 2, 4, 5 and 6 lives in the text segment
extension, which only the installed `.self` has.

```
python ps3mapi_patch.py 192.168.1.85            # apply, game running
python ps3mapi_patch.py 192.168.1.85 --revert   # restore
python ps3mapi_patch.py 192.168.1.85 --watch    # patch every game you start
```

Careful with webMAN PS3MAPI: reads of 256 bytes work, but bursts of hundreds of requests make webMAN drop out, and
reading the RSX video memory (`0xC0xxxxxx`) is not possible through it.

## What did not work

| Approach | Result |
|----------|--------|
| Unsigned SELF (fself), original control flags | loads, black screen (even with no patch) |
| fself with a minimal header | error `80010001` |
| Stock container, segments repacked, hashes left stale | error `80010017` |
| Stock container, segments repacked, hashes recomputed | works |
| Analog forced at boot | breaks digital-only games (Castlevania) |
| Analog whenever a game clocks extra pad bytes | broke Castlevania |
| Widening the picture to fill the sides | not requested, reverted |
| Writing the vibration block flag (`X + 0x84`) | the pad thread resets it every cycle |
| Creating the card with `0x200` as the create flag | `0x200` is `O_TRUNC`; PS3 flags are octal, `O_CREAT` is `0x40` |
| Handing the card to slot 1 without the extension | the Assign Slots menu showed slot 1 empty |

## Restore

The package app (Triangle), or copy the original back the same way you installed.
