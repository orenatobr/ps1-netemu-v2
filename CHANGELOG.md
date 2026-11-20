# Changelog

Every release lists what changed here, newest first. The section of a version is its release notes: `installer/tools/release_notes.sh` prints it and `installer/tools/dist.sh` refuses to package a version that has none. The version lives in `VERSION`; the package and the app show it, the tag is `v<version>`.

## v2.27
- Fixes Dino Crisis (black screen after the PS logo). The faster CD loading of v2.25 and v2.26 also divided the delays that time the commands (the start of a read, a seek). Dino Crisis then looped on the sectors 637 to 660 and never got past the logo, with the console still running. Only the tick of the CD controller (callback `0xdec48`, 225792 cycles) is divided now, so the data sectors still arrive 4 times faster and XA and CD-DA streams keep the pace of the disc. Found by reading the CD state of the running emulator through PS3MAPI (the sector stuck between 637 and 660) and putting the stock scheduler word back, then restricting the hook by callback: with it the game went on to the intro FMV (XA mode).
- Tests: the tick is divided only for the tick callback, the three command callbacks keep both delays in every mode, and the tick slot is checked against the stock ELF (it must resolve to `0xdec48`).

## v2.26
- Both answers are now inside the package. The v2.25 overclock only existed as a live script; it is now installed, off by default, and switched with the controller: **SELECT + R1** raises the overclock one step and **SELECT + L1** lowers it (off, 1.2x, 1.5x, 2x; a press, not a hold, moves one step). It is off every time the emulator starts, so no game changes unless you ask. The loading speedup of v2.25 (data sectors 4 times faster, FMV voices and CD music untouched) is unchanged and always on.
- How the overclock works: the interpreter takes `r7` CPU cycles (1) out of the budget of the next event for every MIPS instruction (`subf r28,r7,r28` at `0x106798`), and the timers, the video, the CD and the SPU run on the same cycle count. With the overclock on the hook (`cave_overclock.py`) keeps the cost in 1/256 of a cycle (`256 - 21 * level`, level 0, 2, 4 or 6) in free data words (`0x1be7e0`, `0x1be7e4`) and takes only the whole cycles out; with it off the cost is exactly the stock `r7`. A game that waits on the CPU can misbehave with it on, and the PPU interprets more instructions per second, so use the lowest step that helps.
- The `--overclock` option of `ps3mapi_patch.py` is gone (the package does it).
- The pad hook got two more SELECT combos, so the caves were moved inside the same extended text segment; nothing else changed in what they do.
- Checked in the PowerPC test interpreter: level 0 costs exactly the stock cycles for any cycles-per-instruction, levels 2, 4 and 6 cost 0.84, 0.67 and 0.51 of a cycle (fraction kept between instructions), the loop keeps its registers, SELECT + R1 and SELECT + L1 move the level one step with a stop at both ends, and a shoulder button alone does nothing. Not measured on the console: the loading time with the speedup and the overclock on a real game (the console was busy with another application and the v2.25 attempt had left its webMAN down for a while, it answers again).

## v2.25
- Faster loading for games that run from the HD. The emulator keeps the pace of a real disc: it schedules each data sector as an event 451584 CPU cycles after the previous one (75 sectors a second), or 225792 when the game set the double speed bit of the CD mode (150 a second). That pace is the whole loading time of a game that runs from the hard disk. The scheduler (`0x105e68`) now divides those two delays by 4, so data sectors arrive 4 times faster (a game at double speed reads like at 8x of a first generation drive). `cave_cdspeed.py` does it. The CD mode register is read from the CD controller state (TOC slot `-0x7c60`, `+0x2734`): when the XA-ADPCM bit (`0x40`) or the CD-DA bit (`0x01`) is set the delay is left alone, so the FMVs with voices and the music of CD tracks play at the pace of the disc, as in DuckStation's CD-ROM read speedup. Every other delay (command responses, timers, vertical blank) is untouched.
- Overclock, opt-in and live only in this release (v2.26 installs it with a controller switch). The emulator costs every MIPS instruction exactly 1 CPU cycle (`0x106798`: `subf r28,r7,r28`, `r7 = 1` from the parameter table at `0x105704`), and the timers, the video, the CD and the SPU all run on that same cycle count, so an instruction that costs less than a cycle speeds the CPU up while every hardware event keeps its proportion (what DuckStation's overclock does). `cave_overclock.py` keeps the cost in 1/256 of a cycle in a free data word and takes the whole cycles out of the budget. `python ps3mapi_patch.py <console> --overclock 1.5` writes it into the running emulator (1.0 to 4.0, slow writes one at a time); `--revert` takes it out; closing the game also does. Not installed on purpose: a game that busy-waits on the CPU can break, and the PPU has to interpret the extra instructions, so the factor is the owner's call per game. Not run on the console yet (its webMAN stopped answering during the test, see below).
- Checked by running the real emulator code in the PowerPC test interpreter (the delays 451584 and 225792 become a quarter with the CD mode of a data read, stay with the XA and CD-DA bits set, and any other delay stays), and the CD speedup was applied live in the emulator memory of Crash Bandicoot while it ran (the emulated clock kept real time, 31 to 34 million cycles a second, and the game stayed alive). The loading time was not timed on the console: the next launch (a `quit.ps3` followed at once by a `play.ps3`) reset the connection and the console's webMAN did not answer for the rest of the session (the console answered ping), so the A/B timing and the overclock were not tried on hardware.

## v2.24
- Full Screen: the 3D characters no longer land at the wrong place on pre-rendered backgrounds ([#9](https://github.com/orenatobr/ps1-netemu-v2/issues/9)). The owner's observation: the background texture is stretched in X, while the vertices of the characters are scaled by a different factor, so they end up in the wrong place of the picture (Chrono Cross). Found in the emulator code: only in its screen mode 2 (Full Screen) four places of the primitive set-up (entered from `0xc6560`, `0xc6904`, `0xc6c64` and `0xc4778`) replace an X quantity of the geometry by `x/2 + x/4`, three quarters of it, and the Normal mode does not. The four mode compares now test a value the mode never has, so every mode takes the unscaled path and the quad scales the whole picture the same way (the v2.20 edge to edge stretch is unchanged).
- Side effect to know: in Full Screen the 3D geometry is no longer narrowed by 3/4 before the stretch, so 3D games look stretched like 2D games, uniformly.
- Same package structure as v2.23; four words changed in the emulator code, nothing else. Checked by running the real emulator code in the PowerPC test interpreter (mode 2 scales by 3/4 in the stock build and not in this one) and applied live in the emulator memory while the owner watched Chrono Cross.
- Confirmed on the TV by the owner ("ficou 100%"): the same four words applied live in the emulator memory while Chrono Cross ran in Full Screen. The installed package was not played yet.

## v2.23
- Parasite Eve (USA, discs 1 and 2): in analog mode the d-pad now does what the left analog stick does. The game walks Aya with the stick in that mode and ignored the d-pad. The d-pad bits set the left stick to 0 or 255 on their axis (the other axis as it is) and are still sent, so the menus work. Only this game, matched by its card name (`SLUS-00662`, `SLUS-00668`); every other game and the digital mode are unchanged. Confirmed on the console.
- The d-pad code grew, so the overscan, analog and full screen caves moved up inside the same extended text segment (no change in what they do).
- Issue #9 (Full Screen with pre-rendered backgrounds) is not fixed in this release.

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
