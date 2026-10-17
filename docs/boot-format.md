# 引导镜像格式（逆向自原厂 boot.img 与 aboot）

## 1. 原厂镜像实测数据

Nubia Z11 (NX531J) 原厂 `boot` 分区 64 MiB，实际镜像 14,434,304 B：

```
offset 0      : Android boot header (v0, page_size=4096)
offset 4096   : kernel 载荷 11,970,868 B  ← 前 8 字节 1f 8b 08 00 = gzip！
              :   ├─ gzip 流        10,371,546 B
              :   └─ 追加 DTB       1,599,322 B   ← 紧接 gz 流末尾，无任何对齐填充
offset …      : ramdisk 2,454,394 B (gzip cpio)
offset …      : 高通 X.509 签名块 ~1,306 B (0x30820526 起始)
```

`kernel_size = 10,371,546 + 1,599,322 = 11,970,868`，精确等于两项之和，
说明 aboot 以 `kernel_size` 为界、在 gz 流末尾直接定位 DTB。

头部地址：`kernel=0x80008000`、`ramdisk=0x81000000`、`tags=0x80000100`。

## 2. aboot 能力（从 `aboot.img` 字符串提取）

```
decompressing kernel image failed!!!      → 支持 gzip 内核解压
decompressing dtb failed!!!               → 支持压缩 DTB
DTB offset is incorrect, kernel image
  does not have appended DTB              → 要求 DTB 偏移严格正确
ERROR: Couldn't find the suitable DTB!    → 存在多 DTB 候选选择逻辑
kernel/ramdisk addresses overlap
  with aboot addresses.                   → 校验地址区间不得与 aboot 自身重叠
decompress address overlap ...            → 解压目标地址也参与重叠检查
```

## 3. 已确认的刷写行为

| 操作 | 结果 |
|---|---|
| `fastboot oem unlock` / `unlock-go` / `flashing unlock` | `oem unlock is not allowed` |
| `fastboot oem device-info` | `Device unlocked: false`、`secure: yes` |
| `fastboot flash boot <img>` | **成功**（锁定态） |
| `fastboot boot <img>` | `bootimage: incomplete or not signed` |
| 无有效签名、改写 cmdline 的镜像 | **正常启动**（`/proc/cmdline` 确认） |
| 格式不合规的镜像 | 掉入 EDL 9008（`VID_05C6 PID_9008`），可按键组合救回 |

结论：**刷写通道开放、启动不校验签名，但镜像格式必须严格符合 aboot 预期**。
格式错误时 aboot 不会留在 fastboot，而是进入 EDL，需要「电源键 15 秒 + 音量下+电源」回到 fastboot。

## 4. 组装命令

```bash
cat vmlinuz dtb > kernel            # 严禁插入对齐填充
python3 tools/bootimg.py pack bi out.img \
  --page 4096 --header-version 0 \
  --kernel-addr 0x80008000 --ramdisk-addr 0x81000000 --tags-addr 0x80000100 \
  --cmdline "console=tty0 ..."
```

## 5. 失败模式与恢复

| 现象 | 含义 | 处理 |
|---|---|---|
| 黑屏 + 主机出现 `Qualcomm HS-USB QDLoader 9008` | aboot 拒绝镜像，进入 EDL | 电源键 15s，再按住音量下+电源进 fastboot，回刷 `stock-boot.img` |
| 黑屏 + 主机无任何设备 | 内核启动但无输出 | 需帧缓冲或 USB 网络通路 |
| 屏幕出现内核日志 | 成功 | — |
