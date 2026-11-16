# ps1_netemu v2.00

A patched `ps1_netemu.self` (the PS3's PS1 emulator) with two changes:

1. **Analog mode switches on when a game enters DualShock configuration (command `0x43`).** The change is
   four words in the data-byte stage of that command (`0xd6b7c..0xd6b98`), checked in a PowerPC interpreter
   against the original code: entering configuration sets the pad's analog flag, leaving it changes nothing, and the
   game's own `0x44` (set mode) still has the last word. Digital-only games such as Castlevania never send `0x43`,
   so they stay digital, and the menu toggle still works both ways. Two earlier approaches broke Castlevania and
   were dropped: analog forced at boot, and analog whenever a game clocked extra pad bytes.
2. **Vertical overscan like DuckStation (NTSC), computed every frame from the display the game
   declares, with no per-game value.** The picture is shown in a 224-line window from video
   line 24, so the black bars above and below go away. The width is the original
   emulator's: the image stays 4:3 and is not widened (an earlier version widened it by removing the emulator's
   640/720 side margin, which was not requested, and it was reverted).
   - A game that starts its display after line 24 is anchored to its own start (no bar on top).
   - A game that declares more lines than the window is cropped like DuckStation does.
   - A game that declares fewer lines than the window is stretched down to the screen edge
     (no bar at the bottom), by up to a few percent.

Tested on a CFW console with webMAN MOD 1.47.48, Legend of Legaia and Chrono Cross (NTSC), using the
webMAN netemu option (the process is named `ps1_emu.self` but runs `ps1_netemu` code).
PAL is not changed and not validated.

## Known limitations

- **Castlevania: Symphony of the Night keeps a thin black band at the top.** Static analysis of the draw path
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
- **Memory card in slot 1: not implemented, with a concrete blocker.** The cards come as `argv[1]` (slot 1) and
  `argv[2]` (slot 2, NULL when absent) into `xMcInsert` (`0xb3a..`, insert helper `0xec5d0`). A correct move needs
  to know whether each card is a PS1 card (`.VM1`) or a PS2 card (`.VM2`, "Sony PS2 Memory Card Format", checked
  asynchronously at `0x141cb4`), which means a file check (`stat` on `/dev_hdd0/savedata/vmc/<name>.VM1`) plus the
  null and empty-string handling of `argv[2]`: roughly 45 instructions. The binary has no safe space for that (zero
  runs are ELF header fields and data tables, no alignment `nop`s). The only free memory is the 0x6c98-byte tail of
  the last text page (`0x1a9368` to `0x1b0000`), which would need the text segment's `p_filesz` and the SELF section
  sizes changed. That alters the container layout, cannot be proven without the console, and a mistake stops every game
  from starting, so it is not shipped. The in-game "Assign Slots" menu does the move by hand.
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
