#!/usr/bin/env python3
"""qcom-wled 过压保护中断风暴的根因修复（Z11 / PMI8996）。

实测（2026-10-20）：`wled_ovp_irq` 约 **22,400 次/秒**，累计 7,000 万次；整机 load 2.9，
phoc 49 分钟吃掉 25 分 CPU，桌面像冻住；dmesg 反复出现
`Unbalanced enable for IRQ 77`（kernel/irq/manage.c:754）。

两层根因：
1. `wled_ovp_work()` 无条件 `enable_irq()`。`wled_module_enable(1)` 可能被调用多次
   （亮度更新 + OVP 处理函数里的自动串数检测），每次都排一次 delayed work，
   当 IRQ 已使能时再 enable 一次 → 使能计数失衡 → 就是那条 Unbalanced 警告。
2. `wled_ovp_irq_handler()` 遇 OVP 就跑 `wled_auto_string_detection()`，而它会
   重新开关模块；每次软启动又制造新的 OVP 边沿 → 故障→检测→再故障，无上限。

修复：
* 用 `ovp_irq_enabled` 显式跟踪状态，只在 0→1 使能、1→0 禁用，计数永远配对；
* 自动串数检测限速到 2 Hz，把正反馈压到可控。

不改任何 DT 参数：硬件配置保持原样，修的是驱动里的两个逻辑缺陷。

本脚本故意用正则而不是整段字符串匹配：CI 上的源码缩进/换行与本地摘要可能不一致，
逐字节匹配太脆。任何一处该改没改成都会直接退出非零，绝不放行。
"""

import pathlib
import re
import sys

CANDIDATES = [
    pathlib.Path("drivers/leds/backlight/qcom-wled.c"),
    pathlib.Path("drivers/video/backlight/qcom-wled.c"),
]

p = next((c for c in CANDIDATES if c.exists()), None)
if p is None:
    sys.exit("wled patch: qcom-wled.c not found in the expected paths")

s = p.read_text()
orig = s
applied = []

# --- 1) struct wled: 状态位 ----------------------------------------------
if "ovp_irq_enabled" not in s:
    m = re.search(r"^(\s*)int ovp_irq;\s*$", s, re.M)
    if not m:
        sys.exit("wled patch: 'int ovp_irq;' not found in struct wled")
    indent = m.group(1)
    s = (
        s[: m.end()]
        + f"\n{indent}bool ovp_irq_enabled;\n{indent}unsigned long ovp_last_jiffies;"
        + s[m.end():]
    )
    applied.append("struct state")

# --- 2) wled_ovp_work: 只在 0->1 边沿使能 --------------------------------
if "if (wled->ovp_irq_enabled)" not in s:
    pat = re.compile(
        r"static void wled_ovp_work\(struct work_struct \*work\)\s*\{.*?\n\}",
        re.S,
    )
    m = pat.search(s)
    if not m:
        sys.exit("wled patch: wled_ovp_work() not found")
    new = """static void wled_ovp_work(struct work_struct *work)
{
	struct wled *wled = container_of(work,
					 struct wled, ovp_work.work);

	/*
	 * wled_module_enable() can run again while the IRQ is already
	 * enabled (brightness updates and the OVP handler's own string
	 * detection both schedule this work). Enabling it twice desynchronises
	 * the IRQ enable count - "Unbalanced enable for IRQ" - and on PMI8996
	 * with the Z11 panel this produced a storm of ~22k IRQ/s.
	 * Only re-enable on a real 0 -> 1 edge.
	 */
	if (wled->ovp_irq_enabled)
		return;

	wled->ovp_irq_enabled = true;
	enable_irq(wled->ovp_irq);
}"""
    s = s[: m.start()] + new + s[m.end():]
    applied.append("ovp work guarded")

# --- 3) 关闭模块时 disable 也要配对 --------------------------------------
pat3 = re.compile(
    r"if \(!cancel_delayed_work_sync\(&wled->ovp_work\)\)\s*\n\s*disable_irq\(wled->ovp_irq\);"
)
if pat3.search(s):
    s = pat3.sub(
        """if (!cancel_delayed_work_sync(&wled->ovp_work) &&
			    wled->ovp_irq_enabled) {
				wled->ovp_irq_enabled = false;
				disable_irq(wled->ovp_irq);
			}""",
        s,
        count=1,
    )
    applied.append("disable guarded")
elif "wled->ovp_irq_enabled = false" not in s:
    sys.exit("wled patch: disable_irq site not found")

# --- 4) 自动串数检测限速 -------------------------------------------------
if "ovp_last_jiffies" in s and "msecs_to_jiffies(500)" not in s:
    pat4 = re.compile(
        r"if \(wled->wled_auto_detection_required\(wled\)\)\s*\{\s*\n\s*"
        r"mutex_lock\(&wled->lock\);\s*\n\s*wled_auto_string_detection\(wled\);\s*\n\s*"
        r"mutex_unlock\(&wled->lock\);\s*\n\s*\}",
        re.S,
    )
    m = pat4.search(s)
    if m:
        s = (
            s[: m.start()]
            + """if (wled->wled_auto_detection_required(wled) &&
		    time_after(jiffies, wled->ovp_last_jiffies +
			       msecs_to_jiffies(500))) {
			wled->ovp_last_jiffies = jiffies;
			mutex_lock(&wled->lock);
			wled_auto_string_detection(wled);
			mutex_unlock(&wled->lock);
		}"""
            + s[m.end():]
        )
        applied.append("detection rate-limited")
    else:
        sys.exit("wled patch: auto detection block not found (refusing to ship half a fix)")

if s == orig or len(applied) < 3:
    sys.exit(f"wled patch: incomplete, applied={applied}")

# jiffies/time_after 需要 jiffies.h；文件已用 HZ/schedule_delayed_work，
# 但显式包含一次，避免依赖传递包含。
if "#include <linux/jiffies.h>" not in s:
    m = re.search(r"^#include <linux/[^\n]*>\n", s, re.M)
    if m:
        s = s[: m.end()] + "#include <linux/jiffies.h>\n" + s[m.end():]
        applied.append("jiffies.h included")

p.write_text(s)
print(f"qcom-wled patched OK: {p}  applied={applied}")
