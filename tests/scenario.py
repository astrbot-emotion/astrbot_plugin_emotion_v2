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
check("思：喜100 + 12h ≈ 28~40", 28 <= s.her.si_base <= 40, s.her.si_base)

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

# ================================================================
# v11.7 新增行为的回归
# ================================================================

# ---------------------------------------------------------------- 剩余空间因子
h = eng.Her()
check("余量：接近上限时同一刺激加得少", h.gain(90, 95, 10) < 1.0, h.gain(90, 95, 10))
check("余量：空位多时几乎全额", h.gain(0, 95, 10) > 9.5, h.gain(0, 95, 10))
check("余量：负增量原样放行", h.gain(90, 95, -10) == -10, h.gain(90, 95, -10))

s = new()
xs = []
for _ in range(10):
    s.tick("我好想你", hour=21, idle_minutes=1)
    xs.append(s.her.values()["喜"])
check("余量：连说 5 次「想你」不到顶", xs[4] < 95, round(xs[4], 1))
check("余量：10 次才接近顶", xs[9] >= 90, round(xs[9], 1))

# ---------------------------------------------------------------- 逐情绪仲裁
merged, log = eng.arbitrate_per_emotion({"喜": 0.70, "忧": 0.60}, {"忧": 0.60})
check("仲裁：语义优先（词义没超 0.10）", merged.get("忧") == 0.60, merged)
check("仲裁：词义明显更强时胜出",
      eng.arbitrate_per_emotion({"喜": 0.80}, {"喜": 0.60})[0].get("喜") == 0.80)
check("仲裁：只有词义时用词义",
      eng.arbitrate_per_emotion({"乐": 0.4}, {})[0].get("乐") == 0.4)
check("仲裁：逐情绪都有理由", all(r["理由"] for r in log), log)

# ---------------------------------------------------------------- 否定词首字
check("否定：最喜欢你了 ≠ 厌弃", run("最喜欢你了")["judge"]["label"] == "喜")
check("否定：我很喜欢你了 ≠ 厌弃", run("我很喜欢你了")["judge"]["label"] == "喜")
check("否定：不喜欢你了 = 厌弃", run("不喜欢你了")["judge"]["label"] == "厌弃")

# ---------------------------------------------------------------- 喜→思→忧→惧 断链
s = new()
for _ in range(60):
    s.tick("你真厉害", hour=21, idle_minutes=1)
v = s.her.values()
check("断链：60 次夸赞 思 ≤ 25（曾被推到 100）", v["思"] <= 25, round(v["思"], 1))
check("断链：60 次夸赞 忧 ≤ 3（曾 95）", v["忧"] <= 3, round(v["忧"], 1))
check("断链：60 次夸赞 惧 ≤ 3（曾 95）", v["惧"] <= 3, round(v["惧"], 1))

# ---------------------------------------------------------------- 思：三分量 + 人/事
o = run("你还记得去年那天吗")
check("思·事：对事件的思不进思", o["her"]["思"] < 5, round(o["her"]["思"], 1))
o = run("我想你了")
check("思·人：对人的思进冲", o["her"]["思"] > 12, round(o["her"]["思"], 1))

s = new()
for _ in range(40):
    s.tick("你真厉害", hour=21, idle_minutes=1)
check("思·惦：惦记封顶 20", s.her.si_hold <= 20.001, round(s.her.si_hold, 2))
check("思·惦：底包含惦记", abs(s.her.components()["思"]["底"] - (s.her.si_base + s.her.si_hold)) < 0.11,
      s.her.components()["思"])

# ---------------------------------------------------------------- 日常小底
# 思的「离开一会儿」从 20 分钟起算，且随间隔线性增长
v5 = run("嗯", gap=5, xi=95)["her"]["思"]
v20 = run("嗯", gap=20, xi=95)["her"]["思"]
v60 = run("嗯", gap=60, xi=95)["her"]["思"]
v120 = run("嗯", gap=120, xi=95)["her"]["思"]
check("短间隔：5 分钟不算离开", v5 < 0.2, round(v5, 2))
check("短间隔：20 分钟起算", v20 >= 0.8, round(v20, 2))
check("短间隔：间隔越长思越多", v20 < v60 < v120, f"{v20:.1f}/{v60:.1f}/{v120:.1f}")

s = new(xi=95)
for _ in range(30):
    s.tick("今天天气不错", hour=20, idle_minutes=5)
check("短间隔：连贯快聊不给底", s.her.si_base < 1, round(s.her.si_base, 2))

o = run("嗯", gap=1440, xi=30)
check("小底：喜 30 不惦记", o["her"]["思"] < 1, round(o["her"]["思"], 1))

# ---------------------------------------------------------------- 深夜倦
s = new()
for _ in range(6):
    o = s.tick("今天好无聊", hour=23, idle_minutes=5)
check("深夜：倦 ≤ 60（0~100 刻度）", s.fuzzy.v["倦"] <= 60, s.fuzzy.v["倦"])
t = eng.tone_spec(s.her, s.fuzzy.v, o.get("judge"), s.like)
check("深夜：语气词没被压死", t["语气词"] > 0.08, round(t["语气词"], 2))
check("深夜：句长没被压死", t["句长"] > 0.15, round(t["句长"], 2))

# ---------------------------------------------------------------- 哀词表
for w in ("我好痛啊 痛啊", "疼哭了", "呜呜呜呜", "腿不在了，截了一条"):
    o = run(w)
    check(f"哀词表：{w[:8]} 判哀", o["judge"]["label"] == "哀", o["judge"]["label"])
check("哀词表：别哭了（安慰）不判哀", run("别哭了，我在")["judge"]["label"] != "哀")
check("哀词表：心疼你（关心）不判哀", run("心疼你")["judge"]["label"] != "哀")

# ---------------------------------------------------------------- 乐底跟随喜
a = new(xi=95)
for _ in range(96):
    a.heartbeat(15)
b = new(xi=20)
for _ in range(96):
    b.heartbeat(15)
check("乐底：喜高时底色更亮", a.le_base > b.le_base + 8,
      f"{a.le_base:.1f} vs {b.le_base:.1f}")

# ---------------------------------------------------------------- 汇总
print(f"共 {CHECKS} 项检查")
if FAILS:
    print(f"失败 {len(FAILS)} 项：")
    for f in FAILS:
        print("  ✗", f)
    sys.exit(1)
print("ALL CHECKS PASSED")

