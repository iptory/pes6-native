#!/usr/bin/env python3
"""Resolve libmspack's symlink stubs on Windows checkouts without symlinks.

The ReXGlue submodule stores cabextract/mspack/* as symlinks whose targets are
relative paths. Git for Windows without symlink support writes the target text
into a regular file, which the compiler then rejects. This replaces each such
stub with a copy of its target. Idempotent.

Usage: python tools/fix_libmspack_symlinks.py [sdk-root]
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sdk = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "rexglue-sdk")
stub_dir = os.path.join(sdk, "thirdparty", "libmspack", "cabextract", "mspack")

fixed = 0
if os.path.isdir(stub_dir):
    for name in os.listdir(stub_dir):
        path = os.path.join(stub_dir, name)
        if not os.path.isfile(path) or os.path.getsize(path) > 200:
            continue
        text = open(path, "rb").read().strip()
        if b"\n" in text:
            continue
        target = os.path.normpath(os.path.join(stub_dir, text.decode("latin-1")))
        if os.path.isfile(target) and os.path.getsize(target) > 200:
            open(path, "wb").write(open(target, "rb").read())
            fixed += 1
print(f"resolved {fixed} libmspack stub(s) in {stub_dir}")
