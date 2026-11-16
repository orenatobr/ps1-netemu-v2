# ps1_netemu v2.00

A patched `ps1_netemu.self` (the PS3's PS1 emulator) with two changes:

1. **Analog mode on at boot** for every pad. The in-game "Switch Analog Mode" still toggles it,
   and games that send their own pad configuration still win.
2. **Vertical overscan like DuckStation (NTSC).** The picture is shown in a 224-line window from
   video line 24, so the black bars above and below go away. The width is untouched: the image
   stays 4:3 and is not widened to 16:9. If a game starts its display after line 24, the window
   is anchored to the game's own start, so no bar is left at the top.

Tested on a CFW console with webMAN MOD 1.47.48, Legend of Legaia (USA), NTSC, using the
webMAN netemu option (the process is named `ps1_emu.self` but runs `ps1_netemu` code).
PAL is not changed and not validated.

## Install

### Option 1: the package (recommended)

1. Copy `PS1NetemuPatch.pkg` to the console (USB drive or `/dev_hdd0/packages`) and install it
   from the XMB (Game > Package Manager > Install Package Files).
2. Open **PS1 Emulator Patch**. Press **Cross** (or Circle with the Japanese layout) to install,
   **Triangle** to restore the original, the other face button to exit.

The app saves the original to `/dev_hdd0/ps1_netemu_stock.self` first and checks every write by
hash. It refuses any `ps1_netemu.self` that is not the one build the patch was made for
(SHA-256 `27d4121e...ebdd5d`), so it never overwrites an unknown file.

### Option 2: the file

Copy `ps1_netemu_v2.00.self` to `/dev_flash/ps1emu/ps1_netemu.self`. The flash must be mounted
writable (webMAN: open `http://<ps3-ip>/dev_blind`) and webMAN's FTP cannot rename over an
existing file: delete the old one first. Keep a copy of the original.

## How the `.self` is made

The stock SELF container is kept: header, offsets and sizes do not move. Each segment is
decrypted with the public APP key for revision 0x1C, patched, recompressed to the **same stored
size** (zlib padded with empty blocks) and encrypted again. The section HMAC-SHA1s inside the
metadata and the ELF SHA-1 in the control info are recomputed. The ECDSA signature stays as it
was: this console does not check it, but it does check the hashes (it rejects a file whose
hashes are stale with error `80010017`).

The 20 changed words are in `ps3mapi_patch.py` (`CODE`, `CONSTANTS`) and `ps1_netemu_patch.py`
(`ANALOG`). The patcher checks the original bytes at every site and refuses anything else.

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
tools/dist.sh       # ../dist/PS1NetemuPatch.pkg, ps1_netemu_v2.00.self, SHA256SUMS
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

- The patched binary is Sony's code with 20 words changed. Use it on your own console only; this
  repository is private for that reason, so downloads need the owner's login.
- Restore: the package app (Triangle), or copy the original back the same way you installed.
