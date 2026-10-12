# ctw2re3: the Chinatown Wars city in re3

A first prototype of GTA: Chinatown Wars played like GTA 3: its city, converted from your own copy of the game, loaded
by re3 instead of Liberty City, so you can walk it in GTA 3's third-person camera with GTA 3's controls.

What you get: the Chinatown Wars city blocks with their textures, collision for the streets and buildings, flat
water where the sea and lakes are, and GTA 3's Claude standing at the CTW start point. What is not there yet: traffic,
pedestrians, missions, CTW's own vehicles and characters, real water (swimming, boats) and a radar map.

## What you need

- GTA 3 for PC (your own copy) and a build of re3 from this branch.
- GTA: Chinatown Wars for Android **4.4.243** (your own copy), ported with the
  [CTW-Native](https://github.com/official-kryo-to/ctw-native) Porter. Its output folder has a `data/` folder
  with `game.pak`, `dxt.bin`, `rom.toc` and `rom.wad`; that folder is what the converter reads.
- Python 3.8 or newer (no extra packages).

Neither game's files are part of this repository; the converter only runs on files you provide.

## 1. Convert the city

```text
python tools/ctw2re3/ctw2re3.py --ctw-data "C:\Games\GTA Chinatown Wars PC\data" --out "C:\Games\GTA3"
```

It writes into the GTA 3 folder (it does not touch any original file):

```text
models/ctw.img, models/ctw.dir      one model and texture dictionary per 120 x 120 city block
data/maps/ctw/ctw.ide, ctw.ipl      the block definitions and their placements
data/maps/ctw/ctw.col               collision, plus a ground plane from the CTW ground map
data/ctw.dat                        the level file the CTW build loads instead of gta3.dat
```

`--area c0,r0,c1,r1` converts only some blocks (the grid is 59 x 42), which is handy for a quick first try.
`--jobs N` sets the number of worker processes. The summary at the end lists any texture it could not read; those
show up magenta in game.

## 2. Build re3 with the CTW option

CMake: add `-DRE3_CTW_PROTOTYPE=ON`. Premake (Visual Studio): add `--with-ctw`, for example
`premake5 vs2019 --with-librw --with-ctw`. Everything the option changes is behind `#ifdef CTW_PROTOTYPE`; without
it, re3 is unchanged.

The option makes re3:

- load `data/ctw.dat` (the converted city) instead of `data/gta3.dat`;
- use a world grid that covers the CTW city (x -3680..3680, y -2720..2720) and more texture dictionary slots;
- skip `main.scm` and create the player at the CTW start point (`src/extras/ctw.cpp`), with no traffic or
  pedestrians since the city has no paths yet;
- leave out Liberty City's trains, planes, cull zones, water grid and radar map.

## 3. Play

Copy the re3 build into the GTA 3 folder as usual and start it, then pick Start Game. You start where CTW-Native starts the player.

## Tests

```text
python -m unittest discover -s tools/ctw2re3/tests -v
```

The tests build a tiny made-up data folder in the CTW layouts (`tests/make_fixture.py`) and convert it.
`tests/rwcheck.cpp` reads the output back through librw; build it against `vendor/librw` and point `RWCHECK` at it
to include that check.

## Credits and licence

The CTW file layouts come from CTW-Native's documentation of the game's formats, and the PVRTC decoder is a port of
its `DecodePVRTC`. CTW-Native is GPL-3.0, so this tool (everything under `tools/ctw2re3`) is GPL-3.0-only too.
