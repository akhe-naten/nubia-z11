#!/usr/bin/env python3
"""Nubia Z11 (NX531J) 全分区备份工具
- 通过 adb exec-out 直接二进制流式拉取，不在设备上占空间
- 排除 system / userdata / cache（体积大且可重刷）
- 额外保存 GPT 头尾，用于灾难恢复
- 逐分区 md5 双向校验，生成 manifest.json
"""
import hashlib
import json
import os
import subprocess
import sys
import time

ADB = r"D:\platform-tools\adb.exe"
OUT = r"D:\刷机\努比亚z11\backup_full"
BYNAME = "/dev/block/bootdevice/by-name"
SKIP = {"system", "userdata", "cache"}


def sh(cmd: str) -> str:
    r = subprocess.run([ADB, "shell", cmd], capture_output=True)
    return r.stdout.decode("utf-8", "replace").replace("\r", "").strip()


def adb_bytes(remote_cmd: str, dest: str) -> int:
    """adb exec-out 拉取二进制数据到本地文件，返回字节数"""
    with open(dest, "wb") as f:
        p = subprocess.run([ADB, "exec-out", remote_cmd], stdout=f, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(f"exec-out 失败: {p.stderr.decode('utf-8', 'replace')[:200]}")
    return os.path.getsize(dest)


def md5_local(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    if not sh("id").startswith("uid=0"):
        print("[!] adb shell 非 root，备份可能不完整", file=sys.stderr)
        return 2

    # 枚举分区名 -> 扇区数
    raw = sh(f'for p in {BYNAME}/*; do n=$(basename $p); d=$(readlink -f $p); '
             f's=$(cat /sys/class/block/${{d##*/}}/size 2>/dev/null); echo "$n $s"; done')
    parts = {}
    for line in raw.splitlines():
        bits = line.split()
        if len(bits) == 2 and bits[1].isdigit():
            parts[bits[0]] = int(bits[1])

    manifest = {"device": "NX531J", "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "partitions": {}, "gpt": {}, "skipped": sorted(SKIP)}
    total = 0
    ok = fail = 0

    print(f"[*] 共 {len(parts)} 个分区，开始备份（排除 {', '.join(sorted(SKIP))}）\n")
    for name in sorted(parts):
        if name in SKIP:
            continue
        sectors = parts[name]
        expect = sectors * 512
        dest = os.path.join(OUT, f"{name}.img")
        src = f"{BYNAME}/{name}"
        try:
            n = adb_bytes(f"dd if={src} 2>/dev/null", dest)
            dm = sh(f"md5sum {src} 2>/dev/null").split()[0] if n else ""
            lm = md5_local(dest)
            match = (lm == dm)
            status = "OK" if (n == expect and match) else "MISMATCH"
            if status == "OK":
                ok += 1
            else:
                fail += 1
            total += n
            manifest["partitions"][name] = {
                "sectors": sectors, "expected_bytes": expect, "bytes": n,
                "md5_local": lm, "md5_device": dm, "status": status}
            print(f"  {name:14s} {n:>11,} / {expect:>11,}  {status}")
        except Exception as e:  # noqa: BLE001
            fail += 1
            manifest["partitions"][name] = {"sectors": sectors, "error": str(e)}
            print(f"  {name:14s} FAILED: {e}")

    # GPT 头尾（灾难恢复用）
    print("\n[*] 备份 GPT 分区表头尾")
    tot_sectors = int(sh("cat /sys/class/block/mmcblk0/size") or 0)
    for tag, cmd in (
        ("gpt_head.img", "dd if=/dev/block/mmcblk0 bs=512 count=34 2>/dev/null"),
        ("gpt_tail.img", f"dd if=/dev/block/mmcblk0 bs=512 skip={max(tot_sectors - 34, 0)} 2>/dev/null"),
    ):
        dest = os.path.join(OUT, tag)
        n = adb_bytes(cmd, dest)
        manifest["gpt"][tag] = {"bytes": n, "md5": md5_local(dest)}
        print(f"  {tag:14s} {n:>11,}  OK")

    manifest["summary"] = {"partitions_ok": ok, "partitions_failed": fail,
                           "total_bytes": total, "mmc_sectors": tot_sectors}
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"\n[✓] 完成: {ok} 成功 / {fail} 失败, 共 {total/1048576:.1f} MiB")
    print(f"[✓] 目录: {OUT}")
    print(f"[✓] 清单: {os.path.join(OUT, 'manifest.json')}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
