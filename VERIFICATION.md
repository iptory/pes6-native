# Verification record

These results were observed on the development machine with the pinned SDK,
the project patch, and a local PAL game image. This is not a guarantee for
other hardware. The repository does not include game files, screenshots, or
build logs.

## Test environment

- Windows 10 19045 x64
- NVIDIA GeForce RTX 3090, Direct3D 12
- LLVM/Clang 23.1.4, CMake 4.3.1, Ninja 1.13, Visual Studio 2022
- ReXGlue `c94f5ebdcb3c9d1a460ca48e04f9758448f8d518` with
  `patches/rexglue-sdk.patch`
- PES6 PAL, title `4B4E07DA`

## Verified behavior

| Area | Result |
| --- | --- |
| Boot | Splash screens, real-time intro, and title screen render |
| Native execution | Guest calls reach generated x64 functions through the function table; no CPU interpreter or JIT |
| Match setup | Team selection, stadium, match settings, lineup, and kickoff work |
| Match | AI-controlled and player-controlled play, ball physics, HUD, stamina, weather, referees, and replays work |
| Audio | XMA decode through FFmpeg and SDL output; 6-channel 48 kHz submission on the test endpoint |
| Frame rate | 60.0 fps on the test machine over repeated 2-second captures |
| Saves | System Data, Cup, League, and Edit saves were written and reloaded after restart |
| Settings | The F5 overlay, controller navigation, presets, and save-to-config build and run |
| Presentation | D3D12 presenter logged the waitable swap chain with a maximum frame latency of 1 |
| Window | Title reads `Pro Evolution Soccer 2006`; a local title icon was applied through the native window handle |
| Multi-user sign-in | Users 0-3 and any-user sign-in queries now report the configured Live state; profile-backed storage works regardless of the checked index |
| Multi-controller keyboard | The keyboard dialog merges all four input-system user states, so any connected controller can drive selection and text entry |
| Settings | A valid `resolution_scale = 4` config reloaded as 4; TOML path escaping was fixed; F5 and shutdown saves write a parseable config |
| Save flow | With `xam_signin_state = 2`, boot reached storage prompt, the local device was selected, and Save System Data completed after choosing Yes; `savegame.txt` mtime advanced |
| Frame timer | Repeated `[timing]` records reported 59.95 to 60.08 fps in test runs |
| Shader cache | 75 shaders and 132 graphics pipelines loaded from persistent storage at startup |
| Package smoke test | The built executable with its runtime DLLs booted the intro with an external game-data path |
| Clean-tree build | A detached worktree containing only the tracked files configured, generated, built, and booted with `rexgpu-xenos.dll` staged by the build |

## Builder verification, 2026-10-28

- A fresh headless builder run staged an extracted game folder, cloned and
  built the pinned SDK, generated native code, compiled and assembled the game.
  It completed without launching the game. Code generation reported unresolved
  function/stub warnings; gameplay was not re-tested in this pass.
- The ISO extraction path produced the expected local `default.xex` SHA-256
  `b76db0fc2f86794f4e99bb4d93dd8148d31a00b518cb1e5c822ef0bc75ac1087`
  and reported progress. An invalid image surfaced an error and left no
  installed game directory.
- The Windows drag-and-drop handler received a real `WM_DROPFILES` message
  with a Unicode ISO path and started the source-selection flow.
- Automatic launch was exercised with a compiled harmless executable, not the
  game; its data argument and working directory were verified.
- 38 builder tests and 4 extractor tests passed. Missing-tool installation was
  not exercised against a real package installation.
- Android has not been built or run. No Android device was attached.

## Builder 1.0.2 hotfix verification, 2026-10-28

- 40 builder tests and 4 extractor tests passed. The installer worker executed
  a harmless stand-in process that validates the Visual Studio C++ workload
  options. A rejected package stopped the queue and surfaced its exit code
  and diagnostic output. No compiler packages were installed during this check.
- The real Tk window was exercised on an isolated desktop, including ISO/folder
  picker cancellation, retry, folder/license actions, prerequisite details,
  log expansion and progress updates. Normal and enlarged Tk text layouts
  kept visible controls inside the window.
- This hotfix does not change game runtime code. Cleanup/dependency redesign
  and replay-audio/frame-pacing investigation are deferred.

## Builder 1.0.3 verification, 2026-10-29

- A fresh run imported the local PAL ISO, cloned and built the pinned SDK,
  generated code and compiled the game into a path containing spaces and `é`.
  It assembled the final directory and removed its private workspace before
  the handoff. A harmless windowless executable verified the launch arguments
  and working directory; folder opening was intercepted. The game itself was
  not launched.
- With an empty portable-tool inventory and a minimal PATH, the builder
  downloaded and verified its actual Git, CMake, Ninja and LLVM archives,
  compiled and ran a C++/Windows-resource probe with the existing Microsoft
  toolchain, and removed the disposable workspace. The original input stand-in
  remained intact.
- Both current Microsoft bootstrapper downloads passed the real Windows
  Authenticode validation without executing either installer. Installer
  success, errors, UAC denial and post-install detection were exercised with
  stand-ins. A clean Windows VM was unavailable; a real first-time elevated
  MSVC/SDK installation was not exercised.
- 85 builder and 4 extractor tests passed, including cancellation, process
  tree ownership, long paths, Unicode configuration, cleanup failure and
  preservation of user files. The runtime logging probe loaded a config from a
  Unicode directory: disabled logging produced no file, while explicitly
  enabling it produced the expected file.
- The real packaged builder opened on an isolated desktop. Source UI captures
  at normal and enlarged text scales, including the packaged cleanup option,
  passed two independent visual reviews. No game window or audio was opened.
- Replay scratching and intermittent 30 fps remain unresolved and are deferred.

## Known limits

- The game uses a fixed 60 Hz simulation tick. The project does not retime
  gameplay.
- Pre-rendered WMV movies are 30 fps assets. The project does not re-encode
  or interpolate them.
- Xbox Live is unavailable. No replacement service is included.
- A full Master League season, a Cup trophy, and a complete two-player match
  were not verified.
- A hand-written replacement D3D12 or Vulkan renderer is not included. The
  project uses ReXGlue's native D3D12 command processor on Windows and its
  Vulkan backend where supported.
- The F5 settings beyond the waitable swap chain were compiled and packaged
  but were not re-tested in the final cleanup pass.

## Repeating a check

Build with your own game data, run a match, and keep the generated log. Do not
attach game files, generated translation units, or compiled binaries to a
public issue.
