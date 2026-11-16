# ps1_netemu v2.00

A patched `ps1_netemu.self` (the PS3's PS1 emulator) with three changes:

1. **Analog mode switches on, and the motors get what the game commands.** A game that enters DualShock
   configuration (command `0x43`, data byte 1) supports analog sticks and motors. The hook at `0xd6b7c` jumps to new
   code (`cave_analog.py`) that sets the pad's analog flag (record + 0x9c) and redoes the displaced stock
   instructions. The pad starts digital (setting 28 reads 0), the game's own `0x44` (set mode) and `0x4d` (motor
   mapping) still have the last word, and the menu toggle works both ways. For vibration, three words in the actuator
   function (`0xeb2d8..0xeb2e0`) replace the stock chain (an internal block flag at `X + 0x84` that the pad thread resets
   every cycle, and a strength curve built from settings 50 and 51) with a direct pass: small motor = what the game sent
   (on/off), large motor = what it sent (0..255). The "Vibration Function" menu option could not be located in memory
   (toggling it changed no setting, pad record or pad state), so vibration no longer depends on it. Digital-only games
   such as Castlevania never send `0x43` and stay digital. Two earlier approaches broke Castlevania and were dropped:
   analog forced at boot, and analog whenever a game clocked extra pad bytes. Checked in a PowerPC interpreter against the
   stock code; analog was confirmed on a console, the vibration pass has not run on one yet.
2. **DuckStation-style vertical overscan, cropped to the area the game really draws.** The stock formula shows a
   224-line window (video lines 24 to 248) of the display the game declares. On top of it, new code in the text
   segment extension reads the GPU packet buffer for the game's draw area (`GP0 E3/E4`, never decoded on the CPU side)
   and crops the picture to it, so Castlevania: Symphony of the Night, which draws only lines 20 to 226 of its 240,
   loses its black band. Half of the extra zoom goes to the width, so the distortion is shared (about 4% each way
   for that game). The window grows at once and shrinks only after 24 display commands in a row ask for the same smaller
   one, and stays as it is when a frame has no usable area, so scenes that alternate areas (the game's intro) do
   not make the picture jump. PAL, 480i and games without a usable draw area keep the stock behavior. The width is the
   emulator's own (an earlier version widened it, which was not requested, and it was reverted).
3. **One memory card per game, named after the game's serial, in slot 1.** A hook at `0xb3b90` (inside `xMcInsert`)
   first calls `percard` (`cave_percard.py`), which opens the disc image whose path the emulator keeps in a global
   buffer (copied from `argv[5]`; webMAN starts the emulator without it, so the path is then read from webMAN's own
   record of the game it just launched, `/dev_hdd0/tmp/wmtmp/last_game.txt`), finds the ISO9660 volume descriptor in any of three layouts (raw 2352-byte
   sectors with a 24 or 16 byte header, or 2048-byte sectors), reads `SYSTEM.CNF`, takes the executable name after
   `cdrom:` (`SLUS_006.62;1`) and turns it into the serial `SLUS-00662` (underscore to hyphen, dot dropped, upper
   case); the card is handed to the slots as `SLUS-00662.VM1`, with the extension, the form slot 2 uses (`PS1.VM1`) and the
   one the Assign Slots menu matches against its list of cards. It opens `/dev_hdd0/savedata/vmc/SLUS-00662.VM1`; if the file does not exist it writes a blank formatted
   128 KB card (the same layout as any PS1 card: `MC` header, 15 free directory frames, 20 empty broken-sector frames,
   `0xFF` filler frames, empty blocks); an existing card is only opened and closed, never rewritten. That card
   becomes slot 1 (inserted, and registered with the system through `cellSysutilGameDataAssignVmc(0, name)`, which is what
   the *Assign Slots* menu also does); slot 2 is untouched. If the serial cannot be found (no disc path, unknown format, no `BOOT` line,
   a failed open or write) the original card logic runs, and so does the earlier rule: a PS1 card in slot 2 moves to
   slot 1 when slot 1 has no PS1 card (`.VM1` file check, PS2 cards never count). The discs of any multi-disc game share one card:
   when the image path has `(Disc 2)` to `(Disc 9)` (also `[Disc 2]` and `(Disc 2 of 3)`), the code reads the serial
   from the `(Disc 1)` image with the same name instead, so every disc of the set uses the card named after disc 1.
   Nothing is specific to one title: the serial comes from each disc's `SYSTEM.CNF`, and the grouping from the file
   name, so it works for any game named in the usual Redump style (Final Fantasy VII, Metal Gear Solid, Xenogears,
   Vagrant Story and Chrono Cross are in the tests). Without that `(Disc 1)` file the disc's own serial is used, and
   other disc naming (`CD2`, `Disc2`) gets one card per disc. The earlier rule (a PS1 card in slot 2 moves to slot 1
   when slot 1 has none) stays as the fallback. Saves kept in the old shared `PS1.VM1` stay there: assign it to slot 2
   in the emulator menu to copy them into a per-game card. 99 tests run the real emulator code in a PowerPC
   interpreter with a simulated file system and synthetic discs; it has not run on a console yet.

## Known limitations

- **(Fixed in v2.08, was open until v2.07) Castlevania: Symphony of the Night kept a thin black band at the top.** Static analysis of the draw path
  (`0x1152a0` and `0x115c40`, shared by every game) shows the quad is correct: the vertices are
  the `+0x1ac..+0x1b8` floats this patch computes (they overshoot the screen by 7% for that game), and
  the texture coordinates span the whole declared display area (`+0x1bc`/`+0x1c0` = 253 x 238 texels).
  So the band is not geometry. It is black pixels inside the area the game declares. Removing it needs a
  per-frame scan of the frame content, which is not implemented, because the frame lives in RSX memory
  that only the emulator can read. Games that fill their declared area (Legend of Legaia, Chrono Cross) are fine.
- **Memory card in slot 1 (not implemented).** The emulator's `main` receives the cards as arguments
  (`argv[1]` = slot 1, `argv[2]` = slot 2, NULL when absent) and calls `xMcInsert` (`0xb3a..`). The rule is not as simple as
  "slot 1 empty and slot 2 has a card": the emulator shares code with PS2 and also knows `.VM2` cards, so the card must be
  a PS1 card (`.VM1`) before it is moved. The move itself would reuse the path at `0xb4124` when slot 1 comes empty
  (`0xb4148`), but it also needs the null and empty-string checks on `argv[2]` plus a card-type check, and the binary has no safe free
  space: its zero runs are ELF header fields and data tables, and there are no alignment `nop`s. The in-game
  "Assign Slots" menu does the same by hand.
- **Analog with vibration: handled by the stock emulator, not patched.** The pad command table at `0xd5328`
  (commands `0x40` to `0x4f`) has handlers for every DualShock command a game can send: `0x43` enter
  configuration (`0xd5de0`), `0x44` set analog mode (`0xd5908`), `0x45` to `0x4a`, `0x4c`, and `0x4d` rumble
  mapping (`0xd59fc`); only `0x40`, `0x41`, `0x4b`, `0x4e` and `0x4f` fall to the default. So a game that
  supports analog switches the pad to analog and enables rumble by itself, as on a console, and the emulator's
  "Vibration Function" setting applies to it. A game that never sends these commands cannot be told apart from a
  digital-only game, and two attempts to guess (analog forced at boot, analog when the game clocks extra bytes)
  broke Castlevania: Symphony of the Night, so neither is shipped.
- PAL, 480i modes and analog-aware games are untested.

## Install

### Option 1: the package (recommended)

1. Copy `ps1_netemu_v2.00.pkg` to the console (USB drive or `/dev_hdd0/packages`) and install it
   from the XMB (Game > Package Manager > Install Package Files).
2. Open **PS1 Emulator Patch**. Press **Cross** (or Circle with the Japanese layout) to install,
   **Triangle** to restore the original, the other face button to exit.

The app saves the original to `/dev_hdd0/ps1_netemu_stock.self` first and checks every write by
hash. It refuses any `ps1_netemu.self` that is not the one build the patch was made for
(SHA-256 `27d4121e...ebdd5d`), so it never overwrites an unknown file.

The backup is only made when the app finds the original on the console. If the patched file
was put there by another route first, there is no backup and **Triangle** answers "no backup":
copy the original (SHA-256 `27d4121e...`) to `/dev_hdd0/ps1_netemu_stock.self` yourself.
The `.self` is embedded in the package and is not offered as a separate download. This is a deliberate
deviation from the original goal (a downloadable `ps1_netemu_v2.00.self`), made at the owner's request (their words: "então não precisa ter o .self no release latest").
The app now shows this warning on screen when the patch is installed and no backup exists.

### Option 2: the file

The release ships only the package. Build the file yourself (see below) and copy `ps1_netemu_v2.00.self` to `/dev_flash/ps1emu/ps1_netemu.self`. The flash must be mounted
writable (webMAN: open `http://<ps3-ip>/dev_blind`) and webMAN's FTP cannot rename over an
existing file: delete the old one first. Keep a copy of the original.

## How the `.self` is made

The stock SELF container is kept: header, offsets and sizes do not move. Each segment is
decrypted with the public APP key for revision 0x1C, patched, recompressed to the **same stored
size** (zlib padded with empty blocks) and encrypted again. The section HMAC-SHA1s inside the
metadata and the ELF SHA-1 in the control info are recomputed. The ECDSA signature stays as it
was: this console does not check it, but it does check the hashes (it rejects a file whose
hashes are stale with error `80010017`).

The changed words are in `ps3mapi_patch.py` (`CODE`, `CONSTANTS`) and `ps1_netemu_patch.py`
The patcher checks the original bytes at every site and refuses anything else.

```
pip install -r requirements-dev.txt
python ps1_netemu_patch.py ps1_netemu.self ps1_netemu_v2.00.self
PS1_NETEMU_SELF=ps1_netemu.self python -m pytest -q tests
```

You need the stock `ps1_netemu.self` (APP, key revision 0x1C); it is not in this repository.

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

`ps3mapi_patch.py` writes the same changes into the running emulator through webMAN PS3MAPI.
Nothing on the console changes and it is undone when the game closes. It checks the stock bytes
before writing.

```
python ps3mapi_patch.py 192.168.1.85            # apply, game running
python ps3mapi_patch.py 192.168.1.85 --revert   # restore
python ps3mapi_patch.py 192.168.1.85 --watch    # patch every game you start
```

## What did not work

| Approach | Result |
|----------|--------|
| Unsigned SELF (fself), original control flags | loads, black screen (even with no patch) |
| fself with a minimal header | error `80010001` |
| Stock container, segments repacked, hashes left stale | error `80010017` |
| Stock container, segments repacked, hashes recomputed | works |

## Notes

- The patched binary is Sony's code with a few dozen words changed. Use it on your own console only; this
  repository is private for that reason, so downloads need the owner's login.
- Restore: the package app (Triangle), or copy the original back the same way you installed.
