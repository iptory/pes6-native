#!/usr/bin/env python3
"""Reject game data, generated code, and build output from the Git index."""

import subprocess
import sys


FORBIDDEN_PREFIXES = (
    "game/",
    "work/",
    "dist/",
    "project/generated/",
    "project/out/",
    "rexglue-sdk/",
    "xenia-canary/",
    "rea/",
    ".agents/",
    ".gemini/",
)

FORBIDDEN_NAMES = {
    "pes6.iso",
    "default.xex",
    "default.xbe",
    "checkpoint.md",
    "seeds.json",
    "cmakeuserpresets.json",
    "pinned_revisions.md",
}

FORBIDDEN_SUFFIXES = (
    ".iso",
    ".xex",
    ".xbe",
    ".img",
    ".wmv",
    ".wma",
    ".xma",
    ".bin",
    ".dll",
    ".exe",
    ".pdb",
    ".obj",
    ".pyc",
    ".log",
)

REQUIRED = (
    "LICENSE",
    "LEGAL.md",
    "README.md",
    "BUILD.md",
    "THIRD_PARTY_NOTICES.md",
    "patches/rexglue-sdk.patch",
    "project/pes6_manifest.toml",
    "project/src/main.cpp",
    "tools/xdvdfs_extract.py",
)


def tracked_files():
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return [name.decode("utf-8") for name in result.stdout.split(b"\0") if name]


def fail(message):
    print(f"public-tree check: {message}", file=sys.stderr)


def main():
    try:
        files = tracked_files()
    except (OSError, subprocess.CalledProcessError) as error:
        fail(f"cannot read the Git index: {error}")
        return 1

    problems = []
    for name in files:
        lower = name.lower()
        base = lower.rsplit("/", 1)[-1]
        if lower.startswith(FORBIDDEN_PREFIXES) or base in FORBIDDEN_NAMES:
            problems.append(f"forbidden path: {name}")
        elif lower.endswith(FORBIDDEN_SUFFIXES):
            problems.append(f"forbidden file type: {name}")

    for name in REQUIRED:
        if name not in files:
            problems.append(f"missing required file: {name}")

    if problems:
        for problem in problems:
            fail(problem)
        return 1

    print(f"public-tree check: {len(files)} tracked files, no game data or build output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
