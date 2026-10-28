#!/usr/bin/env python3
"""Package the Windows builder and the tracked source needed for a local build.

Run from a Git checkout after installing PyInstaller:
    py -3 builder/package_builder.py --out <new-output-directory>
The builder contains no game data or generated game code.
"""

import argparse
import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="new output directory")
    args = parser.parse_args()
    output = args.out.resolve()
    if os.name != "nt":
        parser.error("the Windows builder must be packaged on Windows")
    if output.exists():
        parser.error(f"output directory already exists: {output}")

    subprocess.run([sys.executable, str(ROOT / "tools/check_public_tree.py")], cwd=ROOT, check=True)
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).split(b"\0")
    paths = {Path(os.fsdecode(entry)) for entry in tracked if entry}
    for name in ("builder/package_builder.py", "builder/test_pes6_builder.py", "builder/TCL_LICENSE.txt"):
        if (ROOT / name).is_file():
            paths.add(Path(name))
    output.mkdir(parents=True)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, output / name)
    shutil.copy2(ROOT / "builder/NOTICES.md", output / "BUILDER_NOTICES.md")
    licenses = output / "licenses"
    licenses.mkdir()
    shutil.copy2(ROOT / "LICENSE", licenses / "PROJECT.txt")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", licenses / "THIRD_PARTY_NOTICES.md")
    shutil.copy2(ROOT / "builder/NOTICES.md", licenses / "BUILDER_NOTICES.md")
    python_root = Path(sysconfig.get_path("stdlib")).parent
    shutil.copy2(python_root / "LICENSE.txt", licenses / "PYTHON.txt")
    tk_license = next((python_root / "tcl").glob("tk*/license.terms"))
    shutil.copy2(tk_license, licenses / "TK.txt")
    shutil.copy2(ROOT / "builder/TCL_LICENSE.txt", licenses / "TCL.txt")
    for package, output_name in (("PyInstaller", "PYINSTALLER.txt"), ("Pillow", "PILLOW.txt")):
        try:
            distribution = importlib.metadata.distribution(package)
        except importlib.metadata.PackageNotFoundError:
            if package == "Pillow":
                continue
            raise
        matches = [item for item in distribution.files or ()
                   if "/licenses/" in str(item).replace("\\", "/").lower()]
        if not matches:
            raise RuntimeError(f"{package} license file not found in installed distribution")
        shutil.copy2(distribution.locate_file(matches[0]), licenses / output_name)
    with tempfile.TemporaryDirectory(prefix="pes6-builder-package-") as temp:
        temp = Path(temp)
        archive = temp / "pes6-native-source.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as source:
            for path in sorted(paths):
                source.write(ROOT / path, (Path("pes6-native") / path).as_posix())
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
            "--windowed", "--name", "PES6Builder", "--distpath", str(output),
            "--workpath", str(temp / "build"), "--specpath", str(temp),
            "--add-data", f"{archive}{os.pathsep}.",
            "--add-data", f"{ROOT / 'tools/xdvdfs_extract.py'}{os.pathsep}.",
            "--add-data", f"{licenses}{os.pathsep}licenses",
            str(ROOT / "builder/pes6_builder.py"),
        ], cwd=ROOT, check=True)
    print(f"Builder and notices written to {output}")


if __name__ == "__main__":
    main()
