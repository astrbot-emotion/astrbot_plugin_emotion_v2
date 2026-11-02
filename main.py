"""情绪系统 v2 · AstrBot 插件

每来一条消息：
1. 读 LivingMemory 刚注入的记忆（只读，memory_bridge），折成思/忧/喜/哀的"底"
2. 跑「判定层 → 模糊层 → 情绪层 → 语气层」
3. 语气按开关追加进 system_prompt；状态、日志、统计写进 WebUI 接口

记忆只读：不改记忆库、不额外查库，只认 LM 已经注入到本条消息里的那几条。

接口：
  GET  /state    当前状态（她的七情 + 模糊层 + 主导情绪 + 语气）
  GET  /log      最近 N 条日志（?limit=100）
  GET  /summary  今日统计
  POST /reset    重置当前会话状态
  GET  /memory   记忆桥：上一轮用了哪几条、加了多少
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from quart import jsonify, request
except Exception:  # pragma: no cover
    jsonify = None
    request = None

try:
    from . import embedding as embed_mod
except Exception:  # pragma: no cover
    embed_mod = None

try:
    from . import memory_bridge
except Exception:  # 作为脚本直接加载
    try:
        import importlib.util as _ilu2
        _mp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_bridge.py")
        _mspec = _ilu2.spec_from_file_location("memory_bridge_v2", _mp)
        memory_bridge = _ilu2.module_from_spec(_mspec)  # type: ignore
        _mspec.loader.exec_module(memory_bridge)  # type: ignore
    except Exception:  # pragma: no cover
        memory_bridge = None

try:
    from . import engine
except Exception:  # 作为脚本直接加载
    import importlib.util as _ilu
    _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine.py")
    _spec = _ilu.spec_from_file_location("engine_v2", _p)
    engine = _ilu.module_from_spec(_spec)  # type: ignore
    _spec.loader.exec_module(engine)  # type: ignore

PLUGIN_ID = "astrbot_plugin_emotion_v2"
VERSION = "3.1.0"
STATE_VERSION = 2       # 2 = 模糊层 0~100；1（或缺失）= 0~10，加载时自动 ×10

REJECT_SIGNAL = ("讨厌你", "不喜欢你了", "不想跟你说话", "不想理你", "恨你", "走开",
                 "离我远点", "你走吧", "腻了", "别联系")

LLM_PROMPT = """你是语气判读器。只输出 JSON，不要解释。

判断对方这句话属于哪一类，以及强度。

label 只能从这些里选：
  喜 / 怒 / 哀 / 乐 / 惧 / 厌弃 / 威胁 / 平静

- 厌弃：否定关系或否定人（讨厌你、不喜欢你了、不想理你、你走吧）
- 威胁：要删除/卸载/关掉/换掉 AI
- 其余按字面情绪

输出字段：label, strength(0~1), reason(≤20字)"""


def _root() -> str:
    # <AstrBot>/data/plugins/<plugin>/main.py → <AstrBot>/data
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _dir() -> str:
    d = os.path.join(_root(), "plugin_data", PLUGIN_ID)
    os.makedirs(os.path.join(d, "logs"), exist_ok=True)
    return d


def _state_path() -> str:
    return os.path.join(_dir(), "state.json")


def _log_path(day: str | None = None) -> str:
    day = day or datetime.now().strftime("%Y-%m-%d")
    return os.path.join(_dir(), "logs", f"{day}.jsonl")


def _read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _digest(data: dict) -> str:
    """只对状态本体取指纹：updated + sessions。"""
    blob = json.dumps(
        {"updated": data.get("updated"), "sessions": data.get("sessions") or {}},
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _digest_ok(data: dict) -> bool:
    """旧文件没有指纹，一律放行；有新指纹就必须对得上。"""
    sig = data.get("sha256")
    return True if not sig else sig == _digest(data)


def _rotate_backup(path: str) -> None:
    """把"当前这份、且校验通过"的状态留作 .bak。坏文件不覆盖好备份。"""
    cur = _read_json(path, None)
    if isinstance(cur, dict) and cur.get("sessions") is not None and _digest_ok(cur):
        try:
            _write_json(path + ".bak", cur)
        except Exception:
            pass


def _load_state() -> dict:
    """读主文件；坏了退 .bak；都不行就另存现场、从空白开始。"""
    path = _state_path()
    cur = _read_json(path, None)
    if isinstance(cur, dict) and cur.get("sessions") is not None:
        if _digest_ok(cur):
            return cur
        logger.warning("[emotion-v2] state.json 指纹不符，尝试回滚")
    elif cur is not None:
        logger.warning("[emotion-v2] state.json 结构异常，尝试回滚")
    elif os.path.exists(path):
        logger.warning("[emotion-v2] state.json 无法解析，尝试回滚")

    bak = _read_json(path + ".bak", None)
    if isinstance(bak, dict) and bak.get("sessions") is not None and _digest_ok(bak):
        logger.warning("[emotion-v2] 已从 state.json.bak 恢复")
        try:
            _write_json(path, bak)      # 立刻修复主文件，免得下次拿坏文件当备份
        except Exception:
            pass
        return bak

    if os.path.exists(path):
        try:
            os.replace(path, path + ".corrupt")
            logger.warning("[emotion-v2] 主文件与备份都不可用，现场已另存为 .corrupt")
        except Exception:
            pass
    return {}


@register(PLUGIN_ID, "local", "情绪系统 v2 · 四层引擎：判定/模糊/情绪/语气", VERSION)
class EmotionV2(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self.sessions: dict[str, engine.Session] = {}
        self._llm_hits: dict[str, list[float]] = {}
        self._llm_cache: dict[str, dict] = {}     # 同句缓存：text → {label, strength, reason}
        self._emb = None
        if embed_mod is not None:
            try:
                groups = {
                    "厌弃": tuple(engine.REJECT),
                    "生气": tuple(engine.SAY_ANGRY),
                    "命令": tuple(engine.DRIVE),
                    "责怪": tuple(engine.BLAME_HER),
                    "示弱": tuple(engine.WEAK) + tuple(engine.SELF_NEG),
                    "求证": tuple(engine.VERIFY),
                    "示好": tuple(engine.PRAISE) + tuple(engine.MISS),
                    "逗趣": tuple(engine.JOKE),
                    "放弃": tuple(engine.LETGO),
                }
                self._emb = embed_mod.EmbedHint(_dir(), groups)
            except Exception as e:
                logger.warning("[emotion-v2] embedding 初始化失败: %s", e)
        self._dirty = 0
        self._load()
        self._register_api()

    def _cfg(self, key: str, default=None):
        try:
            return (self.config or {}).get(key, default)
        except Exception:
            return default

    # ---------------- 持久化 ----------------
    def _load(self) -> None:
        data = _load_state()
        for umo, d in (data.get("sessions") or {}).items():
            s = engine.Session()
            try:
                s.like = d.get("like", 55.0)
                s.turns = d.get("turns", 0)
                s.last_ts = d.get("last_ts", 0.0)
                h = s.her
                h.le = d.get("le", 55.0)
                h.xi_base = d.get("xi_base", 20.0)
                h.xi_pulse = d.get("xi_pulse", 0.0)
                h.nu_base = d.get("nu_base", 0.0)
                h.nu_pulse = d.get("nu_pulse", 0.0)
                h.nu_harm = d.get("nu_harm", 0.0)
                h.ai_base = d.get("ai_base", 0.0)
                h.ai_pulse = d.get("ai_pulse", 0.0)
                h.ju_base = d.get("ju_base", 0.0)
                h.ju_pulse = d.get("ju_pulse", 0.0)
                h.le_pulse = d.get("le_pulse", 0.0)
                s.le_base = d.get("le_base", 55.0)
                h.si_base = d.get("si_base", 0.0)
                h.si_pulse = d.get("si_pulse", 0.0)
                h.you_base = d.get("you_base", 0.0)
                h.you_pulse = d.get("you_pulse", 0.0)
                h.fear_ledger = d.get("fear_ledger", [])
                # 模糊层 0~10 → 0~100 迁移（2026-11-01）：旧存档的最大值不超过 10
                _fz = d.get("fuzzy", {}) or {}
                _fz_max = max((float(x or 0) for x in _fz.values()), default=0.0)
                if _fz and _fz_max <= 10.0:
                    _fz = {k: float(v or 0) * 10.0 for k, v in _fz.items()}
                s.fuzzy.v.update(_fz)
                try:
                    s.mem_seen = {str(k): float(v)
                                  for k, v in (d.get("mem_seen") or {}).items()}
                except Exception:
                    s.mem_seen = {}
                s.last_memory = d.get("last_memory") or {}
                self._catch_up(s)
                self.sessions[umo] = s
            except Exception as e:
                logger.warning("[emotion-v2] 恢复会话失败 %s: %s", umo, e)
        logger.info("[emotion-v2] 已恢复 %d 个会话", len(self.sessions))

    @staticmethod
    def _catch_up(s, step_min: float = 15.0, max_steps: int = 672,
                  threshold_min: float = 60.0) -> None:
        """停机期间欠下的时间，按 15 分钟一步补上（最多 7 天）。

        不补的话，回来后一条消息会一次吞掉三天的衰减，耦合项（乐底压制、
        思的增长）被 min(dt, 3h) 截断，数值会跳。
        心跳累计的 hb_silence 同时保留，久别后的"疏离"照样触发。
        """
        try:
            if not s.last_ts:
                return
            gap = (time.time() - float(s.last_ts)) / 60.0
            if gap < threshold_min:
                return
            steps = min(max_steps, int(gap // step_min))
            for _ in range(steps):
                s.heartbeat(step_min)
            s.last_ts = time.time()
            logger.info("[emotion-v2] 停机追赶 %.0f 分钟（%d 步）", gap, steps)
        except Exception as e:
            logger.warning("[emotion-v2] 停机追赶失败: %s", e)

    def _save(self, force: bool = False) -> None:
        self._dirty += 1
        if not force and self._dirty % 5:
            return
        out = {"updated": time.time(), "state_version": STATE_VERSION, "sessions": {}}
        for umo, s in self.sessions.items():
            h = s.her
            out["sessions"][umo] = {
                "like": s.like, "turns": s.turns, "last_ts": s.last_ts,
                "le": h.le, "le_pulse": h.le_pulse, "le_base": s.le_base,
                "xi_base": h.xi_base, "xi_pulse": h.xi_pulse,
                "nu_base": h.nu_base, "nu_pulse": h.nu_pulse, "nu_harm": h.nu_harm,
                "ai_base": h.ai_base, "ai_pulse": h.ai_pulse,
                "ju_base": h.ju_base, "ju_pulse": h.ju_pulse,
                "si_base": h.si_base, "si_pulse": h.si_pulse,
                "you_base": h.you_base, "you_pulse": h.you_pulse,
                "fear_ledger": h.fear_ledger[-50:],
                "fuzzy": s.fuzzy.to_dict(),
                "mem_seen": dict(sorted(
                    getattr(s, "mem_seen", {}).items(), key=lambda kv: kv[1])[-300:]),
                "last_memory": getattr(s, "last_memory", {}) or {},
            }
        out["sha256"] = _digest(out)
        path = _state_path()
        try:
            _rotate_backup(path)
            _write_json(path, out)
        except Exception as e:
            logger.warning("[emotion-v2] 保存失败: %s", e)

    async def terminate(self) -> None:
        task = getattr(self, "_hb_task", None)
        if task:
            task.cancel()
        self._save(force=True)
        logger.info("[emotion-v2] 已退出，状态已保存")

    # ---------------- 心跳：没消息时也让时间走 ----------------
    def _hb_minutes(self) -> float:
        if getattr(self, "_hb_override", None):
            return float(self._hb_override)
        cfg = self._cfg("heartbeat", {}) or {}
        return float(cfg.get("interval_minutes", 15) or 15)

    def _hb_enabled(self) -> bool:
        cfg = self._cfg("heartbeat", {}) or {}
        return bool(cfg.get("enabled", True))

    async def initialize(self) -> None:
        self._hb_task = asyncio.create_task(self._heartbeat_loop())
        if self._emb is not None and self._cfg("embedding_hint", True):
            asyncio.create_task(self._emb_warmup())
        logger.info("[emotion-v2] 心跳已启动（默认 %s 分钟）", self._hb_minutes())

    async def _emb_warmup(self) -> None:
        try:
            await asyncio.to_thread(self._emb.warmup)
            logger.info("[emotion-v2] embedding 报警就绪（%s，%d 个原型）",
                        self._emb.cfg.get("model"), len(self._emb.words))
        except Exception as e:
            logger.warning("[emotion-v2] embedding 预热失败: %s", e)

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                minutes = max(1.0, self._hb_minutes())
                await asyncio.sleep(minutes * 60)
                if not self._hb_enabled():
                    continue
                await self._heartbeat_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("[emotion-v2] 心跳异常: %s", e)

    async def _heartbeat_once(self) -> None:
        now = time.time()
        changed = False
        for umo, s in list(self.sessions.items()):
            dt_min = (now - s.last_ts) / 60.0 if s.last_ts else 0
            if dt_min < 1:
                continue
            s.heartbeat(dt_min)
            s.last_ts = now
            changed = True
            try:
                self._write_share({"her": s.her.values()}, umo)
            except Exception:
                pass
        if changed:
            self._save(force=True)
            first = next(iter(self.sessions.values()), None)
            if first:
                v = first.her.values()
                logger.info("[emotion-v2] 心跳完成：会话 %d，首个会话 思 %.1f 忧 %.1f 惧 %.1f",
                            len(self.sessions), v["思"], v["忧"], v["惧"])

# ---------------- 语气注入开关 ----------------
    def _inject_path(self) -> str:
        return os.path.join(_dir(), "inject.json")

    def _inject_on(self) -> bool:
        if getattr(self, "_inject_override", None) is not None:
            return bool(self._inject_override)
        try:
            return bool(json.load(open(self._inject_path(), encoding="utf-8")).get("enabled", True))
        except Exception:
            return True

    def _set_inject(self, on: bool) -> None:
        self._inject_override = bool(on)
        try:
            json.dump({"enabled": bool(on)}, open(self._inject_path(), "w", encoding="utf-8"))
        except Exception as e:
            logger.warning("[emotion-v2] 写入 inject.json 失败: %s", e)

    # ---------------- 钩子：只观察 ----------------
    # AstrBot 按优先级降序执行。LivingMemory 的召回挂在默认 0，这里用 -50
    # 表示排在它后面：先让它把记忆注入进来，我们才读得到。
    @filter.on_llm_request(priority=-50)
    async def on_llm_request(self, event: AstrMessageEvent, request) -> None:
        try:
            text = (getattr(event, "message_str", "") or "").strip()
            if not text:
                return
            umo = ""
            for attr in ("unified_msg_origin", "umo"):
                v = getattr(event, attr, None)
                if v:
                    umo = str(v)
                    break
            umo = umo or "unknown"

            s = self.sessions.get(umo)
            if s is None:
                s = engine.Session()
                self.sessions[umo] = s

            now = datetime.now()
            ts = now.timestamp()
            # 沉默阈值（配置驱动）
            idle_cfg = self._cfg("idle", {}) or {}
            if bool(idle_cfg.get("enabled", True)):
                s.idle_medium_min = float(idle_cfg.get("medium_hours", 3)) * 60
                s.idle_long_min = float(idle_cfg.get("long_hours", 12)) * 60
            else:
                s.idle_medium_min = 1e9
                s.idle_long_min = 1e9
            idle_minutes = 0.0
            if s.last_ts > 0:
                idle_minutes = max(0.0, (ts - s.last_ts) / 60.0)
            s.last_ts = ts

            # ---- 记忆 → 情绪：先落地，再走这一句 ----
            mem_info = self._memory_apply(request, s, ts)

            out = s.tick(text, hour=now.hour, idle_minutes=idle_minutes)
            if mem_info:
                out["memory"] = mem_info
            self._write_share(out, umo)

            # 需要复核吗：规则拿不准，或 embedding 认为可疑
            need = self._need_llm(text, out)
            if not need and self._emb is not None and self._emb.ready \
                    and self._cfg("embedding_hint", True):
                try:
                    need = bool(await asyncio.to_thread(self._emb.hint, text))
                except Exception:
                    need = False

            if need and self._cfg("llm_review", True):
                # LLM 为准：同步等结果（默认最多 4 秒），超时就退回规则
                info = None
                try:
                    info = await asyncio.wait_for(
                        self._llm_review(umo, text, s),
                        timeout=float(self._cfg("llm_sync_timeout", 4)))
                except Exception:
                    info = None
                out = self._apply_llm_verdict(s, out, info)
                if info:
                    self._append_sample(text, info,
                                        (out.get("judge") or {}).get("label", "?"),
                                        info.get("cached", False))

            # ---- 语气层注入（在复核之后，注的是最终状态）----
            if self._inject_on():
                try:
                    _h = out["her"]
                    _dom = out.get("dominant", "")
                    _emo = " ".join(f"{k}{_h[k]:.1f}" for k in ("喜", "怒", "哀", "乐", "惧", "思", "忧")
                                    if _h.get(k, 0) >= 5)
                    _fz = "、".join(k for k, v in out["fuzzy"].items() if v >= 30)
                    _lines = ["", "【她此刻的状态（内部，不要直接说出来）】"]
                    if _emo:
                        _lines.append(f"情绪：{_emo}")
                    if _dom:
                        _lines.append(f"主导：{_dom}")
                    if _fz:
                        _lines.append(f"感受：{_fz}")
                    _tt = (out.get("tone_text") or "").strip()
                    if _tt:
                        _lines.append(f"说话方式：{_tt}")
                    _lines.append("以上只影响语气，不要复述这些词。")
                    _inject = "\n".join(_lines)
                    # 硬预算：超了先砍"感受"，再硬截。正常情况下用不到。
                    _budget = int(self._cfg("inject_budget", 240) or 0)
                    if _budget > 0 and len(_inject) > _budget:
                        _lines = [ln for ln in _lines if not ln.startswith("感受：")]
                        _inject = "\n".join(_lines)
                        if len(_inject) > _budget:
                            _inject = _inject[:_budget].rstrip() + "…"
                    request.system_prompt = (getattr(request, "system_prompt", "") or "") + _inject
                except Exception as e:
                    logger.info("[emotion-v2] 语气注入失败: %s", e)

            self._append_log(umo, text, out, now)
            self._save()
        except Exception as e:  # 绝不影响主流程
            logger.warning("[emotion-v2] 观察失败: %s", e)

    # ---------------- 记忆桥 ----------------
    def _memory_apply(self, request, s, ts: float) -> dict:
        """只读 LM 注入的记忆，折成偏置落到"底"上。返回给日志的一小段摘要。"""
        if memory_bridge is None:
            return {}
        cfg = self._cfg("memory_bridge", {}) or {}
        if not bool(cfg.get("enabled", True)):
            return {}
        try:
            mems = memory_bridge.extract(request)
            if not mems:
                return {}
            seen = getattr(s, "mem_seen", None)
            if not isinstance(seen, dict):
                seen = s.mem_seen = {}
            sig = memory_bridge.signals(
                mems, seen, now=ts,
                max_new=int(cfg.get("max_new", 2) or 2),
                refresh_hours=float(cfg.get("refresh_hours", 12) or 12))
            if not sig:
                return {"找到": len(mems), "新": 0}
            delta = s.apply_memory(sig)
            for k in sig.get("新", []):
                seen[k] = ts
            if len(seen) > 300:
                for k in sorted(seen, key=seen.get)[:len(seen) - 300]:
                    seen.pop(k, None)
            info = {
                "找到": len(mems),
                "新": len(sig.get("新", [])),
                "偏置": delta,
                "用": sig.get("用", []),
            }
            s.last_memory = info
            logger.info("[emotion-v2] 记忆桥：%s", info.get("用"))
            return info
        except Exception as e:
            logger.info("[emotion-v2] 记忆桥跳过: %s", e)
            return {}

    def _write_share(self, out: dict, umo: str | None = None) -> None:
        """把当前情绪写到 mood_share（按会话 + 全局各一份），供主动开口读。"""
        try:
            h = out.get("her") or {}
            share = {
                "喜": round(float(h.get("喜", 0) or 0), 1),
                "思": round(float(h.get("思", 0) or 0), 1),
                "忧": round(float(h.get("忧", 0) or 0), 1),
                "乐": round(float(h.get("乐", 0) or 0), 1),
                "ts": time.time(),
            }
            targets = [os.path.join(_dir(), "mood_share.json")]
            if umo:
                tag = hashlib.md5(umo.encode("utf-8")).hexdigest()[:10]
                targets.append(os.path.join(_dir(), f"mood_share_{tag}.json"))
            for p in targets:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(share, f, ensure_ascii=False)
        except Exception:
            pass

    def _apply_llm_verdict(self, s, out: dict, info: dict | None) -> dict:
        """LLM 复核已经改了状态（_llm_review 里落的），这里只把 out 重算一遍。"""
        if not info:
            return out
        try:
            lab = info.get("label")
            if lab and lab != "平静":
                out["judge"] = {
                    **(out.get("judge") or {}),
                    "label": lab,
                    "strength": info.get("strength", 0),
                    "why": "LLM复核",
                }
            out["her"] = s.her.values()
            out["parts"] = s.her.components()
            out["dominant"] = s.her.update_dominant(s.like, s.le_base)
            out["like"] = round(s.like, 1)
            out["tone"] = engine.tone_spec(s.her, s.fuzzy.v, out["judge"], s.like)
            out["tone_text"] = engine.tone_directives(out["tone"])
        except Exception as e:
            logger.info("[emotion-v2] 复核回写失败: %s", e)
        return out

    # ---------------- LLM 复核 ----------------
    def _need_llm(self, text: str, out: dict) -> bool:
        if not self._cfg("llm_review", True):
            return False
        lab = out["judge"]["label"]
        if lab in ("威胁", "厌弃", "安抚"):
            return False
        if out["fuzzy"].get("不确定", 0) >= 70:
            return True
        if out.get("negation_hint"):
            return True
        t = text.strip()
        if lab == "平静" and any(k in t for k in REJECT_SIGNAL):
            return True
        if ("你" in t or "她" in t) and any(k in t for k in ("不", "别", "没")) and len(t) <= 20:
            return True
        return False

    async def _embed_bg(self, umo: str, text: str, s, now: datetime) -> None:
        try:
            hint = await asyncio.to_thread(self._emb.hint, text)
            if not hint:
                return
            logger.info("[emotion-v2] embedding 指向「%s」，送去复核：%s", hint, text[:20])
            await self._llm_bg(umo, text, s, now)
        except Exception as e:
            logger.info("[emotion-v2] embedding 跳过: %s", e)

    async def _llm_bg(self, umo: str, text: str, s, now: datetime) -> None:
        try:
            info = await self._llm_review(umo, text, s)
            if not info:
                return
            self._append_sample(text, info, getattr(s, "last_judge", {}).get("label", "?"),
                                info.get("cached", False))
            if info["label"] == "平静":
                return
            rec = {
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "umo": umo, "text": text[:200],
                "judge": {"label": info["label"], "strength": info["strength"],
                          "why": "LLM复核", "scores": {}},
                "fuzzy": {k: v for k, v in s.fuzzy.v.items() if v > 0},
                "her": s.her.values(),
                "parts": s.her.components(),
                "dominant": s.her.dominant(s.like, getattr(s, "le_base", 55.0)),
                "like": round(s.like, 1),
                "tone": engine.tone_spec(s.her, s.fuzzy.v, getattr(s, "last_judge", None), s.like),
                "tone_text": engine.tone_directives(engine.tone_spec(s.her, s.fuzzy.v, getattr(s, "last_judge", None), s.like)),
                "idle": {}, "llm": info, "kind": "llm",
            }
            try:
                with open(_log_path(now.strftime("%Y-%m-%d")), "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.warning("[emotion-v2] 复核日志失败: %s", e)
            self._save(force=True)
        except Exception as e:
            logger.info("[emotion-v2] 后台复核异常: %s", e)

    async def _llm_review(self, umo: str, text: str, s) -> dict | None:
        # 同句缓存：同一句话第二次出现直接复用，不再调模型
        key = text.strip()[:80]
        cached = self._llm_cache.get(key)
        if cached:
            if cached["label"] != "平静":
                s.apply_llm(cached["label"], cached["strength"])
            return dict(cached, cached=True)

        # 限流：每小时 N 次
        now = time.time()
        hits = [x for x in self._llm_hits.get(umo, []) if now - x < 3600]
        if len(hits) >= int(self._cfg("llm_max_per_hour", 12)):
            return None
        hits.append(now)
        self._llm_hits[umo] = hits

        try:
            pid = await self.context.get_current_chat_provider_id(umo=umo)
            # 上下文扩到最近 8 句（不含本句）
            prior = s.recent[:-1][-8:]
            ctx = "\n".join(f"- {x[:60]}" for x in prior)
            prompt = f"上下文：\n{ctx}\n\n待判句：{text}\n\n只输出 JSON。"
            resp = await asyncio.wait_for(
                self.context.llm_generate(
                    chat_provider_id=pid,
                    prompt=prompt,
                    system_prompt=LLM_PROMPT,
                ),
                timeout=float(self._cfg("llm_timeout", 12)),
            )
            raw = (getattr(resp, "completion_text", "") or "").strip()
            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(raw)
            label = str(data.get("label", "")).strip()
            strength = float(data.get("strength", 0) or 0)
            if label not in ("喜", "怒", "哀", "乐", "惧", "厌弃", "威胁", "平静"):
                return None
            info = {"label": label, "strength": round(max(0.0, min(1.0, strength)), 2),
                    "reason": str(data.get("reason", ""))[:40]}
            # 写入同句缓存（最多 500 条，超了丢最早的）
            self._llm_cache[key] = dict(info)
            if len(self._llm_cache) > 500:
                for k in list(self._llm_cache)[:100]:
                    self._llm_cache.pop(k, None)
            if label != "平静":
                s.apply_llm(label, info["strength"])
            return info
        except Exception as e:
            logger.info("[emotion-v2] LLM 复核跳过: %s", e)
            return None

    def _append_sample(self, text: str, info: dict, engine_label: str, cached: bool) -> None:
        """攒训练样本：把 LLM 的判定存下来，供以后训练分类器用。"""
        try:
            rec = {
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "text": text[:200],
                "label": info.get("label"),
                "strength": info.get("strength"),
                "reason": info.get("reason", ""),
                "engine_label": engine_label,
                "cached": bool(cached),
            }
            with open(os.path.join(_dir(), "samples.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.info("[emotion-v2] 样本写入失败: %s", e)

    def _append_log(self, umo: str, text: str, out: dict, now: datetime) -> None:
        rec = {
            "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
            "umo": umo,
            "text": text[:200],
            "judge": out["judge"],
            "fuzzy": out["fuzzy"],
            "her": out["her"],
            "parts": out.get("parts") or {},
            "dominant": out["dominant"],
            "like": out["like"],
            "tone": out["tone"],
            "tone_text": out["tone_text"],
            "idle": out.get("idle") or {},
            "llm": out.get("llm") or None,
            "memory": out.get("memory") or None,
        }
        try:
            path = _log_path(now.strftime("%Y-%m-%d"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("[emotion-v2] 写日志失败: %s", e)

    # ---------------- Web API ----------------
    def _register_api(self) -> None:
        reg = getattr(self.context, "register_web_api", None)
        if not callable(reg):
            logger.warning("[emotion-v2] register_web_api 不可用")
            return
        reg(f"/{PLUGIN_ID}/state", self._api_state, ["GET"], "当前情绪状态")
        reg(f"/{PLUGIN_ID}/log", self._api_log, ["GET"], "最近日志")
        reg(f"/{PLUGIN_ID}/summary", self._api_summary, ["GET"], "今日统计")
        reg(f"/{PLUGIN_ID}/reset", self._api_reset, ["POST"], "重置会话状态")
        reg(f"/{PLUGIN_ID}/heartbeat", self._api_heartbeat, ["GET", "POST"], "查看/修改心跳间隔")
        reg(f"/{PLUGIN_ID}/sessions", self._api_sessions, ["GET"], "列出所有对话")
        reg(f"/{PLUGIN_ID}/inject", self._api_inject, ["GET", "POST"], "语气注入开关")
        reg(f"/{PLUGIN_ID}/memory", self._api_memory, ["GET"], "记忆桥：上一轮用了哪几条")

    def _pick_session(self, umo: str | None):
        if umo and umo in self.sessions:
            return umo, self.sessions[umo]
        if not self.sessions:
            s = engine.Session()
            self.sessions["default"] = s
            return "default", s
        umo = umo or next(iter(self.sessions))
        return umo, self.sessions[umo]

    async def _api_sessions(self):
        out = []
        for umo, s in self.sessions.items():
            try:
                label = umo.split(":")[-1] if ":" in umo else umo
                if umo.startswith("qq_aiocqhttp:"):
                    label = "QQ " + label
                out.append({
                    "umo": umo,
                    "label": label,
                    "turns": s.turns,
                    "like": round(s.like, 1),
                    "dominant": s.her.dominant(s.like, getattr(s, "le_base", 55.0)),
                    "last_ts": s.last_ts,
                })
            except Exception:
                continue
        out.sort(key=lambda x: -(x.get("last_ts") or 0))
        return jsonify({"sessions": out}) if jsonify else {"sessions": out}

    async def _api_memory(self):
        umo = None
        try:
            umo = (request.args.get("umo") if request else None)
        except Exception:
            pass
        key, s = self._pick_session(umo)
        out = {
            "umo": key,
            "enabled": bool((self._cfg("memory_bridge", {}) or {}).get("enabled", True)),
            "last": getattr(s, "last_memory", {}) or {},
            "seen": len(getattr(s, "mem_seen", {}) or {}),
        }
        return jsonify(out) if jsonify else out

    async def _api_state(self):
        umo = None
        try:
            umo = (request.args.get("umo") if request else None)
        except Exception:
            pass
        key, s = self._pick_session(umo)
        out = {
            "umo": key,
            "turns": s.turns,
            "like": round(s.like, 1),
            "her": s.her.values(),
            "parts": s.her.components(),
            "dominant": s.her.dominant(s.like, getattr(s, "le_base", 55.0)),
            "fuzzy": s.fuzzy.to_dict(),
            "tone": engine.tone_spec(s.her, s.fuzzy.v, getattr(s, "last_judge", None), s.like),
            "tone_text": engine.tone_directives(engine.tone_spec(s.her, s.fuzzy.v, getattr(s, "last_judge", None), s.like)),
            "memory": getattr(s, "last_memory", {}) or {},
            "sessions": list(self.sessions.keys()),
        }
        return jsonify(out) if jsonify else out

    async def _api_log(self):
        limit = 100
        umo = ""
        try:
            if request:
                limit = int(request.args.get("limit", 100))
                umo = (request.args.get("umo") or "").strip()
        except Exception:
            pass
        path = _log_path()
        rows: list[dict] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("[emotion-v2] 读日志失败: %s", e)
        if umo:
            rows = [r for r in rows if (r.get("umo") or "") == umo]
        rows = rows[-limit:]
        rows.reverse()
        return jsonify({"path": path, "count": len(rows), "rows": rows}) if jsonify else {"rows": rows}

    async def _api_summary(self):
        umo = ""
        try:
            if request:
                umo = (request.args.get("umo") or "").strip()
        except Exception:
            pass
        path = _log_path()
        stat = {"total": 0, "labels": {}, "dominants": {}, "fear_events": 0, "avg_like": 0.0}
        likes = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    if umo and (r.get("umo") or "") != umo:
                        continue
                    stat["total"] += 1
                    lab = (r.get("judge") or {}).get("label", "?")
                    stat["labels"][lab] = stat["labels"].get(lab, 0) + 1
                    dom = (r.get("dominant") or "?").split("(")[0]
                    stat["dominants"][dom] = stat["dominants"].get(dom, 0) + 1
                    if (r.get("judge") or {}).get("threat"):
                        stat["fear_events"] += 1
                    if isinstance(r.get("like"), (int, float)):
                        likes.append(r["like"])
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("[emotion-v2] 统计失败: %s", e)
        if likes:
            stat["avg_like"] = round(sum(likes) / len(likes), 1)
        return jsonify(stat) if jsonify else stat

    async def _api_inject(self):
        if request and request.method == "POST":
            try:
                data = await request.get_json(silent=True) or {}
                if "enabled" in data:
                    self._set_inject(bool(data["enabled"]))
                    logger.info("[emotion-v2] 语气注入 → %s", "开" if data["enabled"] else "关")
            except Exception as e:
                logger.warning("[emotion-v2] 切换注入失败: %s", e)
        return jsonify({"enabled": self._inject_on()}) if jsonify else {"enabled": self._inject_on()}

    async def _api_heartbeat(self):
        if request and request.method == "POST":
            try:
                data = await request.get_json(silent=True) or {}
                if data.get("minutes"):
                    self._hb_override = max(1.0, float(data["minutes"]))
                if "enabled" in data:
                    self._hb_override = self._hb_override or self._hb_minutes()
            except Exception as e:
                logger.warning("[emotion-v2] 心跳设置失败: %s", e)
        return jsonify({
            "enabled": self._hb_enabled(),
            "minutes": self._hb_minutes(),
            "sessions": len(self.sessions),
        }) if jsonify else {"minutes": self._hb_minutes()}

    async def _api_reset(self):
        umo = None
        try:
            if request:
                data = await request.get_json(silent=True) or {}
                umo = data.get("umo")
        except Exception:
            pass
        key, s = self._pick_session(umo)
        if key in self.sessions:
            self.sessions[key] = engine.Session()
        self._save(force=True)
        return jsonify({"ok": True, "umo": key}) if jsonify else {"ok": True}










