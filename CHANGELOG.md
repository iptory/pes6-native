# Changelog

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
