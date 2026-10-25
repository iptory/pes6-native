# PES6 native Windows x64 recompilation

This repository is an unofficial ReXGlue project for the European Xbox 360
release of **Pro Evolution Soccer 6** (title `4B4E07DA`). ReXGlue translates
the game's PowerPC code to C++ and Clang compiles it to a Windows x64
executable. The resulting executable has no CPU emulator, interpreter, or JIT.

The repository contains the build tooling, the ReXGlue project inputs, and a
patch against a pinned ReXGlue revision. It does **not** contain the game,
game data, generated translation units, XEX decryption keys, or compiled
binaries.

Read [LEGAL.md](LEGAL.md) before you fork this repository or publish a build.
The safe distribution is source only.

## What you get after building

- Native x64 game code generated from your own PAL `default.xex`.
- ReXGlue's native D3D12 renderer on Windows, with a Vulkan backend available
  on platforms that ReXGlue supports.
- Xbox 360 kernel, XAM, input, filesystem, audio, and GPU compatibility code
  from the ReXGlue SDK.
- The original game's menus, modes, controls, saves, audio, and match flow.
- Persistent shader and pipeline storage. On the tested build, the cache held
  75 translated shaders and 132 graphics pipelines.
- A frame-latency waitable DXGI swap chain. The default queue depth is one
  frame, and the renderer falls back to a normal swap chain if DXGI rejects
  the flag.

The game's simulation runs at its original fixed 60 Hz tick. Pre-rendered WMV
movies are 30 fps source assets. See [VERIFICATION.md](VERIFICATION.md) for the
tested behavior and the remaining limits.

## Requirements

- Windows 10 or Windows 11, x64, with a Direct3D 12 GPU.
- A lawfully obtained PAL Xbox 360 copy of PES6. The tested disc image is the
  European release with title ID `4B4E07DA`.
- Visual Studio 2022 Build Tools with the Windows SDK.
- Microsoft Visual C++ 2015-2022 Redistributable (x64) on the target machine.
- LLVM/Clang, CMake 3.25 or newer, Ninja, and Python 3.11 or newer.
- `tools/configure_windows.py` writes local presets for Clang and `rc.exe`.
- The pinned ReXGlue SDK revision and the patch in `patches/`.

A complete build is described in [BUILD.md](BUILD.md).

## Repository layout

| Path | Purpose |
| --- | --- |
| `project/` | ReXGlue project inputs, CMake files, and native host code |
| `project/src/` | Windows setup, settings overlay, and data prefetch code |
| `patches/rexglue-sdk.patch` | Required changes to the pinned ReXGlue revision |
| `tools/xdvdfs_extract.py` | Extracts the XDVDFS partition from your ISO |
| `tools/fix_libmspack_symlinks.py` | Fixes libmspack symlink stubs on Windows |
| `tools/configure_windows.py` | Writes local CMake presets for Clang and `rc.exe` |
| `tools/check_public_tree.py` | Rejects game data and build output in Git |
| `LEGAL.md` | Distribution policy and legal notes |
| `THIRD_PARTY_NOTICES.md` | Upstream licenses and notices |

## Build summary

```bash
git clone https://github.com/rexglue/rexglue-sdk.git
cd rexglue-sdk
git checkout c94f5ebdcb3c9d1a460ca48e04f9758448f8d518
git submodule update --init --recursive
git apply ../patches/rexglue-sdk.patch
python ../tools/fix_libmspack_symlinks.py .
cd ..
python tools/configure_windows.py
cd rexglue-sdk
cmake --preset win-amd64-local
cmake --build --preset win-amd64-local-release --target install
cd ..
python tools/xdvdfs_extract.py PES6.iso game
cd project
../rexglue-sdk/out/install/win-amd64-local/bin/rexglue.exe codegen pes6_manifest.toml --ignore-stamp
cmake --preset win-amd64-release-local
cmake --build --preset win-amd64-release-local
```

Run `project/out/build/win-amd64-release-local/pes6.exe`. On first launch the
setup dialog can select the extracted `game` folder or your ISO. The selected
path is remembered under `%LOCALAPPDATA%\PES6Native\game_path.txt`.

Generated code and build output are ignored by Git. Keep them local.

## Controls

A keyboard and an XInput gamepad both work. The keyboard mapping is:

| Action | Player 1 | Player 2 |
| --- | --- | --- |
| Start | Enter | Y |
| A | Space | N |
| B | Quote/Backspace | M |
| X | L | B |
| Y | P | V |
| Left stick | W/A/S/D | T/F/G/H |
| Back | Z/Tab | J |

P2 is a second synthetic controller on guest user 1. Assign it to AWAY in
Player Settings for local multiplayer. Game text entry uses the Xbox 360
`XamShowKeyboardUI` path and includes a virtual QWERTY keyboard.

## Settings and saves

- `F5` opens the PES6 settings overlay.
- `F4` opens the complete ReXGlue cvar editor.
- `F3` opens frame diagnostics.

Settings cover VSync, resolution scale, 2x MSAA, anisotropic filtering,
presentation filtering, texture-cache limits, shader-cache persistence, data
prefetch, and waitable-swap-chain latency. F5 writes them to `pes6.toml` next to
the executable. Path values are escaped correctly, so they reload after a
restart. Closing the overlay also saves the current values.

`xam_signin_state` defaults to `2` so PES6 treats users 0-3, and any-user
queries, as signed in to Xbox Live. This enables profile-backed storage even
when the game checks a controller-specific user index. On boot, press Start, then confirm the
storage device once. The runtime selects its local HDD automatically. When the
game asks to overwrite System, Cup, League, formation, or Edit data, choose
**Yes**. PES6 often focuses **No** by default, so press Up and then A.

Saves are written under `%USERPROFILE%\Documents\pes6`. Historical Xbox Live
services are unavailable.

## Output resolution

The guest still renders its original 1280x720 framebuffer. The resolution
scale option multiplies that internal framebuffer, so 2x renders at 2560x1440
and 4x renders at 5120x2880 before presentation. It is real supersampling, not
a stretched 720p image, but it is not a native 1080p guest framebuffer. Use 2x
for 1080p output on a 1080p display, and 1x if the GPU cannot hold 60 fps.

The window title is set to **Pro Evolution Soccer 2006**. If a local
`title.ico` is placed next to the executable or in the game data folder, the
original title icon is applied. The icon is extracted from the user's own game
file and is not part of this repository.

## Development checks

```bash
python -m compileall -q tools
python -m unittest discover -s tools -p "test_*.py"
python tools/check_public_tree.py
```

## License

Original project code is MIT licensed. The ReXGlue patch is distributed under
the ReXGlue BSD 3-Clause license. Game data and generated code remain the
property of their respective owners. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

This project is not affiliated with or endorsed by Konami or Microsoft.
