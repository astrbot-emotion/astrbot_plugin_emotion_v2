"""记忆桥：把 LivingMemory 召回的记忆，译成她心里的分量。

单向、只读、零依赖：
- 不 import livingmemory 的任何代码（它是 AGPL-3.0，我们只碰它已经注入的文字）
- 只认 request.extra_user_content_parts 里 <RAG-Faiss-Memory>…</RAG-Faiss-Memory> 那一块
- 解析不出、没命中、出任何错 → 返回空信号，情绪照旧

为什么从注入片段里读，而不是再查一遍库：
1. LM 每条消息前已经做过一次混合检索，这里是它的结果，不再花第二次；
2. 读到的是"模型真正看到的那几条"，来龙去脉不会两边对不上。
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime

BLOCK_RE = re.compile(r"<RAG-Faiss-Memory>(.*?)</RAG-Faiss-Memory>", re.S)
ENTRY_SPLIT = re.compile(r"\n\s*(?=记忆 #\d+\s*/\s*Memory #\d+)")
HEAD_RE = re.compile(
    r"^记忆 #\d+\s*/\s*Memory #\d+\s*\(\s*Importance:\s*([\d.]+)\s*\)"
    r"(?:\s*,\s*Memory write time:\s*([\d\-: ]+?))?\s*$"
)
META_PREFIX = ("Topics:", "Participants:", "Key facts:", "Source time:")

# 记忆里的词 → 她心里的分量。只影响"底"，量很小：
# 一条"他明天考试"的记忆让她忧 +1~1.5，而他自己说一句"累"是 +14。
POS = ("开心", "喜欢", "爱你", "厉害", "谢谢", "太好了", "好耶", "棒", "乖",
       "考上", "过了", "成功", "拿到", "赢了", "顺利", "夸")
NEG = ("难受", "累", "想哭", "委屈", "孤独", "没力气", "怕", "痛",
       "失败", "没考上", "难过", "失望", "撑不住")
CARE = ("考试", "专升本", "考研", "面试", "实习", "加班", "熬夜", "生病", "感冒",
        "发烧", "医院", "打工", "答辩", "赶车", "比赛", "交论文", "复习")
PROMISE = ("答应", "说好", "约好", "下次", "以后", "永远", "一起", "兜底",
           "回来", "陪我", "等我", "不许忘")
CONFLICT = ("讨厌你", "烦死", "别烦", "滚", "不理你", "吵架", "生气", "失望")


def _part_text(part) -> str:
    t = getattr(part, "text", None)
    if isinstance(t, str):
        return t
    return part if isinstance(part, str) else ""


def _hash(text: str) -> str:
    return hashlib.md5(text[:200].encode("utf-8", "ignore")).hexdigest()[:16]


def _parse_ts(raw) -> float | None:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw).strip(), "%Y-%m-%d %H:%M").timestamp()
    except Exception:
        return None


def extract(request) -> list[dict]:
    """从这次 LLM 请求里，捡出 LM 已经注入的记忆条目。"""
    mems: list[dict] = []
    try:
        parts = getattr(request, "extra_user_content_parts", None) or []
    except Exception:
        return mems
    for part in parts:
        try:
            text = _part_text(part)
            for block in BLOCK_RE.findall(text or ""):
                for chunk in ENTRY_SPLIT.split(block):
                    chunk = chunk.strip()
                    if not chunk.startswith("记忆 #"):
                        continue
                    lines = chunk.splitlines()
                    head = HEAD_RE.match(lines[0].strip()) if lines else None
                    if not head:
                        continue
                    try:
                        importance = max(0.0, min(1.0, float(head.group(1))))
                    except Exception:
                        importance = 0.5
                    body = "\n".join(
                        ln.strip() for ln in lines[1:]
                        if ln.strip() and not ln.strip().startswith(META_PREFIX)
                    )
                    if not body:
                        continue
                    mems.append({
                        "key": _hash(body),
                        "text": body,
                        "importance": importance,
                        "ts": _parse_ts(head.group(2)),
                    })
        except Exception:
            continue
    return mems


def signals(mems: list[dict], seen: dict, now: float | None = None,
            max_new: int = 2, refresh_hours: float = 12.0) -> dict:
    """新记忆 → 情绪偏置。同一条记忆在 refresh_hours 内只算一次。

    返回 {} 表示"这轮没有新东西"，调用方直接跳过。
    """
    if not mems:
        return {}
    now = now or time.time()
    window = max(1.0, float(refresh_hours)) * 3600.0

    fresh = []
    for m in mems:
        last = float(seen.get(m["key"], 0) or 0)
        if now - last >= window:
            fresh.append(m)
    if not fresh:
        return {}

    fresh.sort(key=lambda m: (m["importance"], m["ts"] or 0), reverse=True)
    fresh = fresh[:max(1, int(max_new))]

    out = {"思": 0.0, "忧": 0.0, "喜": 0.0, "哀": 0.0, "乐": 0.0, "like": 0.0,
           "用": [], "新": []}
    for m in fresh:
        w = 0.6 + 0.4 * m["importance"]
        t = m["text"][:300]
        pos = any(k in t for k in POS)
        neg = any(k in t for k in NEG)
        promise = any(k in t for k in PROMISE)
        conflict = any(k in t for k in CONFLICT)
        if pos:
            out["喜"] += 0.03 * w          # 喜·底 半衰 30 天，给一点就够久
            out["乐"] += 0.30 * w
        if neg:
            out["哀"] += 0.50 * w
            out["思"] += 0.40 * w
        # 好事里的"复习/考试"不算担心；只有没被好事盖住的关切才抬忧
        if any(k in t for k in CARE) and not (pos and not neg):
            out["忧"] += 3.00 * w
        if promise:
            out["喜"] += 0.03 * w
            out["like"] += 0.02 * w
            out["思"] += 0.10 * w
        if conflict:
            out["哀"] += 0.80 * w
            out["思"] += 0.10 * w

        # 只有"有分量"的旧事（约定、冲突、难过）才会随时间变成思；
        # 好消息放久了只是旧消息，不会让她惦记。
        age_d = 0.0
        if m["ts"]:
            age_d = max(0.0, (now - m["ts"]) / 86400.0)
            if (promise or conflict or neg) and age_d >= 2:
                out["思"] += min(0.6, 0.06 * age_d) * w
            if (promise or conflict or neg) and age_d >= 14 and m["importance"] >= 0.6:
                out["思"] += 0.25 * w

        out["用"].append({
            "k": m["key"][:8],
            "重要": round(m["importance"], 2),
            "天前": round(age_d, 1),
            "句": t[:40],
        })
        out["新"].append(m["key"])

    # 每轮上限：记忆是背景，不是事件
    out["思"] = round(min(3.0, out["思"]), 2)
    out["忧"] = round(min(8.0, out["忧"]), 2)
    out["喜"] = round(min(0.2, out["喜"]), 2)
    out["哀"] = round(min(2.0, out["哀"]), 2)
    out["乐"] = round(min(0.6, out["乐"]), 2)
    out["like"] = round(min(0.06, out["like"]), 3)
    return out
