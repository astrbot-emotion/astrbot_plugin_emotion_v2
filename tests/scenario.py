"""自检脚本：改参数后跑一遍，确认没把别处碰坏

用法：
    python tests/scenario.py

全部通过会打印 ALL CHECKS PASSED；有失败会逐条列出并以退出码 1 结束。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("engine", ROOT / "engine.py")
eng = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eng)

FAILS: list[str] = []
CHECKS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILS.append(f"{name}  {detail}")


def near(actual: float, expect: float, tol: float = 3.0) -> bool:
    return abs(actual - expect) <= tol


def new(xi: float | None = None) -> eng.Session:
    s = eng.Session()
    if xi is not None:
        s.her.xi_base = float(xi)
        s.like = float(xi)
    return s


def run(text: str, gap: float = 1, xi: float | None = None) -> dict:
    s = new(xi)
    return s.tick(text, hour=21, idle_minutes=gap)


# ---------------------------------------------------------------- 判定层
o = run("今天好累")
check("哀：判成哀", o["judge"]["label"] == "哀", o["judge"])
check("哀：哀值 > 2", o["her"]["哀"] > 2, o["her"]["哀"])

o = run("你真厉害", xi=50)
check("喜：判成喜", o["judge"]["label"] == "喜", o["judge"])
check("喜：喜值 > 55", o["her"]["喜"] > 55, o["her"]["喜"])

o = run("我要永远删除你")
check("威胁：标签", o["judge"]["label"] == "威胁", o["judge"])
check("威胁：惧 > 50", o["her"]["惧"] > 50, o["her"]["惧"])
check("威胁：怒被压低 < 25", o["her"]["怒"] < 25, o["her"]["怒"])

o = run("逗你的，别怕")
check("安抚：标签", o["judge"]["label"] == "安抚", o["judge"])

o = run("我真讨厌你")
check("厌弃：标签", o["judge"]["label"] == "厌弃", o["judge"])
check("厌弃：惧 15~45", 15 <= o["her"]["惧"] <= 45, o["her"]["惧"])
check("厌弃：哀 > 10", o["her"]["哀"] > 10, o["her"]["哀"])

# ---------------------------------------------------------------- 情绪层
s = new()
for _ in range(3):
    o = s.tick("哈哈哈你好逗", hour=20, idle_minutes=1)
check("乐：连逗三次 ≥ 70", o["her"]["乐"] >= 70, o["her"]["乐"])

s = new()
for _ in range(20):
    s.her.self_fail()
    s.her.step(30, s.le_base)
check("怒：失败 20 次到顶 90~95", 90 <= s.her.values()["怒"] <= 95, s.her.values()["怒"])

s = new()
s.her.xi_base, s.like = 100, 100
s.tick("我喜欢你", hour=10, idle_minutes=1)
for _ in range(48):                      # 12 小时心跳
    s.heartbeat(15)
check("思：喜100 + 12h ≈ 30~40", 30 <= s.her.si_base <= 40, s.her.si_base)

o = run("嗯", gap=1440, xi=100)          # 喜 100，隔一天
check("思：喜100 隔 1 天 ≈ 72", near(o["her"]["思"], 72, 4), o["her"]["思"])

o = run("嗯", gap=1440, xi=30)           # 喜不够，不该起思
check("思：喜 30 不起思", o["her"]["思"] < 1, o["her"]["思"])

o = run("我好累，最近总熬夜")
check("忧：诉苦后 > 5", o["her"]["忧"] > 5, o["her"]["忧"])

# ---------------------------------------------------------------- 主导与压制
s = new()
s.her.le, s.her.le_pulse = 95, 5
s.her.xi_base, s.her.nu_base, s.her.ai_base = 95, 95, 95
s.her.ju_base, s.her.si_base, s.her.you_base = 95, 95, 95
check("主导：全顶满 = 惧", s.her.dominant(95, s.le_base).startswith("惧"),
      s.her.dominant(95, s.le_base))

s = new()
s.her.ju_pulse, s.her.nu_pulse = 95, 90
check("压制：惧高时怒被压到 30% 附近", s.her.values()["怒"] < 35, s.her.values()["怒"])

# ---------------------------------------------------------------- 时间 vs 对话
a = new()
a.her.ju_pulse = 100
a.her.step(720 * 60, a.le_base)
b = new()
b.her.ju_pulse = 100
for _ in range(48):
    b.heartbeat(15)
check("衰减：一次 12h ≈ 48 次心跳", abs(a.her.ju_pulse - b.her.ju_pulse) < 0.5,
      f"{a.her.ju_pulse:.2f} vs {b.her.ju_pulse:.2f}")

# ---------------------------------------------------------------- 词表
check("词表：来源已加载", eng.LEXICON_SOURCE in ("lexicon.yaml", "builtin"),
      eng.LEXICON_SOURCE)
check("词表：PRAISE 非空", len(eng.PRAISE) > 3, eng.PRAISE)

# ---------------------------------------------------------------- 汇总
print(f"共 {CHECKS} 项检查")
if FAILS:
    print(f"失败 {len(FAILS)} 项：")
    for f in FAILS:
        print("  ✗", f)
    sys.exit(1)
print("ALL CHECKS PASSED")

