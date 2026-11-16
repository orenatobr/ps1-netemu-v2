# ps1_netemu v2.00

Patched build of the PS3 PS1 emulator (`ps1_netemu.self`) with two changes:

1. **Vertical overscan like DuckStation** (live patcher, NTSC). The picture is placed in
   a fixed window of video lines 24..248 instead of fitting the display range each
   game declares, so the black bars above and below are gone. The horizontal extent is
   untouched: the image stays 4:3 (pillarboxed on a 16:9 TV), it is not widened.
2. **Analog mode on by default.** Every pad starts as a DualShock in analog
   mode. "Switch Analog Mode" in the in-game menu still toggles it, and games
   that send their own pad configuration still override it.

The output is an unsigned SELF (fself), so it needs custom firmware.

## Install

Back up the stock file first, then copy the new one under the original name:

```
/dev_flash/ps1emu/ps1_netemu.self   <-  ps1_netemu_v2.00.self (renamed)
```

**Status: the `.self` does not work on hardware.** Even an unpatched fself repack
shows a black screen, and a minimal header fails with `80010001`: the console does
not give an unsigned SELF the same privileges as the signed original. Use the
live patcher below instead; it leaves the stock file untouched.

## Live patch (recommended)

With webMAN MOD (PS3MAPI), start a PS1 game that uses `ps1_netemu` (note: with the
webMAN netemu option the process is named `ps1_emu.self`), then:

```
python ps3mapi_patch.py 192.168.1.85            # apply
python ps3mapi_patch.py 192.168.1.85 --revert   # restore
```

The overscan is adaptive, with no per-game value: the picture is shown in a 224-line
window starting at video line 24 (DuckStation's NTSC overscan). If a game starts its
display before line 24 the extra lines are cropped, like DuckStation. If it starts
after line 24 (Legend of Legaia: line 28) the window is anchored to the game's own
start, so no black bar is left at the top; the overflow at the bottom is cropped.

It reads each patch site first and refuses to write unless the bytes are the stock
ps1_netemu words. Nothing is written to disk; closing the game undoes it.
The analog flag is written directly for the 8 pads because pad init already ran.

## What changed (5 instructions)

All addresses are virtual addresses in the decrypted ELF.

| VA | Original | New | Purpose |
|----|----------|-----|---------|
| `0xd3080` | `clrlwi r0,r0,31` | `li r0,1` | pad reset: analog flag = 1 |
| `0xd3288` | `clrlwi r0,r0,31` | `li r0,1` | pad slot reset loop: analog flag = 1 |
| `0xd4eac` | `clrlwi r0,r0,31` | `li r0,1` | pad init loop: analog flag = 1 |
| `0x115824` | `fsubs f12,f0,f13` | `fsubs f12,f0,f0` | quad top offset = 0 |
| `0x115874` | `fadds f13,f12,f1` | `fmr f13,f10` | quad bottom = full frame height |

### Analog

The per-pad record is 0xd8 bytes. The word at `+0x9c` selects the poll
response ID (`0x73` analog, `0x41` digital). Three places seeded it with
`config & 1`; they now seed it with 1.

### Stretch

The display command handler (function `0x113f0c`, geometry at `0x115780`)
places the picture using the GPU's vertical display range (Y1 to Y2) against a
nominal 240 (NTSC) or 288 (PAL) lines. A game that shows 224 or 256 lines gets
bars. The patches force the quad to span the whole frame height. The texture V
range (`f1`) is not touched, so the same source rows are sampled and only the
on-screen rectangle grows. Games whose range already equals the nominal height
look the same as before.

## Rebuild

You need the stock `ps1_netemu.self` (APP, key revision `0x1C`). It is not in
this repository.

```
pip install -r requirements-dev.txt
python ps1_netemu_patch.py ps1_netemu.self ps1_netemu_v2.00.self
PS1_NETEMU_SELF=ps1_netemu.self python -m pytest -q tests
```

The script decrypts the SELF with the public APP key for revision `0x1C`,
checks the original bytes at each patch site (it refuses to patch anything
unexpected), then wraps the ELF in an unsigned SELF that reuses the stock app
info and control info.

## Checksums

```
27d4121ecf8dddf83fa7f0117a454254bd6626c6f17e3412cdd9e24aa7ebdd5d  ps1_netemu.self (input)
7b994306ad1a984d729e97a939c7d9ac62f7d5823febb0bf58520777ab7b6507  ps1_netemu_v2.00.self
```

The binary is Sony's code with five instructions changed. Use it on your own
console only.

## Tested configuration

Legend of Legaia (USA), NTSC, PS1 ISO mounted by webMAN MOD 1.47.48 with the netemu option
(process `ps1_emu.self` running `ps1_netemu` code). Analog mode: confirmed on the TV. Overscan: geometry
measured in memory; the last top-edge adjustment is not yet confirmed visually. PAL: untouched and
unsupported. The repository is private, so downloads need the owner's login.
