#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inject Z11 bring-up probes into a msm8996-mainline kernel tree.

Why anchor-insertion instead of a difffile: the CI builds a *tagged* kernel
(v6.19.5-msm8996) whose exact whitespace we cannot guarantee, and a failed
`git apply` wastes a whole CI run. Every anchor below is a single, unique
source line; if any of them does not appear exactly once the script aborts
before the (expensive) compile starts.

Usage:  python3 instrument.py <kernel-tree>
"""
import os
import sys

# (relative path, anchor, where, code)
#   where = "before" | "after"   -- position relative to the anchor *line*
EDITS = [
    # ---- gpiochip_add_data_with_key(): the silent window ------------------
    ("drivers/gpio/gpiolib.c", "ret = gpiodev_add_to_list_unlocked(gdev);",
     "before", 'pr_info("Z11[gpio] 1 list base=%d ngpio=%u\\n", base, gc->ngpio);'),
    ("drivers/gpio/gpiolib.c", "ret = init_srcu_struct(&gdev->srcu);",
     "before", 'pr_info("Z11[gpio] 2 srcu\\n");'),
    ("drivers/gpio/gpiolib.c", "ret = init_srcu_struct(&gdev->desc_srcu);",
     "before", 'pr_info("Z11[gpio] 3 desc_srcu\\n");'),
    ("drivers/gpio/gpiolib.c", "ret = gpiochip_set_names(gc);",
     "before", 'pr_info("Z11[gpio] 4 names\\n");'),
    ("drivers/gpio/gpiolib.c", "ret = gpiochip_init_valid_mask(gc);",
     "before", 'pr_info("Z11[gpio] 5 valid_mask\\n");'),
    ("drivers/gpio/gpiolib.c",
     "if (gc->get_direction && gpiochip_line_is_valid(gc, desc_index))",
     "before", 'if (!(desc_index & 31)) pr_info("Z11[gpio] 6 desc %u\\n", desc_index);'),
    ("drivers/gpio/gpiolib.c", "ret = of_gpiochip_add(gc);",
     "before", 'pr_info("Z11[gpio] 7 of_add\\n");'),
    ("drivers/gpio/gpiolib.c", "ret = gpiochip_add_pin_ranges(gc);",
     "before", 'pr_info("Z11[gpio] 8 pin_ranges\\n");'),
    ("drivers/gpio/gpiolib.c", "ret = gpiochip_irqchip_init_valid_mask(gc);",
     "before", 'pr_info("Z11[gpio] 9 irq_valid_mask\\n");'),
    ("drivers/gpio/gpiolib.c", "ret = gpiochip_irqchip_init_hw(gc);",
     "before", 'pr_info("Z11[gpio] 10 irq_init_hw\\n");'),
    ("drivers/gpio/gpiolib.c", "ret = gpiochip_add_irqchip(gc, lock_key, request_key);",
     "before", 'pr_info("Z11[gpio] 11 add_irqchip\\n");'),
    ("drivers/gpio/gpiolib.c", "ret = gpiochip_add_irqchip(gc, lock_key, request_key);",
     "after", 'pr_info("Z11[gpio] 11b add_irqchip ret=%d\\n", ret);'),
    ("drivers/gpio/gpiolib.c", "ret = gpiochip_setup_dev(gdev);",
     "before", 'pr_info("Z11[gpio] 12 setup_dev initialized=%d\\n", gpiolib_initialized);'),

    # ---- inside gpiochip_add_irqchip(): domain + chained handler ---------
    ("drivers/gpio/gpiolib.c", "if (gpiochip_hierarchy_is_hierarchical(gc)) {",
     "before", 'pr_info("Z11[irq] parent_domain=%px hierarchical=%d\\n", gc->irq.parent_domain, gpiochip_hierarchy_is_hierarchical(gc));'),
    ("drivers/gpio/gpiolib.c", "irq_set_chained_handler_and_data(gc->irq.parents[i],",
     "before", 'pr_info("Z11[irq] %u/%u chained handler START irq=%u\\n", i, gc->irq.num_parents, gc->irq.parents[i]);'),
    ("drivers/gpio/gpiolib.c", "gpiochip_set_irq_hooks(gc);",
     "before", 'pr_info("Z11[irq] chained handlers installed\\n");'),
    ("drivers/gpio/gpiolib.c", "ret = gpiochip_irqchip_add_allocated_domain(gc, domain, false);",
     "before", 'pr_info("Z11[irq] add_allocated_domain\\n");'),

    # ---- msm TLMM side ---------------------------------------------------
    ("drivers/pinctrl/qcom/pinctrl-msm.c",
     "ret = devm_gpiochip_add_data(pctrl->dev, &pctrl->chip, pctrl);",
     "before", 'pr_info("Z11[tlmm] gpiochip add irq=%d ngpio=%u\\n", pctrl->irq, pctrl->soc->ngpios);'),
    ("drivers/pinctrl/qcom/pinctrl-msm.c", "chained_irq_enter(chip, desc);",
     "before", 'pr_info_ratelimited("Z11[tlmm] summary IRQ fired\\n");'),
]


def indent_of(text, idx):
    ls = text.rfind("\n", 0, idx) + 1
    line = text[ls:idx]
    return line[:len(line) - len(line.lstrip())]


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: instrument.py <kernel-tree>")
    root = sys.argv[1]
    cache = {}
    applied = 0

    for rel, anchor, where, code in EDITS:
        path = os.path.join(root, rel)
        if path not in cache:
            if not os.path.exists(path):
                sys.exit(f"missing file: {rel}")
            cache[path] = open(path, encoding="utf-8").read()
        text = cache[path]

        n = text.count(anchor)
        if n != 1:
            sys.exit(f"anchor appears {n}x (expected 1) in {rel}:\n    {anchor}")

        idx = text.find(anchor)
        ind = indent_of(text, idx)
        if where == "before":
            ls = text.rfind("\n", 0, idx) + 1
            new = text[:ls] + ind + code + "\n" + text[ls:]
        else:
            le = text.find("\n", idx) + 1
            new = text[:le] + ind + code + "\n" + text[le:]
        cache[path] = new
        applied += 1

    for path, text in cache.items():
        open(path, "w", encoding="utf-8", newline="").write(text)

    print(f"instrumented {applied} sites in {len(cache)} files")


if __name__ == "__main__":
    main()
