# Contributing

## Ground rules

Do not submit:

- Game images, extracted game files, or game assets.
- Generated recompiled C++ or build outputs.
- XEX keys, decryption code, leaked SDK files, or Microsoft SDK files.
- Prebuilt executables or DLLs.
- Personal paths or logs that contain game data.

The repository is source only. Read [LEGAL.md](LEGAL.md) before contributing.

## Build and test

1. Follow [BUILD.md](BUILD.md) with your own PAL game data.
2. Build the SDK and the project.
3. Start the game and exercise the code you changed.
4. Run the repository checks:

```bash
python -m compileall -q tools
python -m unittest discover -s tools -p "test_*.py"
python tools/check_public_tree.py
```

If a change touches the setup extractor, test it with a small synthetic or
personal test image. Do not add that image to the repository.

## Code changes

Keep changes small and explain the reason in the pull request. Match the style
of the surrounding code. Avoid adding comments that only restate the next
line. Keep error handling specific; do not swallow failures.

For SDK changes, update `patches/rexglue-sdk.patch` from the pinned checkout
and keep the BSD notice intact. Do not relicense upstream code.

For documentation, use clear sentences and concrete instructions. Do not add
marketing claims or unverified compatibility claims.

## Pull requests

Include:

- What changed and why.
- How you built it.
- What you ran to verify it.
- Any hardware or game-version differences.

Confirm that no game data, generated code, or binary is included.

## License

Original contributions are accepted under the MIT license. Changes to the
ReXGlue patch remain under the ReXGlue BSD 3-Clause license.
