# Changelog

The package is always `ps1_netemu_v2.00.pkg` (patch version 2.00); the release tag counts the published builds.

## v2.14
- The d-pad is pressed by the left analog stick (the right stick was the first version; the owner asked for the left one).
- Overscan is the DuckStation window: fixed video lines 24 to 248 stretched over the frame (`y = 2*s*(Y-24)/H - 1`, `s = 240/224`), no anchoring. The content-aware crop and its real-time adjustment are removed, so nothing adjusts while a game runs.
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
