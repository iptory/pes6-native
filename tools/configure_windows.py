#!/usr/bin/env python3
"""Write local CMake presets for the SDK and project on Windows.

CMake's upstream presets expect clang and rc.exe on PATH. This writes user
presets with explicit paths when those tools are installed in their default
locations. Existing preset files are left untouched unless --force is given.
"""

import argparse
import glob
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def find_clang():
    for name in ("clang.exe", "clang"):
        found = shutil.which(name)
        if found:
            return Path(found)
    candidate = Path("C:/Program Files/LLVM/bin/clang.exe")
    if candidate.is_file():
        return candidate
    return None


def find_rc():
    pattern = "C:/Program Files (x86)/Windows Kits/10/bin/*/x64/rc.exe"
    candidates = [Path(path) for path in glob.glob(pattern) if Path(path).is_file()]
    if not candidates:
        return None
    return sorted(candidates)[-1]


def write_presets(path, data, force):
    if path.exists() and not force:
        raise SystemExit(f"{path} already exists; pass --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk", default=str(ROOT / "rexglue-sdk"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if sys.platform != "win32":
        raise SystemExit("this helper is for Windows builds")

    sdk = Path(args.sdk).resolve()
    if not (sdk / "CMakePresets.json").is_file():
        raise SystemExit(f"ReXGlue SDK not found at {sdk}")

    clang = find_clang()
    if clang is None:
        raise SystemExit("clang.exe was not found; install LLVM or add it to PATH")
    clangxx = clang.with_name("clang++.exe")
    if not clangxx.is_file():
        raise SystemExit(f"clang++ was not found next to {clang}")
    rc = find_rc()
    if rc is None:
        raise SystemExit("rc.exe was not found in the Windows 10/11 SDK")

    compilers = {
        "CMAKE_C_COMPILER": clang.as_posix(),
        "CMAKE_CXX_COMPILER": clangxx.as_posix(),
        "CMAKE_RC_COMPILER": rc.as_posix(),
    }
    sdk_presets = {
        "version": 6,
        "configurePresets": [
            {
                "name": "win-amd64-local",
                "inherits": "win-amd64",
                "cacheVariables": compilers,
            }
        ],
        "buildPresets": [
            {
                "name": "win-amd64-local-release",
                "configurePreset": "win-amd64-local",
                "configuration": "Release",
            }
        ],
    }
    write_presets(sdk / "CMakeUserPresets.json", sdk_presets, args.force)

    project_presets = {
        "version": 6,
        "configurePresets": [
            {
                "name": "win-amd64-release-local",
                "inherits": "win-amd64-release",
                "cacheVariables": {
                    **compilers,
                    "CMAKE_PREFIX_PATH": (
                        sdk / "out/install/win-amd64-local"
                    ).as_posix(),
                },
            }
        ],
        "buildPresets": [
            {
                "name": "win-amd64-release-local",
                "configurePreset": "win-amd64-release-local",
            }
        ],
    }
    write_presets(ROOT / "project/CMakeUserPresets.json", project_presets, args.force)
    print(f"wrote presets for {sdk}")


if __name__ == "__main__":
    main()
