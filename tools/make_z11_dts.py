#!/usr/bin/env python3
"""从反编译的小米 5 (gemini) 自包含 DTS 派生出 Nubia Z11 (NX531J) 设备树。

只在 gemini.dts 基础上做最小必要改动：
  1. 机型标识改写
  2. 新增 splash 显存保留区 + simple-framebuffer（不依赖 DSI 驱动即可看到内核日志）
其余 SoC/PMIC/USB 节点沿用 gemini（同 MSM8996 + PMI8996 平台）。

用法: python make_z11_dts.py <gemini.dts> <z11.dts>
"""
from __future__ import annotations

import re
import sys

FB_BASE = 0x83401000     # 与下游 DT 的 cont_splash_mem 起始一致
FB_W, FB_H = 1080, 1920  # JDI R63452 1080p 5.5"
FB_STRIDE = FB_W * 4
FB_SIZE = FB_STRIDE * FB_H           # 0x7E9000
RSV_BASE = 0x83400000
RSV_SIZE = 0x023FF000                # 与下游 cont_splash_mem 对齐

FRAMEBUFFER_NODE = f"""
\tframebuffer@{FB_BASE:x} {{
\t\tcompatible = "simple-framebuffer";
\t\treg = <0x00 {hex(FB_BASE)} 0x00 {hex(FB_SIZE)}>;
\t\twidth = <{hex(FB_W)}>;
\t\theight = <{hex(FB_H)}>;
\t\tstride = <{hex(FB_STRIDE)}>;
\t\tformat = "a8r8g8b8";
\t\tstatus = "okay";
\t}};
"""


def sub_once(text: str, pattern: str, repl: str, what: str) -> str:
    new, n = re.subn(pattern, repl, text, count=1, flags=re.M)
    if n != 1:
        raise SystemExit(f"[!] 未找到唯一匹配: {what}")
    return new


def block_end(text: str, open_idx: int) -> int:
    """给定 '{' 的位置，返回与之配对的 '}' 之后的下标（跳过字符串）"""
    depth = 0
    i = open_idx
    in_str = False
    while i < len(text):
        c = text[i]
        if in_str:
            if c == '"' and text[i - 1] != "\\":
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise SystemExit("[!] 大括号不配对")


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8") as f:
        t = f.read()

    t = sub_once(t, r'\tmodel = "Xiaomi Mi 5";',
                 '\tmodel = "Nubia Z11 (NX531J)";', "model")
    t = sub_once(t, r'\tcompatible = "xiaomi,gemini", "qcom,msm8996";',
                 '\tcompatible = "nubia,z11", "qcom,msm8996";', "compatible")

    # splash 保留区：追加到 reserved-memory 末尾（避免落在属性之前）
    if "splash_region" not in t:
        m = re.search(r'\treserved-memory \{\n', t)
        if not m:
            raise SystemExit("[!] 未找到 reserved-memory 节点")
        end = block_end(t, m.end() - 1)          # 指向 '}' 之后
        ins = (f'\t\tsplash_region: splash@{RSV_BASE:x} {{\n'
               f'\t\t\treg = <0x00 {hex(RSV_BASE)} 0x00 {hex(RSV_SIZE)}>;\n'
               '\t\t};\n')
        t = t[:end - 1] + ins + t[end - 1:]

    # 顶层插入 framebuffer 节点（紧跟 chosen 之后）
    t = sub_once(t, r'(\tchosen \{\n\t\};\n)', r'\1' + FRAMEBUFFER_NODE, "framebuffer 插入")

    with open(dst, "w", encoding="utf-8") as f:
        f.write(t)
    print(f"[✓] 写出 {dst}（{len(t.splitlines()):,} 行）")
    print(f"    splash 保留区: {hex(RSV_BASE)} + {hex(RSV_SIZE)}")
    print(f"    帧缓冲: {hex(FB_BASE)}, {FB_W}x{FB_H}, stride {FB_STRIDE}, "
          f"{FB_SIZE/1048576:.2f} MiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
