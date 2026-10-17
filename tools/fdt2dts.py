#!/usr/bin/env python3
"""FDT (dtb) -> DTS 反编译器，纯 Python，无外部依赖。

用于把 Nubia Z11 运行时的下游设备树（/sys/firmware/fdt）还原成可读源码，
作为编写 mainline DTS 的引脚/总线/稳压器参考。

用法: python fdt2dts.py <input.fdt> <output.dts>
"""
from __future__ import annotations

import struct
import sys

FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9


class Fdt:
    def __init__(self, data: bytes) -> None:
        (magic, totalsize, off_struct, off_strings, off_rsv,
         version, last_comp, boot_cpu, size_strings, size_struct) = struct.unpack(
            ">10I", data[:40])
        if magic != FDT_MAGIC:
            raise ValueError(f"bad FDT magic: {magic:#x}")
        self.data = data
        self.strings = data[off_strings:off_strings + size_strings]
        self.struct_blk = data[off_struct:off_struct + size_struct]
        self.rsv = []
        off = off_rsv
        while True:
            addr, size = struct.unpack(">QQ", data[off:off + 16])
            if addr == 0 and size == 0:
                break
            self.rsv.append((addr, size))
            off += 16

    def _name(self, off: int) -> bytes:
        end = self.strings.find(b"\x00", off)
        return self.strings[off:end]

    def walk(self):
        """产出 (depth, kind, payload) 事件流"""
        off = 0
        depth = 0
        buf = self.struct_blk
        while off < len(buf):
            (tok,) = struct.unpack(">I", buf[off:off + 4])
            off += 4
            if tok == FDT_BEGIN_NODE:
                end = buf.find(b"\x00", off)
                name = buf[off:end].decode("latin-1")
                off = (end + 4) & ~3
                yield depth, "begin", name
                depth += 1
            elif tok == FDT_END_NODE:
                depth -= 1
                yield depth, "end", None
            elif tok == FDT_PROP:
                ln, nameoff = struct.unpack(">II", buf[off:off + 8])
                off += 8
                val = buf[off:off + ln]
                off = (off + ln + 3) & ~3
                yield depth, "prop", (self._name(nameoff).decode("latin-1"), val)
            elif tok == FDT_NOP:
                continue
            elif tok == FDT_END:
                break
            else:
                raise ValueError(f"未知 token {tok} @ {off}")


def fmt_prop(name: str, val: bytes) -> str:
    if not val:
        return f"{name};"
    # 判定类型
    cells = len(val) % 4 == 0 and len(val) > 0
    printable = all(32 <= b < 127 or b == 0 for b in val) and val.endswith(b"\x00")
    if printable and not (cells and len(val) >= 4 and all(b == 0 for b in val[:4])):
        items = [s.decode("latin-1") for s in val.split(b"\x00") if s]
        return f'{name} = {", ".join(chr(34) + i + chr(34) for i in items)};'
    if cells and len(val) >= 4:
        vals = struct.unpack(f">{len(val)//4}I", val)
        return f"{name} = <{ ' '.join(hex(v) for v in vals) }>;"
    return f'{name} = [{ " ".join(f"{b:02x}" for b in val) }];'


def convert(data: bytes) -> str:
    f = Fdt(data)
    out = ["/dts-v1/;", "", "/* 由 fdt2dts.py 反编译生成，仅作硬件参考 */"]
    for addr, size in f.rsv:
        out.append(f"/memreserve/ {addr:#x} {size:#x};")
    if f.rsv:
        out.append("")
    for depth, kind, payload in f.walk():
        if kind == "begin":
            name = payload or "/"
            indent = "\t" * depth
            out.append(f"{indent}/ {{" if depth == 0 else f"{indent}{name} {{")
        elif kind == "end":
            out.append("\t" * depth + "};")
        else:
            pname, val = payload
            out.append("\t" * depth + fmt_prop(pname, val))
    return "\n".join(out) + "\n"


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    with open(sys.argv[1], "rb") as fh:
        data = fh.read()
    txt = convert(data)
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        fh.write(txt)
    print(f"[✓] {sys.argv[2]}: {len(txt.splitlines()):,} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
