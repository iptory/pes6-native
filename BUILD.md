# Build

This document describes a source build on Windows x64. The build creates
derivative code from your own game files. Do not commit the generated output,
the extracted game, or a compiled executable.

## Tested toolchain

- Windows 10 19045 x64
- Visual Studio 2022 17.12 with the Windows 10/11 SDK
- Microsoft Visual C++ 2015-2022 Redistributable (x64) for running the build
- LLVM/Clang 23.1.4 in `PATH`
- CMake 4.3.1, Ninja 1.13
- Python 3.13

Clang, CMake, and Ninja must be available from the shell. The upstream SDK
preset selects `clang` and `clang++`.

## Package the Windows builder

From a Windows Git checkout with Python and PyInstaller installed:

```text
py -3 builder/package_builder.py --out C:\path\to\new-builder-output
```

The output directory must not already exist. It receives `PES6Builder.exe` and
license notices, which are also embedded in the executable. The executable
embeds the tracked project source and the ISO extractor; it contains no game
files or generated game code. Packaging reads the current working-tree contents
of tracked files, so review changes before publishing. The builder still
downloads the pinned SDK and its submodules.

The packaged builder does not require Python or `winget` on the user's PC.
It downloads hash-pinned portable Git, CMake, Ninja and LLVM archives when
required. Missing Microsoft C++ tools, Windows SDK and runtime components use
official signed Microsoft installers, with Windows administrator approval.
Tool detection is refreshed immediately.

All private source, SDK and portable tools live in a uniquely owned temporary
workspace under the chosen output root. The completed runtime is published as
`PES6` only after assembly; an occupied destination is refused. The workspace
is removed before the game or output folder opens, including on build failure
or cancellation. Shared Windows installations and user inputs are preserved.
Cancellation waits safely for a shared Microsoft component installation to
finish. A cleanup error reports its exact remaining path rather than claiming
that deletion succeeded.

The final game contains runtime files, imported game data, `pes6.toml`, a
launcher, icon and required license notices. `log_to_file = false` disables
persistent runtime logs by default. Estimated overall progress is distinct
from real byte/file/Ninja counters. The packaged installer can remove its own
unchanged download after successful completion; it never deletes the source
Python interpreter.

## 1. Clone and patch ReXGlue

The commands below assume the SDK is cloned inside the repository root.
The pinned revision is:

```text
c94f5ebdcb3c9d1a460ca48e04f9758448f8d518
```

```bash
git clone https://github.com/rexglue/rexglue-sdk.git
cd rexglue-sdk
git checkout c94f5ebdcb3c9d1a460ca48e04f9758448f8d518
git submodule update --init --recursive
git apply ../patches/rexglue-sdk.patch
python ../tools/fix_libmspack_symlinks.py .
```

The symlink fix is required on Windows checkouts where Git writes libmspack's
relative symlinks as text files. It replaces those stubs with copies of their
targets.

## 2. Write local presets

From the repository root:

```bash
python tools/configure_windows.py
```

The helper finds `clang.exe`, `clang++.exe`, and the newest Windows SDK
`rc.exe`, then writes `rexglue-sdk/CMakeUserPresets.json` and
`project/CMakeUserPresets.json`. Both files are ignored by Git. Re-run with
`--force` after changing tool paths.

## 3. Build and install the SDK

```bash
cd rexglue-sdk
cmake --preset win-amd64-local
cmake --build --preset win-amd64-local-release --target install
cd ..
```

The install goes to `rexglue-sdk/out/install/win-amd64-local`. It contains
`bin/rexglue.exe`, `rexruntime.dll`, and `rexgpu-xenos.dll`.

## 4. Extract your game data

From the repository root:

```bash
python tools/xdvdfs_extract.py PES6.iso game
```

The extractor finds the XDVDFS partition from its volume descriptor instead of
assuming a fixed partition offset. It writes the game files to `game/`. The
game directory is ignored by Git.

The executable can also perform this extraction at first run. The Python tool
is useful for preparing a build because code generation needs `default.xex`
before the game starts.

## 5. Generate the project

The checked-in `project/pes6_manifest.toml` expects the game at `../game`.
It also includes the function and switch-table inputs in `project/`.

```bash
cd project
../rexglue-sdk/out/install/win-amd64-local/bin/rexglue.exe \
    codegen pes6_manifest.toml --ignore-stamp
```

Codegen writes `project/generated/`. It is ignored by Git. The generated code
is a derivative work of the game and must not be published.

## 6. Build and run

```bash
cmake --preset win-amd64-release-local
cmake --build --preset win-amd64-release-local
```

Run:

```text
project/out/build/win-amd64-release-local/pes6.exe
```

The executable defaults to a `game` directory next to itself. On first launch,
the setup dialog can select your extracted game folder or your ISO. The chosen
path is saved in `%LOCALAPPDATA%\PES6Native\game_path.txt`.

For a direct development launch:

```text
pes6.exe --game_data_root=C:/path/to/game
```

The first-run setup also accepts `PES6_SETUP_FOLDER` and `PES6_SETUP_ISO` for
automated testing.

## Repository hygiene

Run these before committing:

```bash
python -m compileall -q tools
python -m unittest discover -s tools -p "test_*.py"
python tools/check_public_tree.py
```

The check rejects game files, extracted data, generated translation units,
compiled products, and local analysis tools from the index.

## Troubleshooting

- If CMake cannot find ReXGlue, re-run `tools/configure_windows.py --force`.
- If CMake cannot find `clang` or `rc.exe`, install LLVM and the Windows SDK,
  then re-run the preset helper.
- If `rexglue` cannot find the XEX, check `game_root` and `file_path` in
  `project/pes6_manifest.toml`, and confirm that `game/default.xex` exists.
- If the game opens the setup dialog, select the extracted `game` folder, or
  pass `--game_data_root`.
- If a game-data file is missing, extract the ISO again. Do not copy a game
  directory into a Git checkout.
