#!/usr/bin/env python3
"""Android boot image (header v0/v1/v2 + vendor_boot) 打包/解包工具，纯 Python 实现。

用途：在 Windows 上合成 Nubia Z11 可刷写的 boot.img（kernel + dtb + initramfs）。
用法：
    python bootimg.py info   <boot.img>
    python bootimg.py unpack <boot.img> <outdir>
    python bootimg.py pack   <outdir> <out.img> [--cmdline "..."] [--page 4096]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import struct
import sys

MAGIC = b"ANDROID!"
BOOT_NAME_SIZE = 16
BOOT_ARGS_SIZE = 512
BOOT_EXTRA_ARGS_SIZE = 1024
VENDOR_BOOT_MAGIC = b"VNDRBOOT"
DEFAULT_PAGE = 2048


def _pad(n: int, page: int) -> int:
    return ((n + page - 1) // page) * page


class BootImage:
    def __init__(self) -> None:
        self.kernel = b""
        self.ramdisk = b""
        self.second = b""
        self.dtb = b""
        self.recovery_dtbo = b""
        self.kernel_addr = 0x10008000
        self.ramdisk_addr = 0x11000000
        self.second_addr = 0x10F00000
        self.tags_addr = 0x10000100
        self.dtb_addr = 0x1F00000
        self.page_size = DEFAULT_PAGE
        self.header_version = 0
        self.os_version = 0
        self.name = b""
        self.cmdline = b""
        self.extra_cmdline = b""

    # ---------- 解析 ----------
    @classmethod
    def parse(cls, data: bytes) -> "BootImage":
        img = cls()
        if data[:8] == VENDOR_BOOT_MAGIC:
            raise NotImplementedError("vendor_boot 暂不支持")
        if data[:8] != MAGIC:
            raise ValueError(f"不是 Android boot 镜像 (magic={data[:8]!r})")

        (ksize, kaddr, rsize, raddr, ssize, saddr, tags, page, hver) = struct.unpack("<9I", data[8:44])
        img.page_size = page or DEFAULT_PAGE
        img.header_version = hver
        img.os_version = struct.unpack("<I", data[44:48])[0]
        img.name = data[48:48 + BOOT_NAME_SIZE].rstrip(b"\x00")
        img.cmdline = data[64:64 + BOOT_ARGS_SIZE].rstrip(b"\x00")
        img.kernel_addr, img.ramdisk_addr = kaddr, raddr
        img.second_addr, img.tags_addr = saddr, tags

        off = img.page_size
        img.kernel = data[off:off + ksize]
        off += _pad(ksize, img.page_size)
        img.ramdisk = data[off:off + rsize]
        off += _pad(rsize, img.page_size)
        img.second = data[off:off + ssize]
        off += _pad(ssize, img.page_size)

        if hver >= 1:
            (rdtbo_size, rdtbo_addr) = struct.unpack("<II", data[1632:1640])
            off += _pad(0, img.page_size)  # recovery_dtbo 紧跟 second（若有）
        if hver >= 2:
            (dtb_size, dtb_addr) = struct.unpack("<QQ", data[1648:1664])
            img.dtb = data[off:off + dtb_size]
        extra = data[584:584 + BOOT_EXTRA_ARGS_SIZE].rstrip(b"\x00")
        img.extra_cmdline = extra
        return img

    # ---------- 合成 ----------
    def pack(self, cmdline: bytes | None = None, dtb: bytes | None = None,
             template: bytes | None = None) -> bytes:
        """template: 1632 字节原始头部。提供时继承全部未知字段（含厂商私有 id），
        仅覆盖尺寸与命令行，保证未变更内容时字节级可复现。"""
        cmd = self.cmdline if cmdline is None else cmdline
        dtb_data = self.dtb if dtb is None else dtb
        page = self.page_size

        if len(cmd) > BOOT_ARGS_SIZE:
            raise ValueError(f"cmdline 超长: {len(cmd)} > {BOOT_ARGS_SIZE}")
        if len(self.name) > BOOT_NAME_SIZE:
            raise ValueError("name 超长")

        if template is not None:
            hdr = bytearray(template[:1664].ljust(1664, b"\x00"))
            struct.pack_into("<I", hdr, 8, len(self.kernel))
            struct.pack_into("<I", hdr, 16, len(self.ramdisk))
            struct.pack_into("<I", hdr, 24, len(self.second))
            if cmdline is not None:
                hdr[64:64 + BOOT_ARGS_SIZE] = bytes(BOOT_ARGS_SIZE)
                hdr[64:64 + len(cmd)] = cmd
            if self.header_version >= 2:
                struct.pack_into("<I", hdr, 1648, len(dtb_data))
                struct.pack_into("<Q", hdr, 1652, self.dtb_addr)
            out = bytearray()
            out += bytes(hdr).ljust(page, b"\x00")
            out += self.kernel.ljust(_pad(len(self.kernel), page), b"\x00")
            out += self.ramdisk.ljust(_pad(len(self.ramdisk), page), b"\x00")
            if self.second:
                out += self.second.ljust(_pad(len(self.second), page), b"\x00")
            if self.header_version >= 2 and dtb_data:
                out += dtb_data.ljust(_pad(len(dtb_data), page), b"\x00")
            return bytes(out)

        hdr = bytearray(1664)
        hdr[0:8] = MAGIC
        struct.pack_into("<9I", hdr, 8,
                         len(self.kernel), self.kernel_addr,
                         len(self.ramdisk), self.ramdisk_addr,
                         len(self.second), self.second_addr,
                         self.tags_addr, page, self.header_version)
        struct.pack_into("<I", hdr, 44, self.os_version)
        hdr[48:48 + len(self.name)] = self.name
        hdr[64:64 + len(cmd)] = cmd
        # id = sha1(kernel + ramdisk + second)，老 bootloader 会校验该字段
        h = hashlib.sha1()
        h.update(self.kernel)
        h.update(self.ramdisk)
        h.update(self.second)
        hdr[576:576 + 20] = h.digest()
        hdr[584:584 + len(self.extra_cmdline)] = self.extra_cmdline[:BOOT_EXTRA_ARGS_SIZE]
        if self.header_version >= 2:
            struct.pack_into("<I", hdr, 1644, 1660)       # header_size
            struct.pack_into("<I", hdr, 1648, len(dtb_data))
            struct.pack_into("<Q", hdr, 1652, self.dtb_addr)

        out = bytearray()
        out += bytes(hdr).ljust(page, b"\x00")
        out += self.kernel.ljust(_pad(len(self.kernel), page), b"\x00")
        out += self.ramdisk.ljust(_pad(len(self.ramdisk), page), b"\x00")
        if self.second:
            out += self.second.ljust(_pad(len(self.second), page), b"\x00")
        if self.header_version >= 2 and dtb_data:
            out += dtb_data.ljust(_pad(len(dtb_data), page), b"\x00")
        return bytes(out)

    def info(self) -> str:
        return (f"magic=ANDROID!  header_v{self.header_version}  page={self.page_size}\n"
                f"kernel   {len(self.kernel):>10,} B  addr=0x{self.kernel_addr:08x}\n"
                f"ramdisk  {len(self.ramdisk):>10,} B  addr=0x{self.ramdisk_addr:08x}\n"
                f"second   {len(self.second):>10,} B\n"
                f"dtb      {len(self.dtb):>10,} B\n"
                f"cmdline  ({len(self.cmdline)} B) {self.cmdline.decode('utf-8', 'replace')}")


def cmd_info(path: str) -> int:
    with open(path, "rb") as f:
        img = BootImage.parse(f.read())
    print(img.info())
    print(f"file size: {os.path.getsize(path):,} B")
    return 0


def cmd_unpack(path: str, outdir: str) -> int:
    with open(path, "rb") as f:
        raw = f.read()
    img = BootImage.parse(raw)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "header.bin"), "wb") as f:
        f.write(raw[:1632])
    for tag, blob in (("kernel", img.kernel), ("ramdisk", img.ramdisk),
                      ("second", img.second), ("dtb", img.dtb)):
        if blob:
            p = os.path.join(outdir, tag)
            with open(p, "wb") as f:
                f.write(blob)
            print(f"  写出 {tag}: {len(blob):,} B")
    with open(os.path.join(outdir, "cmdline.txt"), "wb") as f:
        f.write(img.cmdline)
    print(f"  写出 cmdline.txt ({len(img.cmdline)} B)")
    print(img.info())
    return 0


def cmd_pack(outdir: str, out_path: str, cmdline: str | None, page: int | None,
             addrs: dict | None = None, hver: int | None = None) -> int:
    img = BootImage()
    tmpl = None
    tmpl_path = os.path.join(outdir, "header.bin")
    if os.path.exists(tmpl_path):
        with open(tmpl_path, "rb") as f:
            tmpl = f.read()
        img.page_size = struct.unpack("<I", tmpl[36:40])[0] or DEFAULT_PAGE
        img.header_version = struct.unpack("<I", tmpl[40:44])[0]
        print(f"[i] 继承模板头部: page={img.page_size} header_v{img.header_version}")
    for k, v in (addrs or {}).items():
        if v is not None:
            setattr(img, k, v)
    if hver is not None:
        img.header_version = hver
    for tag in ("kernel", "ramdisk"):
        p = os.path.join(outdir, tag)
        if not os.path.exists(p):
            print(f"[!] 缺少 {p}", file=sys.stderr)
            return 2
        with open(p, "rb") as f:
            setattr(img, tag, f.read())
    for tag in ("second", "dtb"):
        p = os.path.join(outdir, tag)
        if os.path.exists(p):
            with open(p, "rb") as f:
                setattr(img, tag, f.read())
    if img.dtb:
        img.header_version = 2
    if page:
        img.page_size = page

    # 命令行：显式参数 > cmdline.txt > 模板内原有内容
    new_cmd: bytes | None = None
    if cmdline is not None:
        new_cmd = cmdline.encode()
    elif os.path.exists(os.path.join(outdir, "cmdline.txt")):
        with open(os.path.join(outdir, "cmdline.txt"), "rb") as f:
            new_cmd = f.read()
        if tmpl is not None and tmpl[64:64 + len(new_cmd)] == new_cmd[:BOOT_ARGS_SIZE]:
            new_cmd = None  # 与模板一致，无需改写
    img.cmdline = new_cmd if new_cmd is not None else img.cmdline
    blob = img.pack(cmdline=new_cmd, template=tmpl)
    with open(out_path, "wb") as f:
        f.write(blob)
    print(f"[✓] 写出 {out_path} ({len(blob):,} B)")
    print(img.info())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Android boot image 打包/解包")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info"); p.add_argument("img")
    p = sub.add_parser("unpack"); p.add_argument("img"); p.add_argument("outdir")
    p = sub.add_parser("pack"); p.add_argument("outdir"); p.add_argument("out")
    p.add_argument("--cmdline", default=None); p.add_argument("--page", type=int, default=None)
    p.add_argument("--kernel-addr", type=lambda x: int(x, 0), default=None)
    p.add_argument("--ramdisk-addr", type=lambda x: int(x, 0), default=None)
    p.add_argument("--tags-addr", type=lambda x: int(x, 0), default=None)
    p.add_argument("--header-version", type=int, default=None)
    a = ap.parse_args()
    if a.cmd == "info":
        return cmd_info(a.img)
    if a.cmd == "unpack":
        return cmd_unpack(a.img, a.outdir)
    return cmd_pack(a.outdir, a.out, a.cmdline, a.page,
                    {"kernel_addr": a.kernel_addr, "ramdisk_addr": a.ramdisk_addr,
                     "tags_addr": a.tags_addr}, a.header_version)


if __name__ == "__main__":
    sys.exit(main())
