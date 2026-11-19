# ps1_netemu

A patched `ps1_netemu.self` (the PS3's PS1 emulator) and an installer package for it. The package is
`ps1_netemu_v<version>.pkg`, where `<version>` is the release tag without the `v` (file `VERSION`); what changed in each release is in `CHANGELOG.md`.

## Features

| | Feature | In one line |
|---|---|---|
| 1 | **Overscan 4:3, DuckStation style** | The lines the game declares, clipped to 16 to 248 and stretched over the frame: no black bands, nothing adjusts while a game runs. |
| 2 | **Automatic analog mode** | Switches on when a game talks DualShock; the pad starts digital and digital-only games stay digital. |
| 3 | **Vibration** | The motors get exactly what the game commands. |
| 4 | **One memory card per game** | Named after the game's serial, created blank when missing, shown in slot 1 and registered with the system. |
| 5 | **Multi-disc games share a card** | Disc 2, 3, ... use the card of disc 1. |
| 6 | **Slot 2 to slot 1 fallback** | A PS1 card in slot 2 moves to slot 1 when slot 1 has none (PS2 cards never count). |
| 7 | **Safe installer** | Backs up the original, verifies every write by hash, refuses unknown builds, restores with one button. |
| 8 | **Sticks as buttons** | In digital mode the left analog stick presses the d-pad and the right stick presses L1 (left) and R1 (right). |
| 9 | **Full Screen goes edge to edge** | "Screen Mode: Full Screen" stretches the picture over 100% of the 16:9 width. |
| 11 | **Parasite Eve: d-pad as left stick in analog mode** | In Parasite Eve (USA, both discs), while the pad is in analog mode the d-pad pushes the left stick all the way, so it walks Aya like the stick. |
| 10 | **No dithering** | The ordered dither the PS1 GPU applies to shaded polygons is gone: gradients are smooth on a modern TV. |

Confirmed on a console (Castlevania: Symphony of the Night and Chrono Cross, NTSC, webMAN MOD 1.47.48): features
1, 2, 3, 4 and the slot 1 display of 4 (the Assign Slots menu shows `SLUS-01041.VM1` in slot 1 and `PS1.VM1` in slot 2).
Not tested: PAL and 480i modes (they keep the stock behavior) and features 5 and 6 on a console (they run
in a PowerPC interpreter against the real emulator code).

### 1. Overscan 4:3, DuckStation style

DuckStation shows the lines the game declares with `GP1` (`Y1` to `Y2`) filling the frame and never adjusts anything
while a game runs. Here the quad that shows the picture takes the declared range clipped to the video lines 16 to
248 and stretches it over the frame (the 8 last active lines are the overscan games leave black, as in the menu of
Megaman X5, where the picture ends at line 248): a game that declares less (Xenogears, lines 26 to 242) has no black lines at the edges. `cave_overscan.py` runs a verbatim copy of the stock
formula's tail, so everything the rest of the display handler reads (the texture span in `f1`, the width in `f3`, the
interlace compare) is exactly what the stock code leaves, and then replaces only the quad's top and bottom:
`2*(Y1 - first)/span - 1` and `2*(Y2 - first)/span - 1`, where `first` is `max(Y1, 16)` and `span` is
`min(Y2, 248) - first`. The result depends only on what the game declares, so nothing adjusts while you play. The width
is the same in every game: the emulator derived the left and right ends of the quad from the horizontal range each game
declares (GP1), so a game declaring a narrower range (Final Fantasy VII) came out narrower than one declaring the
nominal range (Megaman X5), measured with tape on the TV. The two ends are now the stock nominal ones, +-640/720 of the 4:3
window, for every game and every width mode (`0x1158ec`, `0x1158f4`, `0x115b48`). PAL keeps the stock formula.

Two earlier designs were removed. v2.08 to v2.13 cropped the picture to the draw area (`GP0 E3/E4`) with a remembered
window: it adjusted in real time and cut Xenogears and Final Fantasy VII battles. v2.14 first anchored the picture to
the game's first line and stretched it over the frame: it zoomed the declared lines and moved the picture. v2.14 then showed lines 16 to 256 one to one, which left black bands
that DuckStation does not show.

### 2. Automatic analog mode

A game that enters DualShock configuration (command `0x43`, data byte 1) supports analog sticks. The hook at
`0xd6b7c` jumps to `cave_analog.py`, which sets the pad's analog flag (record + `0x9c`). The pad starts digital
(setting 28 reads 0); the game's own `0x44` (set mode) still has the last word, and the menu toggle works both
ways. Digital-only games such as Castlevania never send `0x43` and stay digital. Two earlier approaches broke
Castlevania and were dropped: analog forced at boot, and analog whenever a game clocked extra pad bytes.

### 3. Vibration

Three words in the actuator function (`0xeb2d8..0xeb2e0`) replace the stock chain (an internal block flag at
`X + 0x84` that the pad thread resets every cycle, and a strength curve built from settings 50 and 51) with a
direct pass: small motor = what the game sent (on/off), large motor = what it sent (0 to 255). The same words now also
clear the switch at `X + 0x84` on every update and the pad thread (`0xec000`) no longer sets it, so the emulator's own
vibration switch stays on.

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

`ps1_netemu_v<version>.pkg` installs the app **PS1 Emulator Patch** (English). It saves the original to
`/dev_hdd0/ps1_netemu_stock.self` first, checks every write by hash, refuses any `ps1_netemu.self` that is not the
one build the patch was made for (SHA-256 `27d4121e...ebdd5d`), restores the original with Triangle, and warns on
screen when the patch is installed without a backup.

### 8. Left stick as d-pad, right stick as L1 and R1

The left stick, not the right one, on purpose: the first version read the right stick and the owner asked for the left one.

The pad thread stores the button halfword (active low) at `0xec16c`, with the PS3 left stick (a halfword per axis,
128 at rest) in the copy it reads from. The hook jumps to `cave_dpad.py`, which, only while the pad is in digital
mode (the analog flag the protocol handler tests, `+0x9c` of the pad record), clears the UP / RIGHT / DOWN / LEFT bit
when the stick passes `0x40` or `0xC0`. In analog mode nothing changes: the game reads the sticks itself. Pads 0 and 1
are told apart; a multitap pad 2 or 3 reads the flag of pad 1.

### 12. Parasite Eve: the d-pad is the left stick in analog mode

The one exception to "in analog mode nothing changes". Parasite Eve (USA) walks Aya with the left stick when the pad
is in analog mode and ignored the d-pad for that. The same hook (`cave_dpad.py`, `PARASITE_EVE`) compares the card
name of the game (`0x1be780`, `SLUS-00662.VM1` for disc 1 and `SLUS-00668.VM1` for disc 2) and, only for those, sets
the left stick halfwords of the pad data copy to 0 (left, up) or 255 (right, down) when the matching d-pad bit is
pressed, leaving the other axis as it is. The d-pad bits are still sent, so the menus keep working. Any other game, and
Parasite Eve while the pad is in digital mode, behave as before. Confirmed on the console by the owner.

## Install

1. Copy `ps1_netemu_v<version>.pkg` to the console (USB drive or `/dev_hdd0/packages`) and install it from the XMB
   (Game > Package Manager > Install Package Files).
2. Open **PS1 Emulator Patch**. Press **Cross** (or Circle with the Japanese layout) to install, **Triangle** to
   restore the original, the other face button to exit. A progress bar with the current stage and a percentage
   shows while it works; do not turn off the console until it ends.
3. Close the game and open it again: the emulator reads its file at launch.

The backup is only made when the app finds the original on the console. If the patched file was put there by
another route first, there is no backup and **Triangle** answers "no backup": copy the original (SHA-256
`27d4121e...`) to `/dev_hdd0/ps1_netemu_stock.self` yourself. If a game fails to start, restore the original.

The `.self` is embedded in the package and is not offered as a separate download: a deliberate deviation from the
original goal (a downloadable `ps1_netemu_v<version>.self`), made at the owner's request ("então não precisa ter o .self
no release latest"). To use the file directly, build it (below) and copy `ps1_netemu_v<version>.self` to
`/dev_flash/ps1emu/ps1_netemu.self`: the flash must be mounted writable (webMAN: open `http://<ps3-ip>/dev_blind`),
webMAN's FTP cannot rename over an existing file (delete the old one first), and a copy of the original must be kept.

## Limitations

- Vibration ignores the "Vibration Function" menu option (feature 3).
- PAL and 480i keep the stock overscan and were not tested.
- The left stick as d-pad is tested in a PowerPC interpreter against the real emulator code, not yet on a console.
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
| `cave_dpad.py` (also the Parasite Eve d-pad), `cave_analog.py`, `cave_memcard.py`, `cave_percard.py`, `cave_fullscreen.py`, `nodither.py` | New code placed in the 4 KB text segment extension (`0x1a9380`) |
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
python ps1_netemu_patch.py ps1_netemu.self ps1_netemu_v<version>.self
PS1_NETEMU_SELF=ps1_netemu.self python -m pytest -q tests
```

You need the stock `ps1_netemu.self` (APP, key revision 0x1C); it is not in this repository.

The right stick X axis (`+0xc` of the same copy) presses L1 when it goes left of `0x40` and R1 when it goes right of `0xC0`, again only while the pad is in digital mode.

### 9. Top and end of the picture, per game and adjusted by you

Some games draw their own black bars inside the picture (Final Fantasy VII at the top, Castlevania: Symphony of the Night at
the top and the end of the game), and no fixed window can remove them without cutting the top of a game that uses
every line (Megaman X5). Two things handle it:

* **Defaults per game.** The cave reads the card name of the game (`SLUS-00067.VM1`, kept at `0x1BE780` by feature 4) and,
  for the games measured on the owner's console, starts the picture lower: Final Fantasy VII (`SCUS-94163`, the card of
  disc 1 is shared by the three discs) 8 lines lower. Every other game keeps lines 16 to 248. Castlevania SotN needs 20 lines
  lower and 4 earlier while it plays, but its menus use every line and were cut, so it has no default yet (use the adjust).
* **Your adjust on top.** Hold **SELECT** and press **UP** or **DOWN** to start the picture 2 lines lower or higher (up
  to 24), **RIGHT** or **LEFT** to end it 2 lines earlier or later (up to 16). A press moves it once, nothing moves by
  itself, and it returns to 0 when the emulator starts again. Only the first pad reads it.

To add a game, hold SELECT and press UP until its bar is gone, count the presses and add `serial: (2 * presses, ...)` to `GAMES`
in `cave_overscan.py` (the serial is the name of its card in `/dev_hdd0/savedata/vmc`).


### 9. Full Screen goes edge to edge

The emulator draws the picture quad with its horizontal ends kept in the render state (`r20 + 0x1ac` left, `+0x1b0` right,
nominal +-640/720 of half the frame). Which path draws it depends on the up-convert mode the menu sets (`0x1620474`, read
at `0x114ddc`):

  * **Normal** (0) multiplies the ends by `(height / width) * 1.4668`: a 4:3 window inside the 16:9 frame.
  * **Full Screen** (any other value) used the nominal ends as they were, so the picture filled 88.9% of the frame width
    and left 5.5% of black on each side.

`cave_fullscreen.py` hooks the start of the second path (`0x115b60`) and multiplies both ends by 720/640, which takes
them to +-1: the picture fills 100% of the width (measured on the console in the frame buffer: 88.9% before the patch).
An intermediate build (v2.19) used the 1.1001 overscan of the Normal path and left 1.1% on each side, still a visible
stripe on the TV. The factor is a ratio of the frame, so it does not depend on the output resolution (720p or 1080p). It
acts only when the output is wider than 720, the same test the emulator makes for Normal; SD outputs keep the stock
picture. Normal never reaches the hook and the vertical ends are not touched.

### 11. No dithering

The PS1 GPU is emulated in software on the SPUs (`gpusoft.cc`, an SPU ELF embedded in the emulator at file offset
`0x171100`). Its draw routines pick one of two tables of 16 vectors by a flag: `XBGR_DITHER` (SPU `0x15170`, the 4x4 matrix
of channel offsets of the PS1, eight distinct values from -4 to 3) or `XBGR_NO_DITHER` (`0x15270`, zeros). Both are
filled once at start by the static initialiser at SPU `0x1018`. `nodither.py` changes the 14 stores that put a non-zero
offset into `XBGR_DITHER` so they store `$5`, the zero vector the initialiser makes with `il $5,0`: both tables become
the same zeros and no primitive is dithered. The RSX switch (`SetDitherEnable`, `0x28244`, method `0x0304`, always called
with a constant) is forced to 0 too; it only matters for targets with fewer than 8 bits per channel.

The tests run the real initialiser in a small SPU interpreter (`tests/test_nodither.py`) and check that after the patch
`XBGR_DITHER` equals `XBGR_NO_DITHER`, and that no store into the table was missed. It was not measured live: the SPU
program is loaded when the emulator starts, so the effect shows only after installing the package and reopening a game.

### 10. Screen mode and Smoothing of the emulator menu: not controllable here

The menu of the emulator has two options, "Screen mode" and "Smoothing". Their effect on the geometry is described above,
but the picture the overscan cave builds still breaks when they change. They cannot be forced from the emulator code: with Smoothing switched on, none of the 61 settings of the emulator (0 to
60, read through PS3MAPI at `base + 0x14 + 4 * index`, base from TOC slot `-0x7e94`) changed, so the two options live
outside that table, like the vibration option of feature 3 (the system menu keeps them). Keep them at Normal and Disabled.
An earlier build forced bits 0 and 1 of setting 49, which the display handler reads; it was removed because it never
controlled those options.

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
tools/dist.sh       # ../dist/ps1_netemu_v<version>.pkg, ps1_netemu_v<version>.self, SHA256SUMS (needs a CHANGELOG.md section for the version)
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

## Contributing

`main` is protected: every change goes through a pull request and needs one approving review. The owner can merge a pull
request without that approval (a bypass limited to pull requests), but nobody can push to `main` directly, delete it or
force-push it. Run the tests before opening the pull request:

```
PS1_NETEMU_SELF=<stock ps1_netemu.self> python -m pytest
installer/tests/run.sh
```

Bugs and ideas go in the [issues](https://github.com/orenatobr/ps1-netemu-v2/issues). The stock `ps1_netemu.self` is not
in this repository: you need your own copy from a console (SHA-256 `27d4121e...ebdd5d`).

## Release

1. Put the new version in `VERSION` and its notes in a `## v<version>` section at the top of `CHANGELOG.md`.
2. Build and package as above (`make pkg`, `tools/dist.sh`).
3. `gh release create v<version> ../dist/* --target main --latest --title v<version> --notes-file <(tools/release_notes.sh)`
