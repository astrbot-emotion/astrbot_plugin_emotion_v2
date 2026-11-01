"""embedding 报警层（判定层之外，只决定"要不要叫 LLM"）

设计：
  · 规则判完后，若"拿不准"，算一次 embedding
  · 仅当 首名 ≥0.82 且 领先 ≥0.06 时，认为"这句可能有规则没覆盖的情绪"
  · 只返回 hint（组名），不参与任何数值计算

缓存：
  · 词表原型向量存 embeddings.json（按模型名 + 词表指纹），重启不重算
  · 同一句话的相似度结果也缓存
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
import urllib.request

API_PATH = "/embeddings"


class EmbedHint:
    TOP_MIN = 0.82       # 第一名最低分
    MARGIN_MIN = 0.06    # 第一名领先第二名的幅度

    def __init__(self, data_dir: str, lexicon_groups: dict):
        self.data_dir = data_dir
        self.groups = lexicon_groups          # {组名: (词, 词...)}
        self.words: list[str] = []
        self.owner: list[str] = []
        for g, ws in lexicon_groups.items():
            for w in ws:
                if w:
                    self.words.append(w)
                    self.owner.append(g)
        self.wvec: list[list[float]] = []
        self.cache: dict[str, tuple] = {}
        self.cfg = self._read_cfg()
        self.ready = False

    # ---------------- 配置 ----------------
    def _read_cfg(self) -> dict:
        """从 AstrBot 的 cmd_config.json 里找 embedding provider。

        结构：provider[].embedding_model 只是引用，真正 base/key 在 provider_sources[]。
        """
        try:
            import json as _json
            data_root = os.path.dirname(os.path.dirname(self.data_dir))   # <AstrBot>/data
            path = os.path.join(data_root, "cmd_config.json")
            with open(path, "r", encoding="utf-8-sig") as f:
                data = _json.load(f)

            model, src_id = "BAAI/bge-m3", None
            for p in (data.get("provider") or []):
                if p.get("embedding_model"):
                    model = p["embedding_model"]
                    src_id = p.get("provider_source_id")
                    break

            for src in (data.get("provider_sources") or []):
                base = src.get("embedding_api_base")
                if not base:
                    continue
                if src_id and src.get("id") != src_id:
                    continue
                return {
                    "base": str(base).rstrip("/"),
                    "key": src.get("embedding_api_key", ""),
                    "model": model,
                }
        except Exception:
            pass
        return {}

    # ---------------- 底层调用 ----------------
    def _embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 32):
            body = {"model": self.cfg["model"], "input": texts[i:i + 32],
                    "encoding_format": "float"}
            req = urllib.request.Request(
                self.cfg["base"] + API_PATH, data=json.dumps(body).encode(), method="POST",
                headers={"Authorization": f"Bearer {self.cfg['key']}",
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                out.extend([d["embedding"] for d in json.loads(r.read())["data"]])
        return out

    @staticmethod
    def _cos(a, b) -> float:
        num = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return num / (na * nb) if na and nb else 0.0

    # ---------------- 初始化 ----------------
    def _cache_path(self) -> str:
        h = hashlib.sha256(("\n".join(self.words) + "|" +
                            self.cfg.get("model", "")).encode()).hexdigest()[:16]
        return os.path.join(self.data_dir, f"embeddings_{h}.json")

    def warmup(self) -> None:
        """算好词表原型向量（有缓存就用缓存）。"""
        if not self.cfg:
            return
        path = self._cache_path()
        try:
            if os.path.exists(path):
                data = json.load(open(path, "r", encoding="utf-8"))
                if len(data.get("vecs", [])) == len(self.words):
                    self.wvec = data["vecs"]
                    self.ready = True
                    return
        except Exception:
            pass
        try:
            self.wvec = self._embed(self.words)
            json.dump({"model": self.cfg.get("model"), "vecs": self.wvec},
                      open(path, "w", encoding="utf-8"))
            self.ready = True
        except Exception:
            self.ready = False

    # ---------------- 对外 ----------------
    def hint(self, text: str) -> str | None:
        """返回"最像的词表组"或 None。调用失败一律返回 None（不影响主流程）。"""
        if not self.ready or not text.strip():
            return None
        key = text.strip()[:60]
        now = time.time()
        if key in self.cache:
            ts, val = self.cache[key]
            if now - ts < 6 * 3600:
                return val

        try:
            tv = self._embed([text])[0]
        except Exception:
            return None

        best: dict[str, float] = {}
        for i, g in enumerate(self.owner):
            c = self._cos(tv, self.wvec[i])
            if c > best.get(g, 0.0):
                best[g] = c
        if not best:
            return None
        ranked = sorted(best.items(), key=lambda x: -x[1])
        top_g, top_v = ranked[0]
        margin = top_v - ranked[1][1] if len(ranked) > 1 else top_v

        out = top_g if (top_v >= self.TOP_MIN and margin >= self.MARGIN_MIN) else None
        self.cache[key] = (now, out)
        return out
