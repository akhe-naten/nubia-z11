#!/usr/bin/env python3
"""qcom-wled 过压保护中断风暴的根因修复（Z11 / PMI8996）。

现象（2026-10-20 实测）：`wled_ovp_irq` 以 **约 22,400 次/秒** 的速度触发，
累计 7,000 万次；`irq/77-wled_ovp` 线程吃掉大量 CPU，整机 load 2.9，
phoc 在 49 分钟内吃掉 25 分 CPU，桌面像冻住。dmesg 反复出现
`Unbalanced enable for IRQ 77`（`kernel/irq/manage.c:754`）。

根因有两层：

1. `wled_ovp_work()` 无条件 `enable_irq()`。
   `wled_module_enable(1)` 可能被调用多次（亮度更新、以及 OVP 处理函数里的
   自动串数检测），每次都会排一个 delayed work；当 IRQ 已经使能时再
   enable 一次，使能计数就失衡（就是那条 Unbalanced 警告）。
2. `wled_ovp_irq_handler()` 在 OVP 故障时跑 `wled_auto_string_detection()`，
   而该函数会**重新开关模块**；每次软启动又会制造新的 OVP 边沿 →
   故障 → 自动检测 → 再故障，形成无上限的正反馈。

修复：
* 用 `ovp_irq_enabled` 显式跟踪使能状态，只在真正的 0→1 边沿 enable、
  在 1→0 边沿 disable，计数永远配对；
* 自动串数检测限速到 2 Hz，把正反馈从"硬件能跑多快就多快"压到可接受范围。

不改任何 DT 参数：硬件配置保持原样，修的是驱动里的两个逻辑缺陷。
"""

import pathlib
import sys

CANDIDATES = [
    pathlib.Path("drivers/leds/backlight/qcom-wled.c"),
    pathlib.Path("drivers/video/backlight/qcom-wled.c"),
]

p = next((c for c in CANDIDATES if c.exists()), None)
if p is None:
    sys.exit("wled patch: qcom-wled.c not found")

s = p.read_text()
orig = s

# --- 1) struct wled: 增加状态位 ------------------------------------------
anchor = "\tint ovp_irq;\n"
if anchor not in s:
    sys.exit("wled patch: 'int ovp_irq;' not found in struct wled")
s = s.replace(
    anchor,
    anchor + "\tbool ovp_irq_enabled;\n\tunsigned long ovp_last_jiffies;\n",
    1,
)

# --- 2) wled_ovp_work: 只在 0->1 边沿使能 --------------------------------
old_work = """static void wled_ovp_work(struct work_struct *work)
{
	struct wled *wled = container_of(work,
					 struct wled, ovp_work.work);
	enable_irq(wled->ovp_irq);
}"""
new_work = """static void wled_ovp_work(struct work_struct *work)
{
	struct wled *wled = container_of(work,
					 struct wled, ovp_work.work);

	/*
	 * wled_module_enable() can run again while the IRQ is already
	 * enabled (brightness updates and the OVP handler's own string
	 * detection both schedule this work). Enabling twice desynchronises
	 * the IRQ enable count - "Unbalanced enable for IRQ" - and on
	 * PMI8996 with the Z11 panel produced a storm of ~22k IRQ/s.
	 * Only re-enable on a real 0 -> 1 edge.
	 */
	if (wled->ovp_irq_enabled)
		return;

	wled->ovp_irq_enabled = true;
	enable_irq(wled->ovp_irq);
}"""
if old_work not in s:
    sys.exit("wled patch: wled_ovp_work body not found")
s = s.replace(old_work, new_work, 1)

# --- 3) 关闭模块时的 disable 也要配对 ------------------------------------
old_dis = """			if (!cancel_delayed_work_sync(&wled->ovp_work))
				disable_irq(wled->ovp_irq);"""
new_dis = """			if (!cancel_delayed_work_sync(&wled->ovp_work) &&
			    wled->ovp_irq_enabled) {
				wled->ovp_irq_enabled = false;
				disable_irq(wled->ovp_irq);
			}"""
if old_dis not in s:
    sys.exit("wled patch: disable_irq site not found")
s = s.replace(old_dis, new_dis, 1)

# --- 4) 自动串数检测限速 ------------------------------------------------
old_det = """	if (fault_sts & WLED3_CTRL_REG_OVP_FAULT_BIT) {
		if (wled->wled_auto_detection_required(wled)) {
			mutex_lock(&wled->lock);
			wled_auto_string_detection(wled);
			mutex_unlock(&wled->lock);
		}
	}"""
new_det = """	if (fault_sts & WLED3_CTRL_REG_OVP_FAULT_BIT) {
		/*
		 * String detection turns the module off and on again, and
		 * every soft start generates fresh OVP edges. Running it on
		 * every fault is an unbounded feedback loop, so cap it at 2 Hz.
		 */
		if (wled->wled_auto_detection_required(wled) &&
		    time_after(jiffies, wled->ovp_last_jiffies +
			       msecs_to_jiffies(500))) {
			wled->ovp_last_jiffies = jiffies;
			mutex_lock(&wled->lock);
			wled_auto_string_detection(wled);
			mutex_unlock(&wled->lock);
		}
	}"""
if old_det not in s:
    sys.exit("wled patch: auto detection site not found")
s = s.replace(old_det, new_det, 1)

if s == orig:
    sys.exit("wled patch: no changes applied")

p.write_text(s)
print(f"qcom-wled patched OK: {p}")
