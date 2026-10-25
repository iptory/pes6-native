# Security policy

## Scope

This policy covers the build tooling, the XDVDFS extractor, the setup code,
the settings overlay, and the ReXGlue patch. It does not cover the game
itself or third-party SDK code.

## Reporting a vulnerability

Use GitHub's private security advisory for this repository. Do not open a
public issue with an exploit, a game image, a key, or personal data.

Include:

- A short description.
- Steps to reproduce.
- The affected commit.
- The host OS and toolchain.
- Whether the issue is in this repository or in upstream ReXGlue.

Do not attach a game ISO, XEX, extracted game data, generated code, or a
compiled binary.

## Supported versions

Only the current `main` branch and the pinned ReXGlue revision are supported.
Older commits may not receive fixes.

## Game files

Requests for game files, decryption keys, or prebuilt binaries are out of
scope. Do not send them.
