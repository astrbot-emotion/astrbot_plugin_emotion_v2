"""把历史对话重放成情绪状态，写回 state.json（迁移用）

用法：
    python tools/replay_import.py            # 只看结果，不写盘（默认）
    python tools/replay_import.py --write    # 真写（会先备份 state.json）
    python tools/replay_import.py --write --umo qq_aiocqhttp:FriendMessage:<你的QQ号>

做法：
  1. 读所有 logs/*.jsonl，按真实时间排序
  2. 用相邻消息的时间差做衰减，把每条喂进引擎（只走规则，不调 LLM）
  3. 结束后把"最后一条消息到现在"的空档也补上（心跳补算）
  4. 把结果写成插件能读的 state.json 结构
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
from pathlib import Path
import shutil
import time
from datetime import datetime

HERE = Path(__file__).resolve().parent            # <插件>/tools
PLUGIN = str(HERE.parent)                          # <插件>
BASE = str(Path(PLUGIN).parent.parent / "plugin_data" / Path(PLUGIN).name)
STATE = os.path.join(BASE, "state.json")

spec = importlib.util.spec_from_file_location("eng", os.path.join(PLUGIN, "engine.py"))
eng = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eng)


def load_rows(umo: str) -> list[dict]:
    rows = []
    for f in sorted(glob.glob(os.path.join(BASE, "logs", "*.jsonl"))):
        for line in open(f, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            if umo and r.get("umo") != umo:
                continue
            rows.append(r)
    rows.sort(key=lambda r: r.get("ts", ""))
    return rows


def replay(rows: list[dict]) -> tuple[eng.Session, list]:
    s = eng.Session()
    prev = None
    trail = []
    for r in rows:
        text = (r.get("text") or "").strip()
        if not text:
            continue
        try:
            t = datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        gap = 1.0 if prev is None else max(0.0, (t - prev).total_seconds() / 60.0)
        out = s.tick(text, hour=t.hour, idle_minutes=gap)
        trail.append({"ts": r["ts"], "text": text,
                      "judge": out["judge"]["label"], "her": out["her"]})
        prev = t
    return s, trail


def catch_up(s: eng.Session, last_ts: datetime) -> float:
    """把"最后一条消息 → 现在"这段空档补上（分 15 分钟一步，和心跳一致）。"""
    now = datetime.now()
    minutes = (now - last_ts).total_seconds() / 60.0
    steps = 0
    while minutes > 0.5:
        step = min(15.0, minutes)
        s.heartbeat(step)
        minutes -= step
        steps += 1
        if steps > 20000:
            break
    return (now - last_ts).total_seconds() / 60.0


def to_state(s: eng.Session, umo: str, turns: int) -> dict:
    h = s.her
    return {
        "like": s.like, "turns": turns, "last_ts": time.time(),
        "le": h.le, "le_pulse": h.le_pulse, "le_base": s.le_base,
        "xi_base": h.xi_base, "xi_pulse": h.xi_pulse,
        "nu_base": h.nu_base, "nu_pulse": h.nu_pulse, "nu_harm": h.nu_harm,
        "ai_base": h.ai_base, "ai_pulse": h.ai_pulse,
        "ju_base": h.ju_base, "ju_pulse": h.ju_pulse,
        "si_base": h.si_base, "si_pulse": h.si_pulse,
        "you_base": h.you_base, "you_pulse": h.you_pulse,
        "fear_ledger": h.fear_ledger[-50:],
        "fuzzy": s.fuzzy.to_dict(),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="真写 state.json（默认只预览）")
    ap.add_argument("--umo", default="", help="只重放某个会话")
    args = ap.parse_args()

    rows = load_rows(args.umo)
    if not rows:
        print("没有可重放的日志")
        return
    umos = {r.get("umo") for r in rows}
    print(f"日志 {len(rows)} 条，会话 {len(umos)} 个")
    print(f"时间 {rows[0]['ts']} → {rows[-1]['ts']}")

    s, trail = replay(rows)
    last = datetime.strptime(rows[-1]["ts"], "%Y-%m-%d %H:%M:%S")
    gap_min = catch_up(s, last)

    print(f"\n重放完成：{len(trail)} 句，之后又空档了 {gap_min:.0f} 分钟")
    print("\n=== 重放得到的情绪 ===")
    comp = s.her.components()
    for k, v in s.her.values().items():
        print(f"  {k} {v:6.1f}   底 {comp[k]['底']:5.1f}  冲 {comp[k]['冲']:5.1f}")
    print(f"  喜欢(喜底) {s.like:.1f}   主导 {s.her.dominant(s.like, s.le_base)}")

    cur = {}
    if os.path.exists(STATE):
        try:
            cur = json.load(open(STATE, encoding="utf-8")).get("sessions", {})
        except Exception:
            cur = {}
    key = args.umo or next(iter(cur), None)
    if not key:
        key = sorted(umos)[0]
    print(f"\n=== 现在 state.json 里的 {key[-22:]} ===")
    old = cur.get(key)
    if old:
        oldv = {
            "喜": min(95, old.get("xi_base", 20) + old.get("xi_pulse", 0)),
            "怒": old.get("nu_base", 0) + old.get("nu_pulse", 0),
            "哀": min(95, old.get("ai_base", 0) + old.get("ai_pulse", 0)),
            "乐": old.get("le", 55) + old.get("le_pulse", 0),
            "惧": min(95, old.get("ju_base", 0) + old.get("ju_pulse", 0)),
            "思": old.get("si_base", 0) + old.get("si_pulse", 0),
            "忧": old.get("you_base", 0) + old.get("you_pulse", 0),
        }
        for k, v in oldv.items():
            print(f"  {k} {round(v, 1):6.1f}")
    else:
        print("  （没有这个会话的记录）")

    if not args.write:
        print("\n[预览模式] 加 --write 才会真的写盘（会先备份 state.json.bak）")
        return

    data = {"updated": time.time(), "sessions": dict(cur)}
    data["sessions"][key] = to_state(s, key, len(trail))
    if os.path.exists(STATE):
        shutil.copyfile(STATE, STATE + ".bak")
        print(f"\n已备份 → {STATE}.bak")
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"已写入 → {STATE}")
    print("**需要重启 AstrBot 才会生效**")


if __name__ == "__main__":
    main()
