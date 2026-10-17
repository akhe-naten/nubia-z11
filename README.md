# Nubia Z11 (NX531J) — 原生 Linux 移植

把努比亚 Z11（MSM8996 / Snapdragon 820）引导到 mainline Linux。

## 当前状态

| 阶段 | 状态 |
|---|---|
| 全分区备份（44 分区 + GPT，双向 MD5） | ✅ |
| 引导路径可行性验证（锁定态可刷写、启动不校验签名） | ✅ |
| 硬件清单 / 下游设备树反编译（16,829 行） | ✅ |
| 工具链（bootimg / fdt2dts / mkinitramfs / dtc） | ✅ |
| 设备树 `dts/msm8996-nubia-z11.dts` | ✅ 可编译 |
| boot.img 组装（gz 内核 + DTB，与原厂格式一致） | ✅ |
| 首次启动验证 | 进行中 |

## 关键结论

1. **无需解锁 bootloader**。`oem unlock` 被拒，但 `fastboot flash` 在锁定态下可写，且正常启动路径不校验 boot 签名（已用改写 cmdline 的镜像实测）。
2. **`fastboot boot` 不可用**（`bootimage: incomplete or not signed`），只能刷 `boot` 分区。
3. **boot.img 格式**：v0 头部 + `Image.gz` + **紧接**其后的 DTB，`kernel_size = gz长度 + dtb长度`。详见 [docs/boot-format.md](docs/boot-format.md)。
4. **没有现成的 Z11 端口**。本仓库是设备端口本身，不是"刷机包"。

## 硬件

| 部件 | 型号 | mainline 支持 |
|---|---|---|
| SoC | MSM8996 + PMI8996 | ✅ 成熟 |
| 屏 | JDI R63452 1080x1920 DSI cmd mode | ✅ `panel-jdi-fhd-r63452`（5.17+，小米 5 同款） |
| 触摸 | Synaptics DSX @ i2c 0x20, irq GPIO 125 | ⚠️ 需验证 `rmi4_i2c` |
| WiFi/BT | QCA6174 | ✅ ath10k |
| 音频 | AK4376 + TFA9890 | ❌ 无 mainline 驱动 |

## 仓库结构

```
dts/msm8996-nubia-z11.dts   设备树（自包含，无 #include，可直接 dtc 编译）
initramfs/init              最小 initramfs 启动脚本
tools/bootimg.py            Android boot 镜像打包/解包（含字节级复现校验）
tools/fdt2dts.py            FDT → DTS 反编译
tools/mkinitramfs.py        目录 → cpio.gz
tools/make_z11_dts.py       从 gemini DTS 派生 Z11 DTS
tools/backup_partitions.py  全分区备份 + MD5 校验
.github/workflows/           CI：编译 DTB + 组装 boot.img，产物可直刷
docs/boot-format.md         引导镜像格式逆向结论
docs/PLAN.md                分阶段实施规划
```

## 刷写

```bash
fastboot flash boot boot-z11.img
fastboot reboot
```

回滚（务必保留）：

```bash
fastboot flash boot stock-boot.img
```

## 备份

刷写前请务必完成全分区备份（`tools/backup_partitions.py`），至少覆盖
`xbl aboot tz hyp pmic devcfg persist modem` 与 GPT 头尾。这些是唯一的救砖底牌。
