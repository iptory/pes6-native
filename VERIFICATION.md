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
| Shader cache | 75 shaders and 132 graphics pipelines loaded from persistent storage at startup |
| Package smoke test | The built executable with its runtime DLLs booted the intro with an external game-data path |

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
