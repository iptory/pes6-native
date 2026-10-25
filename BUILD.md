# Build

This document describes a source build on Windows x64. The build creates
derivative code from your own game files. Do not commit the generated output,
the extracted game, or a compiled executable.

## Tested toolchain

- Windows 10 19045 x64
- Visual Studio 2022 17.12 with the Windows 10/11 SDK
- LLVM/Clang 23.1.4 in `PATH`
- CMake 4.3.1, Ninja 1.13
- Python 3.13

Clang, CMake, and Ninja must be available from the shell. The upstream SDK
preset selects `clang` and `clang++`.

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

## 2. Build and install the SDK

```bash
cmake --preset win-amd64
cmake --build --preset win-amd64-release --target install
```

The install goes to `rexglue-sdk/out/install/win-amd64`. It contains
`bin/rexglue.exe`, `rexruntime.dll`, and `rexgpu-xenos.dll`.

If your LLVM installation is not on `PATH`, pass the compiler paths to
CMake, or create a local `CMakeUserPresets.json` in the SDK checkout. Do not
commit machine-specific paths.

## 3. Extract your game data

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

## 4. Generate the project

The checked-in `project/pes6_manifest.toml` expects the game at `../game`.
It also includes the function and switch-table inputs in `project/`.

```bash
cd project
cp CMakeUserPresets.json.example CMakeUserPresets.json
```

Edit `CMakeUserPresets.json` and set `CMAKE_PREFIX_PATH` to the install path
from step 2. Then run codegen:

```bash
C:/path/to/rexglue-sdk/out/install/win-amd64/bin/rexglue.exe \
    codegen pes6_manifest.toml --ignore-stamp
```

Codegen writes `project/generated/`. It is ignored by Git. The generated code
is a derivative work of the game and must not be published.

## 5. Build and run

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
pes6.exe --game_data_root=C:/path/to/360PES6TOPC/game
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

- If CMake cannot find ReXGlue, check `CMAKE_PREFIX_PATH` in your local
  `CMakeUserPresets.json`.
- If `rexglue` cannot find the XEX, check `game_root` and `file_path` in
  `project/pes6_manifest.toml`, and confirm that `game/default.xex` exists.
- If the game opens the setup dialog, select the extracted `game` folder, or
  pass `--game_data_root`.
- If a game-data file is missing, extract the ISO again. Do not copy a game
  directory into a Git checkout.
