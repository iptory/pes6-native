# Changelog

## 1.0.3 - 2026-10-29

- Automatically download verified portable Git, CMake, Ninja and LLVM when
  needed; install missing Microsoft C++/SDK/runtime components after Windows
  administrator approval, then detect them without a restart.
- Show estimated overall progress, real operation counters, elapsed time and
  recent activity in the compact PES6-themed installer.
- Keep each build in an owned disposable workspace; clean it after success,
  failure or cancellation without replacing an existing game or removing user
  input and shared system tools. The downloaded installer can remove itself
  after successful completion.
- Open the finished game folder and start the locally built executable after
  cleanup. Disable persistent game log files by default, retaining required
  runtime configuration and license notices.
- Fix SDK checkout on long Windows paths and codegen/config parsing in Unicode
  installation paths.


## 1.0.2 - 2026-10-28

- Fix the undefined Visual Studio installer options that stopped missing-tool
  installation and broke the prerequisite details dialog in 1.0.1.
- Add installer-worker regression coverage for the C++ workload and for
  failed installations stopping the remaining package queue.
- Refresh the compact builder window with a PES6-oriented palette, a primary
  ISO action and an expandable build log. Keep controls visible with larger text.

## 1.0.1 - 2026-10-28

- Fix source preparation and surface build stages, progress, cancellation, and errors.
- Bundle project source with the builder and offer installation of missing
  Windows build tools through Windows Package Manager.
- Accept ISO drag-and-drop or browsing, start the build automatically, show the
  resulting paths and launch the locally built game.
- Add a reproducible builder packaging command with embedded license notices.
- Android remains unsupported; no Android build has been verified.

## 1.0.0 - 2026-10-25

First full source release of the native Windows x64 PES6 project.

### Added

- AOT x64 ReXGlue project for the PAL Xbox 360 release.
- Native Windows setup, game-data extraction, settings, and data-prefetch code.
- D3D12 presenter with a waitable swap chain.
- F5 settings overlay with persistent configuration.
- Controller-navigable virtual keyboard for Xbox 360 text entry.
- Original window title and optional user-supplied title icon.
- Source-only release checks that reject game data and build output.
- `PES6Builder.exe`, a no-game-code setup and build program for Windows.

### Fixed

- TOML string escaping for Windows paths in saved cvars.
- Config reloading after GPU plugin cvars are registered.
- F5 values now persist across restart and are saved on change.
- PES6 profile-backed saves work for users 0 through 3 and any-user queries.
- Save overwrite guidance for the game's Yes/No prompts.
- Virtual keyboard input from all connected controllers.
- Repeated timing logs report a stable 60 fps on the test host.

### Known limitations

- The guest framebuffer remains 1280x720. Resolution scale performs integer supersampling, not native 1080p guest rendering.
- Pre-rendered WMV movies remain 30 fps source assets.
- Xbox Live services are unavailable.
- The game's storage and save prompts still require input.
- Physical controller keyboard verification is left to the player.

### Licensing

Original project code is MIT licensed. The ReXGlue patch is distributed under the ReXGlue BSD 3-Clause license. Game data and generated recompiled code are not part of this release.
