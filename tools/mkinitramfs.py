#!/usr/bin/env python3
"""目录 -> initramfs (cpio newc + gzip)，纯 Python，无需 cpio 工具。

用法: python mkinitramfs.py <rootdir> <out.cpio.gz>
支持普通文件、目录、符号链接、可执行位；跳过设备节点（运行时由 devtmpfs 提供）。
"""
from __future__ import annotations

import gzip
import os
import stat
import sys

MAGIC = b"070701"
HDR_FMT = "8s" + "8s" * 13
TRAILER = "TRAILER!!!"
# Windows 文件系统不保留可执行位，按路径还原
EXEC_DIRS = {"bin", "sbin", "usr/bin", "usr/sbin", "lib", "usr/lib", "lib64"}


def perm_for(rel: str, fallback: int) -> int:
    if fallback & 0o111:
        return fallback & 0o7777
    parts = rel.replace("\\", "/")
    if parts == "init" or any(parts.startswith(d + "/") for d in EXEC_DIRS):
        return 0o755
    return fallback & 0o7777 or 0o644


def _hdr(name: str, ino: int, mode: int, size: int, nlink: int = 1,
         rdevmajor: int = 0, rdevminor: int = 0) -> bytes:
    fields = [ino & 0xFFFFFFFF, mode, 0, 0, nlink, 0, size,
              0, 0, rdevmajor, rdevminor, len(name) + 1, 0]
    out = MAGIC + b"".join(f"{f:08x}".encode() for f in fields)
    out += name.encode() + b"\x00"
    out += b"\x00" * ((4 - (len(out) % 4)) % 4)
    return out


def _data(blob: bytes) -> bytes:
    return blob + b"\x00" * ((4 - (len(blob) % 4)) % 4)


def build(root: str) -> bytes:
    root = os.path.abspath(root)
    entries: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        rel = os.path.relpath(dirpath, root)
        if rel != ".":
            entries.append(rel)
        for f in sorted(filenames):
            entries.append(os.path.relpath(os.path.join(dirpath, f), root))

    out = bytearray()
    ino = 1
    # 根目录条目必须存在
    st = os.stat(root)
    out += _hdr(".", ino, stat.S_IFDIR | 0o755, 0, nlink=2)
    for rel in entries:
        full = os.path.join(root, rel)
        st = os.lstat(full)
        ino += 1
        mode = st.st_mode
        name = rel.replace(os.sep, "/")
        if stat.S_ISDIR(mode):
            out += _hdr(name, ino, stat.S_IFDIR | (mode & 0o7777), 0, nlink=2)
        elif stat.S_ISLNK(mode):
            target = os.readlink(full).encode()
            out += _hdr(name, ino, stat.S_IFLNK | 0o777, len(target))
            out += _data(target)
        elif stat.S_ISREG(mode):
            with open(full, "rb") as f:
                blob = f.read()
            out += _hdr(name, ino, stat.S_IFREG | perm_for(rel, mode & 0o7777), len(blob))
            out += _data(blob)
        else:
            continue  # 设备节点/管道跳过
    out += _hdr(TRAILER, ino + 1, 0, 0)
    return bytes(out)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    root, out_path = sys.argv[1], sys.argv[2]
    blob = build(root)
    with gzip.open(out_path, "wb", compresslevel=6) as f:
        f.write(blob)
    print(f"[✓] {out_path}: 原始 {len(blob):,} B -> 压缩 {os.path.getsize(out_path):,} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
