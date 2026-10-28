# Distribution policy and legal notes

This is a project policy, not legal advice. Copyright, contract, and
anti-circumvention rules differ by country. Consult a lawyer before publishing
a build or distributing game-derived material.

## Public release policy

Publish the project as source or as a setup and build tool that contains no
game-derived code or assets. Do not attach a compiled game, a game ISO,
extracted game files, or generated translation units.

The generated C++ is produced from the retail game's PowerPC code. The
compiled `pes6.exe` contains that translated code. Both are derivative works
of the game. This repository does not grant permission to distribute them.

## What this repository contains

- Original Windows setup, settings, and prefetch code.
- ReXGlue project inputs and analysis metadata.
- A patch against a pinned ReXGlue revision.
- An XDVDFS extractor that copies the game partition from a user's ISO.
- Build and repository hygiene tools.
- Function addresses, sizes, and switch-table facts used by code generation.
  These are analysis metadata, not game instructions or game assets.

The XDVDFS extractor does not decrypt the retail XEX. The build uses ReXGlue's
public loader. No XEX decryption key is stored in this repository.

## What must never be committed

- `PES6.iso` or any other game image.
- `default.xex`, `default.xbe`, `.img`, movie, or audio files from the game.
- Extracted game directories such as `game/` or `work/`.
- `project/generated/` or any recompiled game translation unit.
- `pes6.exe`, `rexruntime.dll`, `rexgpu-xenos.dll`, or other build products.
- XEX keys, decryption code, Microsoft SDK files, or leaked SDK material.
- Screenshots or logs that contain game assets or personal paths.

`.gitignore` excludes these paths. `tools/check_public_tree.py` checks the
Git index for common mistakes. It is a safety net, not a guarantee.

## Your copy of the game

You need a lawfully obtained PAL copy of PES6. The project does not provide
one and cannot verify your ownership. Keep your disc image and extracted files
private.

Reverse engineering for interoperability is permitted in some jurisdictions
and restricted in others. Anti-circumvention law, license terms, and console
terms of service may apply even when you own the disc. Do not assume that a
successful local build is permission to publish.

## Binary releases

A compiled game contains recompiled game code. Do not publish it without
permission from the rights holders. Distributing a game binary would also
trigger third-party obligations:

- ReXGlue and Xenia-derived code use the BSD 3-Clause license. Keep the
  copyright notice, conditions, and disclaimer.
- FFmpeg is used under the LGPL. If you distribute a linked binary, provide
  the corresponding source and the license notices, or use a build that
  satisfies the LGPL requirements.
- libmspack is used under the LGPL. The same source and notice obligations
  apply.
- Microsoft Visual C++ runtime DLLs are not part of this repository. If you
  redistribute them, follow Microsoft's redistribution terms.
- SDL, Dear ImGui, Vulkan components, and the other SDK dependencies keep
  their own licenses. See `THIRD_PARTY_NOTICES.md`.

Do not upload a release ZIP that contains game data, generated code, or a
compiled game unless you have the rights to do so.

The standalone `PES6Builder.exe` contains project build tooling, not the
compiled game. Its embedded notices describe its own bundled components.

## Patches and contributions

The original project code is MIT licensed. The ReXGlue patch is a modified
work of ReXGlue and remains under the ReXGlue BSD 3-Clause license. The patch
does not relicense the game or the SDK.

By contributing, you confirm that you have the right to submit the change and
that it contains no game data or third-party code with incompatible terms.

## Trademarks and affiliation

Pro Evolution Soccer, Winning Eleven, Xbox, Xbox 360, Konami, and Microsoft
are trademarks of their respective owners. This project is unofficial and is
not affiliated with or endorsed by Konami or Microsoft. Do not use official
logos or imply endorsement.

## Online services

The historical Xbox Live service is not available. This project does not
implement a replacement service and does not include network-play features.

## If you receive a complaint

If a rights holder or GitHub contacts you about the repository, stop
distributing the disputed material and respond through GitHub's process. Do
not argue ownership of game code or game assets. Remove the material first,
then seek advice.

## No warranty

The software is provided as is, without warranty. You are responsible for
your use of the software and for the game files on your system.
