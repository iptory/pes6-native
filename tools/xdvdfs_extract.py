#!/usr/bin/env python3
"""Extract the XDVDFS (Xbox 360 game partition) from an Xbox 360 ISO image.

Supports XGD1/2/3 layouts by locating the XDVDFS volume descriptor rather than
assuming a fixed partition offset. Never modifies the source image.

Usage:
  python tools/xdvdfs_extract.py <image.iso> <outdir> [--list-only] [--filter PREFIX]

The game partition is written as a normal directory tree. Pass --manifest to
write a JSON index with sizes, sectors, attributes, and SHA-256 digests.
"""

import argparse
import hashlib
import json
import os
import struct
import sys

SECTOR = 2048
MAGIC = b"MICROSOFT*XBOX*MEDIA"
DESCRIPTOR_SECTOR = 32  # sector offset of the volume descriptor in the partition


class Image:
    def __init__(self, path):
        self.path = path
        self.f = open(path, "rb")
        self.size = os.path.getsize(path)

    def read_at(self, offset, length):
        self.f.seek(offset)
        return self.f.read(length)

    def close(self):
        self.f.close()


def find_partition(img):
    """Return the byte offset of the game partition start."""
    # The volume descriptor lives at partition + 0x10000 and begins with MAGIC.
    chunk = 1 << 24
    prev = b""
    off = 0
    while off < img.size:
        b = img.read_at(off, chunk)
        if not b:
            break
        data = prev + b
        idx = 0
        while True:
            i = data.find(MAGIC, idx)
            if i < 0:
                break
            abs_off = off - len(prev) + i
            cand = abs_off - DESCRIPTOR_SECTOR * SECTOR
            if cand >= 0:
                # Validate: root dir sector/size sane, and root dir sector
                # starts with a directory entry.
                if validate_descriptor(img, cand):
                    return cand
            idx = i + 1
        prev = b[-len(MAGIC):]
        off += len(b)
    raise RuntimeError("XDVDFS volume descriptor not found")


def validate_descriptor(img, part):
    d = img.read_at(part + DESCRIPTOR_SECTOR * SECTOR, SECTOR)
    if len(d) < 24 or not d.startswith(MAGIC):
        return False
    root_sector, root_size = struct.unpack_from("<II", d, 20)
    if root_size == 0 or root_size > 64 * 1024 * 1024:
        return False
    if root_sector * SECTOR + root_size > img.size:
        return False
    entry = img.read_at(part + root_sector * SECTOR, 16)
    if len(entry) < 16:
        return False
    left, right, start, size, attr, namelen = struct.unpack_from("<HHIIBB", entry, 0)
    if namelen == 0 or namelen > 255:
        return False
    if start * SECTOR + size > img.size:
        return False
    return True


def safe_components(path):
    parts = path.split("/")
    if not parts or any(
            not part or part in (".", "..") or "\\" in part or ":" in part
            for part in parts):
        raise RuntimeError(f"unsafe path in image: {path!r}")
    return parts


def read_descriptor(img, part):
    d = img.read_at(part + DESCRIPTOR_SECTOR * SECTOR, SECTOR)
    root_sector, root_size, ftime = struct.unpack_from("<IIQ", d, 20)
    # volume flags / boundaries appear after the timestamp
    return {
        "partition_offset": part,
        "root_dir_sector": root_sector,
        "root_dir_size": root_size,
        "timestamp_filetime": ftime,
    }


def walk_dir(img, part, sector, size):
    """Yield (name, is_dir, sector, size, attributes) for one directory table.

    Entries form a binary search tree; left/right are byte offsets divided by 4.
    """
    table = img.read_at(part + sector * SECTOR, size)
    if len(table) != size:
        raise RuntimeError(f"short directory read at sector {sector}")
    seen = set()
    pending = [0]
    while pending:
        offset = pending.pop()
        if offset in seen or offset + 14 > len(table):
            continue
        seen.add(offset)
        left, right, start, fsize, attr, namelen = struct.unpack_from("<HHIIBB", table, offset)
        if namelen == 0 or offset + 14 + namelen > len(table):
            continue
        if right not in (0, 0xFFFF):
            pending.append(right * 4)
        if left not in (0, 0xFFFF):
            pending.append(left * 4)
        name = table[offset + 14: offset + 14 + namelen]
        try:
            decoded = name.decode("ascii")
        except UnicodeDecodeError:
            decoded = name.decode("latin-1")
        yield decoded, bool(attr & 0x10), start, fsize, attr


def extract(img, part, outdir, list_only=False, filter_prefix="", manifest_path=None):
    desc = read_descriptor(img, part)
    entries = []
    stack = [("", desc["root_dir_sector"], desc["root_dir_size"])]
    while stack:
        prefix, sector, size = stack.pop()
        for name, is_dir, start, fsize, attr in walk_dir(img, part, sector, size):
            path = prefix + name
            if name in (".", ".."):
                continue
            entries.append((path, is_dir, start, fsize, attr))
            if is_dir:
                if start == 0 and fsize == 0:
                    continue
                stack.append((path + "/", start, fsize))

    entries.sort(key=lambda e: e[0])
    if list_only:
        for path, is_dir, start, fsize, attr in entries:
            print(f"{'D' if is_dir else 'F'} 0x{start:08X} {fsize:10d} {path}")
        return entries

    os.makedirs(outdir, exist_ok=True)
    manifest = []
    for path, is_dir, start, fsize, attr in entries:
        if filter_prefix and not path.startswith(filter_prefix):
            continue
        dest = os.path.join(outdir, *safe_components(path))
        if is_dir:
            os.makedirs(dest, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        offset = part + start * SECTOR
        if offset + fsize > img.size:
            raise RuntimeError(f"{path}: data outside image ({offset:#x}+{fsize})")
        h = hashlib.sha256()
        remaining = fsize
        with open(dest, "wb") as out:
            while remaining:
                n = min(remaining, 8 << 20)
                chunk = img.read_at(offset, n)
                if len(chunk) != n:
                    raise RuntimeError(f"{path}: short read")
                out.write(chunk)
                h.update(chunk)
                offset += n
                remaining -= n
        manifest.append({"path": path, "size": fsize, "sector": start,
                         "attr": attr, "sha256": h.hexdigest()})
    if manifest_path:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1, sort_keys=True)
    return entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("outdir", nargs="?")
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--filter", default="", help="only extract paths with this prefix")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--partition", type=lambda s: int(s, 0), default=None,
                    help="override partition byte offset")
    args = ap.parse_args()

    img = Image(args.image)
    try:
        part = args.partition if args.partition is not None else find_partition(img)
        if args.partition is not None and not validate_descriptor(img, part):
            raise RuntimeError("given partition offset does not validate")
        print(f"partition offset: 0x{part:X} (sector {part // SECTOR})", file=sys.stderr)
        if not args.outdir and not args.list_only:
            ap.error("outdir required unless --list-only")
        entries = extract(img, part, args.outdir or "", args.list_only,
                          args.filter, args.manifest)
        if not args.list_only:
            files = [e for e in entries if not e[1]]
            print(f"extracted {len(files)} files", file=sys.stderr)
    finally:
        img.close()


if __name__ == "__main__":
    main()
