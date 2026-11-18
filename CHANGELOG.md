# Changelog

Every release lists what changed here, newest first. The section of a version is its release notes: `installer/tools/release_notes.sh` prints it and `installer/tools/dist.sh` refuses to package a version that has none. The version lives in `VERSION`; the package and the app show it, the tag is `v<version>`.

## v2.22
- The repository is public and `main` is protected: every change goes through a pull request with one approving review, which the owner can bypass on a pull request only. The README has a Contributing section.
- The patch is the same as v2.21 (same patched emulator). The package and its icon carry the new version.
- Known issue, not fixed here: in Full Screen, games with pre-rendered backgrounds do not fill the frame ([#9](https://github.com/orenatobr/ps1-netemu-v2/issues/9)).

## v2.21
- The package icon (the thumbnail in the XMB) showed "v2.00" because it was a fixed PNG. It is now drawn at build time from the `VERSION` file with the same canvas as the app (`installer/tools/icon.sh`), so it always carries the release version.
- Dithering is removed. The emulator draws the PS1 picture with a software GPU on the SPUs, and it did dither: the draw routines pick one of two tables of 16 vectors by a flag, the 4x4 PS1 matrix of channel offsets (`XBGR_DITHER`) or zeros (`XBGR_NO_DITHER`). The 14 stores that fill the first one with non-zero offsets now store the zero vector, so both tables are the same zeros and no primitive is dithered, whatever the game asks for. The RSX dither switch (`SetDitherEnable`) is also forced to 0. On a modern TV the dither pattern shows as noise on gradients. The SPU part is covered by running the real table initialiser in a small SPU interpreter in the tests; it could not be measured live, because the SPU program is loaded when the emulator starts.

## v2.20
- "Screen Mode: Full Screen" now goes edge to edge. v2.19 left 1.1% of the width black on each side, which still showed as a stripe about a finger wide on the TV; the picture now fills 100% of the 16:9 width (the nominal ends grow by 720/640 to the frame edges). Still a ratio of the frame, so it holds at 720p and 1080p, and it still only applies to HD outputs. Normal is unchanged.

## v2.19
- "Screen Mode: Full Screen" now stretches the picture over the whole 16:9 frame. It used to fill 88.9% of the width and leave 5.5% of black on each side; it now fills 97.8% of its window, the same share the Normal mode (the 4:3 window) fills of its own, with 1.1% of margin on each side. Measured on the console in the frame buffer, with the game open in Full Screen. The change is a ratio of the frame, so it holds at 720p and 1080p, and it only applies to HD outputs like Normal does. Normal is unchanged.
- Code: `cave_fullscreen.py` hooks the Full Screen path of the display handler (`0x115b60`).

## v2.18
- The app is in English only (the Portuguese texts are gone).
- The app, the package and the file names carry the real release version instead of "2.00": the app title and "Patched (v...)" line, the package version in the XMB, `ps1_netemu_v<version>.pkg` and `.self`. It comes from the new `VERSION` file at build time.
- Install and Restore show a progress bar with the stage (checking the package, reading, writing, verifying) and a percentage, and keep redrawing while the flash is written. Before, the screen froze for the whole operation and looked hung. Same patch as v2.15.
- The bottom bar shows controller button icons (cross, triangle, circle, matching the confirm button of the console) instead of the letters "X", "Triangle" and "O".
- The changelog is now the source of each release's notes.

## v2.17
- The installer updates an older patched build (for example v2.14) when the original is safe in `/dev_hdd0/ps1_netemu_stock.self`. It used to show "Unknown build" and refuse, so no update could be installed over a previous patch. An unknown file without a valid backup is still refused. Same patch as v2.15.

## v2.16
- The installer works when `/dev_blind` is already mounted by webMAN: the mount fails then, and the app wrongly reported "needs custom firmware". It now checks that the emulator file is reachable and only unmounts what it mounted itself. Same patch as v2.15.

## v2.15
- Every game is drawn with the same width in the 4:3 window. The stock quad took its left and right ends from the horizontal range the game declares, so Final Fantasy VII came out narrower than Megaman X5 (measured on the TV). Both ends are now the nominal ones (+-640/720 of the window) whatever the game declares or the width mode (the stock 384 mode used 672/720): three instructions at `0x1158ec`, `0x1158f4` and `0x115b48`, no code cave.

## v2.14
- The emulator's vibration switch (`X + 0x84`) is kept on: the actuator clears it on every update and the pad thread no longer sets it.
- The menu options Screen mode and Smoothing cannot be forced from the emulator code (none of its 61 settings changed when Smoothing was switched on), so they stay with the system menu: keep Normal and Disabled.
- Final Fantasy VII starts with the picture 8 lines lower, by the serial of the game. SELECT + UP / DOWN moves the top by 2 lines (0 to 24) and SELECT + RIGHT / LEFT the end (0 to 16) for any game; nothing adjusts by itself and the adjust resets when the emulator starts. Castlevania SotN starts 20 lines lower and ends 4 earlier too, only while it plays (256 wide): its menus (384 wide) use every line.
- In digital mode the right stick X axis presses L1 (left) and R1 (right).
- The d-pad is pressed by the left analog stick (the right stick was the first version; the owner asked for the left one).
- Overscan shows the lines the game declares, clipped to the video lines 16 to 248 and stretched over the frame: no black bands (the Megaman X5 menu ends at line 248), its top is kept and nothing adjusts while a game runs. The formula keeps every register the display handler reads afterwards as the stock code leaves it. The content-aware crop and its real-time adjustment are removed.
- The left analog stick presses the d-pad while the pad is in digital mode, so digital-only games can use either stick.

## v2.13
Documentation only, same package as v2.12: the README lists every feature with its status, limits and code map.

## v2.12
Final build of this series. No code change from v2.11, documentation only: everything below is confirmed on a console.

## v2.11
- The per-game card reaches slot 1 in the form the Assign Slots menu matches: `SLUS-01041.VM1`, with the extension.

## v2.10
- Vibration: the motors get exactly what the game commands (ignores the menu option "Vibration Function", which could not be located in memory).
- One memory card per game named after its serial, created when missing, registered with the system as slot 1; later discs of a multi-disc game share the card of disc 1.

## v2.09
- Analog mode and vibration switch on when a game enters DualShock configuration.

## v2.08
- Overscan cropped to the area the game really draws, with a remembered window so alternating draw areas cannot make the picture jump.

## v2.07 and earlier
- PS1 card in slot 2 moves to slot 1 when slot 1 has none; DuckStation-style vertical overscan; installer package; code cave of 4 KB in the text segment.
