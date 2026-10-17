# Nubia Z11 (NX531J) → 原生 Linux 实施规划

> 生成时间：2026-10-17 · 设备序列号 2d371d63 · 主机 Windows 10/11 + platform-tools 36.0.0

## 一、现状核查（已完成）

| 项目 | 结果 |
|---|---|
| 机型 / SoC | NX531J / Qualcomm MSM8996 (Snapdragon 820) + PMI8996，aarch64 |
| 当前系统 | 第三方 Android 9，内核 `3.18.134-mk_EAS`，已装 Magisk（adb shell 为 root） |
| Bootloader | **锁定**（`Device unlocked: false`，`secure: yes`），`bootimage: incomplete or not signed` |
| OEM 解锁开关 | ROM 未暴露 `sys.oem_unlock_allowed`，nubia 官方解锁通道已关闭 |
| 分区 | 47 个，boot 64MB / system 4GB / userdata 111.8GB，GPT 结构标准 |
| 全分区备份 | **已完成** `backup_full/`，44 分区全部双向 MD5 通过，426.5 MiB，含 xbl/aboot/tz/hyp/pmic/devcfg/persist/modem + GPT 头尾 |

### 硬件清单（决定驱动难度）

| 部件 | 型号 | mainline 支持判断 |
|---|---|---|
| 显示屏 | JDI R63452 1080p 5.5" DSI **command mode**，单 DSI | ✅ 内核 5.17+ 自带 `panel-jdi-fhd-r63452`（小米 5 同款） |
| 触摸 | Synaptics DSX，I²C `12-0020` | ⚠️ 地址 0x20 为 RMI4 标准地址，`rmi4_i2c` 有较大概率可用，需实测 |
| PMIC / 充电 | PM8994 + PMI8996、SMB1351、qpnp-wled 背光 | ✅ msm8996 mainline 已覆盖 |
| 音频 | AK4376 codec + TFA9890 功放 | ⚠️ AK4376 无 mainline 驱动，先求"能响"用数字通路 |
| WiFi/BT | QCA6174（`bt_qca6174` 节点） | ✅ ath10k 支持 |
| 传感器 | BH1745、PN54x NFC、霍尔、气压 | 部分支持，非阻塞项 |

## 二、关键结论

1. **没有任何现成的 Nubia Z11 Linux 镜像**。postmarketOS 设备库中 msm8996 阵营只有小米 5/5S/Note2、ZUK Z2、一加 3、Axon 7 等，NX531J 未被收录。这不是"刷个包"，而是**从零移植一个设备端口**。
2. **msm8996 平台本身支持成熟**（社区近 mainline 树 `gitlab.com/msm8996-mainline/linux`，存储/USB/显示/GPU/pinctrl/I²C 均已工作），移植路径清晰：以最接近的机型（小米 5 `xiaomi-gemini`）为底座改 DTS。
3. **bootloader 锁定不构成障碍（已实测验证）**：
   - `oem unlock` / `unlock-go` / `flashing unlock` 全部被拒（`oem unlock is not allowed`）
   - 但 `fastboot flash` **在锁定态下可以正常写入**（misc/boot 均实测成功并落盘校验）
   - 关键实验：改写 boot 头部 cmdline（`z11probe=1`）、签名块失效的情况下刷入并成功启动，`/proc/cmdline` 确认参数生效
   - 结论：**aboot 允许刷写且正常启动路径不校验签名**，无需解锁、无需 EDL，可直接刷自编译内核
4. **回退成本可控**：完整备份 + 刷写通道开放。只要不改动 aboot/xbl/devcfg/tz/hyp，任何内核层失败都可用 `fastboot flash` 回滚。

## 三、分阶段计划

### P0 收尾（已完成）
- [x] 全分区备份 + MD5 校验 + manifest（44 分区，426.5 MiB）
- [x] 导出下游 FDT 并反编译为可读 DTS（`dts/z11_downstream.dts`，16,829 行）
- [x] 硬件清单采集
- [x] 自研 `bootimg.py`（打包/解包，字节级可复现验证通过）
- [x] 自研 `fdt2dts.py`（FDT→DTS 反编译，无外部依赖）
- [ ] 用户自行导出个人数据（本次未触发解锁，数据实际未丢失）

### P1 引导路径验证（已完成，无需解锁）
- [x] 确认 `fastboot flash` 在锁定态可用且真实落盘（全零覆盖 misc 后 MD5 验证）
- [x] 确认改签名 boot 镜像可正常启动（`z11probe=1` 生效）
- [x] 确认 `fastboot boot` 被拒（`bootimage: incomplete or not signed`），故只能走"刷写 boot 分区"路线
- [x] 恢复原始 boot / misc，字节级校验一致

### P2 构建环境（当前受阻）
- 本机 WSL 被安全策略拦截，无 Docker → **无法运行 pmbootstrap / 交叉编译内核**
- 可选：① 安全中心放行 `wsl.exe`（推荐，安装 Ubuntu 24.04 + pmbootstrap）
  ② 云端 CI（GitHub Actions 交叉编译，产物下载回本地）
  ③ 用户自备 Linux 主机/虚拟机（构建在 Linux，刷写仍走本机 fastboot）

### P3 设备端口（核心工作量）
1. 克隆 `pmaports/device/testing/device-xiaomi-gemini` → `device-nubia-z11`
2. 以 `msm8996-mainline/linux` 为内核源，新建 `arch/arm64/boot/dts/qcom/msm8996-nubia-z11.dts`：
   - 从下游 FDT（`z11_downstream.fdt`）提取：I²C 总线映射、GPIO/pinctrl、regulator、reserved-memory、面板 reset/te 引脚
   - 显示：`jdi,fhd-r63452` + qpnp-wled 背光
   - 触摸：`synaptics,rmi4-i2c` @0x20（备用 `12-0020` 引脚中断）
   - USB：按 Z11 实际走 usb2/usb3 配置 `dr_mode = "peripheral"`
3. `pmbootstrap init` → 选 `nubia-z11`，生成 boot.img（kernel + dtb + initramfs）

### P4 试启动（非破坏）
- `fastboot boot boot.img` 试跑，串口/USB 网络（172.16.42.1）确认是否点亮
- 若黑屏：优先排查 DSI 供电与 panel reset 引脚极性；用 `cont_splash` 简单帧缓冲保底看到内核日志
- 达成"看到开机画面 + USB 网络通"即视为里程碑

### P5 落地安装
- 方案 A（推荐）：Linux 装到 userdata 分区，Android 的 boot/recovery 不动，双系统切换
- 方案 B：直刷 boot 分区
- 方案 C：SD 卡启动（若 bootloader 支持）

## 四、工作量与预期（客观估计）

- P1 解锁：分钟级，但存在不可逆数据清除
- P2 环境：1–2 小时
- P3 端口 + P4 点亮：**数天到数周**，取决于显示与触摸首次命中率
- 达到"日用可跑"（通话/相机/传感器）：对无官方 Linux 支持的 2016 机型，预期不乐观；相机与 SSC 传感器 hub 在 msm8996 上至今无开源方案

## 五、阻塞项

**构建环境**：本机无 C 工具链，WSL 被安全策略拦截，且无 git 凭据/SSH 密钥，因此云端 CI 也需要先解决推送凭据。三条路择一：
1. 安全中心放行 `wsl.exe` → 装 Ubuntu 24.04 + pmbootstrap，构建-刷机闭环全部本地自动化（**推荐**，迭代最快）
2. 提供 GitHub 个人访问令牌或已建好的空仓库 → 我写好 workflow 并推送，产物回传本地刷写
3. 自备 Linux 机器编译，本机只做 fastboot

## 六、已就绪的移植素材

| 素材 | 位置 | 说明 |
|---|---|---|
| 下游设备树源码 | `dts/z11_downstream.dts` | 16,829 行；面板节点 L1668、触摸 `synaptics_dsx@20` L5237、`cont_splash_mem@83401000` L16774 |
| 面板初始化序列 | 同上 L1668 | `qcom,mdss_dsi_jdi_r63452_1080p_5p5_cmd`，可用于生成 mainline panel 驱动 |
| 触摸接线 | 同上 L5237 | `compatible="synaptics,dsx-i2c"`，I²C 0x20，irq GPIO 125 高有效 |
| 原始 FDT | `dts/z11_downstream.fdt` | 402,921 B |
| 镜像工具 | `tools/bootimg.py` | 打包/解包，v0 头部，page 4096，含签名块保留能力 |
| FDT 反编译 | `tools/fdt2dts.py` | 纯 Python |
| 分区备份 | `backup_full/` | 44 分区 + GPT，含 manifest |
