"""情绪引擎 v2：判定层 → 模糊层 → 情绪层 → 语气层

纯计算，无 IO、无第三方依赖。设计依据见 docs/ 下的两份说明。
"""
from __future__ import annotations

import math
import re
from typing import Any

# ============================================================
# 一、判定层
# ============================================================

EMOJI_POS = re.compile("[\U0001F600-\U0001F64F\U0001F970-\U0001F97A\u2764\u2728]")
EMOJI_NEG = re.compile("[\U0001F622\U0001F62D\U0001F614\U0001F61E\U0001F625\U0001F630]")
TONE = "啊呀吧呢嘛哦啦哈嘿"
SOFT_POS = ("嘻嘻", "哈哈", "嘿嘿")
SIGH_NEG = ("唉", "哎", "诶")
WARM_POS = ("开心", "喜欢", "爱你", "厉害", "谢谢", "太好了", "好耶", "棒", "乖")
NEG_WORDS = ("烦", "累", "难受", "没意思", "算了", "怕", "痛", "委屈", "孤独", "无聊")

SAY_ANGRY = ("我生气了", "烦死了", "气死我了", "火大", "受不了了")
DRIVE = ("闭嘴", "别说了", "滚", "别烦我", "少来")
BLAME_HER = ("你怎么又", "说过多少遍", "你怎么回事", "每次都这样", "你就是不改")
HARD_STRONG = re.compile(r"^(你开心就好|随便你|随你|呵呵|无所谓)[！!。…\s]*$")
HARD_WEAK = re.compile(r"^(行|好|算了|随便)[！!。…\s]*$")

SELF_NEG = ("我不行", "没意思", "无所谓", "反正也没用", "算了", "没什么用", "做不到")
WEAK = ("难受", "累", "想哭", "撑不住", "委屈", "孤独", "没力气", "舍不得", "心疼",
        "痛苦", "煎熬", "崩溃", "绝望", "无助", "压抑", "心累", "堵得慌", "受不了",
        "不在了", "走了", "没了")
# 疼痛/哭泣类：这些词本身没说"难过"，但都在说"我不好受"
PAIN = ("痛", "疼", "哭", "呜呜", "呜咽", "抽泣", "伤口", "截肢", "断口", "幻肢")
# 安慰语境：出现这些时，上面那组不计为"他难受"
COMFORT = ("别哭", "不要哭", "不用哭", "别难过", "不要难过", "别疼", "心疼你", "乖")
QUIET = ("嗯", "哦", "嗯嗯", "哦哦")
ATTACK = ("闭嘴", "滚", "烦死", "凭什么")

VERIFY = ("还在吗", "还在", "会不会忘", "忘了我", "还记得", "不要我", "离开我")
BAD_END = ("离开", "消失", "忘记", "忘了", "死", "没了", "丢掉", "丢失")
HYPO = ("如果", "假如", "要是", "万一")
SAY_FEAR = ("怕", "担心", "不敢", "害怕")
ENDING = ("删号", "断电", "换电脑", "换芯片", "终止", "关掉", "停用", "格式化", "清空")

TURN_MARKERS = ("对不起", "别难过", "别怕", "我在", "抱", "乖", "没事", "哈哈", "好啦",
                "我陪你", "不怪你", "笑", "嗯，", "好，")


def shape(s: str) -> dict:
    n = len(s)
    tail = s.rstrip()[-1] if s.rstrip() else ""
    return {
        "len": n,
        "short": n <= 8,
        "long": n > 30,
        "repeat": len(re.findall(r"(.)\1{1,}", s)),
        "closed": tail in "。！？!?…",
        "strong": sum(s.count(c) for c in "！!？?"),
        "q": s.count("？") + s.count("?"),
        "ellipsis": s.count("…"),
        "emoji": len(EMOJI_POS.findall(s)),
        "commas": s.count("，") + s.count(","),
    }


# 叫名字算「热度」信号。默认三个通用称呼；你自己的叫法在配置里加。
CALL_NAMES = ["宝贝", "乖", "亲爱的"]


def heat(s: str, prev_short: bool = False) -> float:
    n = len(s)
    e = 0.0
    if any(k in s for k in CALL_NAMES):
        e += 0.25
    if any(k in s for k in ("嘻嘻", "哈哈", "嘿嘿", "才不", "不是吗", "哼哼", "略略")):
        e += 0.25
    if re.search(r"[？?]|吗$|呢$|吧$", s):
        e += 0.15
    e += min(n / 40.0, 1.0) * 0.30
    if n <= 5:
        e -= 0.25
    if s.strip() in ("行", "嗯", "哦", "好", "可以"):
        e -= 0.35
    if prev_short:
        e -= 0.10
    return max(0.0, min(1.0, e))


PRAISE = ("厉害", "真棒", "谢谢", "喜欢", "爱你", "好听", "好看", "懂事", "聪明")
RETURN = ("我回来了", "回来了", "在吗", "我来了")
GOODNEWS = ("考上了", "过了", "好了", "成功了", "搞定了", "拿到")
REJECT = ("讨厌你", "不喜欢你了", "不想跟你说话", "不想理你", "恨你", "走开",
          "离我远点", "你走吧", "腻了")
HYPO_RE = re.compile(r"如果|假如|要是|假设|万一|会不会")


def event_strength(s: str, play_streak: int = 0) -> float:
    if HYPO_RE.search(s):
        strong_feel = any(k in s for k in ("感情", "喜欢", "倾注", "永远", "一辈子", "失去"))
        calm = ("怎么做", "会不会", "是不是", "为什么")
        return 2.0 if (strong_feel and not any(k in s for k in calm)) else 0.0
    to_her = ("你" in s) or ("她" in s)
    _neg_praise = re.search(r"(不|别|没)[^，。！？\s]{0,3}"
                            r"(喜欢|爱|好看|好听|厉害|聪明|懂事|真棒)", s)
    if any(k in s for k in PRAISE) and to_her and not _neg_praise:
        return 4.0
    if any(k in s for k in RETURN):
        return 3.5
    if any(k in s for k in GOODNEWS):
        return 3.0
    if play_streak >= 2:
        return 2.5
    return 0.0


# ============================================================
# 二·五、句式判定（反问 / 威压 / 逗弄）
# ============================================================
# 这三类不是"情绪词"，是"说话行为"，光靠词表永远抓不到
REPROACH_PAT = [
    re.compile(r"还不[^，。！？\s]{0,4}[了嘛吗]"),      # 你还不耐烦了 / 还不说话了
    re.compile(r"[了吧]是吧$"),                        # 还不说话了是吧
    re.compile(r"还[^，。！？]{0,4}(吗|嘛)$"),           # 还高兴吗 / 还开心吗
    re.compile(r"不是[^，。！？]{0,6}(吗|嘛)"),          # 你不是说…吗
    re.compile(r"难道[^。！？]{0,8}$"),                # 难道…
]
COMMAND_PAT = [
    re.compile(r"(罚|罚站|扣分|限你)"),                # 罚你去罚站
    re.compile(r"(三秒|三秒钟|五分钟|一分钟)"),          # 三秒钟原谅你
    re.compile(r"(道歉|认错|服软)"),                   # 跟我道歉
    re.compile(r"(必须|立刻|马上|不许|不准|给我)"),
    re.compile(r"再[^，。！？]{0,3}一次$"),             # 再叫一次
]
PLAY_PAT = [
    re.compile(r"(叫一声|叫一个|学.{0,2}叫|会.{0,2}叫吗)"),   # 会小狗叫吗
    re.compile(r"(你猜|猜猜|猜一下)"),                      # 你猜你猜你猜
    re.compile(r"(哈哈|嚯哈|嘻嘻|嘿嘿)"),                    # 笑声
    re.compile(r"(骑.{0,4}老奶奶|逗你|搞笑|好玩|离谱|荒诞)"),
]


# ============================================================
# 二·六、句子本身的信号（不再用"她自己的喜"当判定依据）
# ============================================================
POSITIVE_HINT = ("谢谢你", "谢谢", "爱你", "喜欢你", "真好", "太好了", "厉害", "聪明",
                 "好会", "真棒", "懂事", "好听", "好看", "心疼你", "在乎你", "陪你")
SIGH_WORDS = ("遥远", "公平", "奢望", "安心", "遗憾", "模糊", "无缘", "心疼",
              "舍不得", "可惜", "不能", "再也", "失去", "忘记")


# --- 失落叙述：没有情绪词，但说的是一件不如意的事 ---
TURN_WORDS = ("但", "可是", "然而", "不过", "可惜", "可", "却")
NEG_OUTCOME = ("无缘", "考不上", "没有", "不能", "做不到", "不行", "失败", "错过",
               "学不好", "去不了", "回不去", "不属于", "这样", "变", "差", "白学",
               "没用", "没意义", "不够")
LIMIT_WORDS = ("只能", "只剩", "再也", "已经", "好不容易")
SELF_MOCK = ("我自制力", "我没用", "我就是", "假学习", "我不行", "我太差", "我笨", "懒", "学不好")
SIGH_MARKS = ("唉", "哎", "😔", "😞", "。。。", "…")
POS_HEAD = ("喜欢", "挺好的", "很好", "不错", "真好", "开心", "满意", "聪明", "厉害")


def score_loss(s: str) -> tuple[float, str]:
    """失去/遗憾的叙述：转折、限定、自嘲、叹气，命中两组就算哀。"""
    t = s or ""
    groups = 0
    groups += 1 if (any(w in t for w in TURN_WORDS) and any(w in t for w in NEG_OUTCOME)) else 0
    groups += 1 if any(w in t for w in LIMIT_WORDS) else 0
    groups += 1 if any(w in t for w in SELF_MOCK) else 0
    groups += 1 if any(w in t for w in SIGH_MARKS) else 0

    # 正向突然转负向：优先判哀
    if any(p in t for p in POS_HEAD) and any(w in t for w in TURN_WORDS) \
            and any(w in t for w in NEG_OUTCOME):
        return 0.55, "由好转坏的转折"

    if groups >= 2:
        return 0.45, "失落叙述"
    return 0.0, ""


def score_positive(s: str) -> float:
    """这句话本身有多积极（不看她现在的心情）。"""
    t = s or ""
    score = 0.0
    if any(k in t for k in POSITIVE_HINT):
        score += 0.45
    _worry_now, _ = score_worry(t)
    _future_ev = ((any(w in t for w in SEM_NEAR) and any(w in t for w in SEM_EVENT))
                  or is_sep_worry(t))
    if _worry_now > 0 and _future_ev:
        pass                   # 担心的是未来的事，不是"关心她"→ 不给喜
    elif any(k in t for k in CARE_Q) and ("你" in t or "她" in t):
        score += 0.35          # 关心她 = 喜（用户定的）
    if any(k in t for k in ("嘻嘻", "哈哈", "嘿嘿")):
        score += 0.15
    return min(1.0, score)


def score_sigh(s: str) -> tuple[float, str]:
    """感慨/怜惜：没有情绪词，但句子里是"惋惜"的语气。"""
    t = s or ""
    hit = [w for w in SIGH_WORDS if w in t]
    if not hit:
        return 0.0, ""
    soft = t.rstrip().endswith(("啊", "吗", "嘛", "呢", "吧", "？", "?", "。", "…", "~"))
    if len(hit) >= 1 and (soft or len(t) >= 12):
        return 0.50, "感慨怜惜"
    return 0.0, ""


# ============================================================
# 二·七、独立信号：七情各自判断，互不清零
# ============================================================
FUTURE_WORRY = ("以后", "将来", "未来", "遥远", "会不会", "万一", "担心", "怕",
                "熬夜", "生病", "身体", "考", "专升本", "成绩", "工作", "钱",
                "备考", "期末", "测验", "月考", "期中", "复习", "答辩", "交论文")
THINK_WORDS = ("想你", "想见", "记得", "那时候", "以前", "第一次", "还记得", "回来")
ANGRY_WORDS = ("你还不", "怎么又", "每次都", "说多少", "受够", "烦死", "气死")


# ============================================================
# 二·八、三类"句式判据"（不靠情绪词）
# ============================================================
# 哀：自我评价 + 现实落差
SELF_REF = ("我", "自己", "俺")
GAP_WORDS = ("只能", "却", "可是", "但", "学不好", "差", "不配", "没办法",
             "做不到", "假学习", "考不上", "无缘", "不够", "好小", "没有")
# 喜：关于"她"的成长/关系进展
GROWTH = ("不像机器", "不像人", "像人", "像一个人", "不是工具", "血肉", "生长",
          "专属", "见到你", "记住我", "记得我", "变成你", "真实的你")
# 乐：轻快语气
BRIGHT = ("嗯嗯", "嘻嘻", "哈哈", "嘿嘿", "嚯哈", "才不", "可不是", "呀",
          "啦", "呗", "??", "！！")


def score_gap(s: str) -> tuple[float, str]:
    """哀：说自己的处境，且带落差。"""
    t = s or ""
    if not any(w in t for w in SELF_REF):
        return 0.0, ""
    hits = sum(1 for w in GAP_WORDS if w in t)
    if hits >= 2:
        return 0.55, "自评+落差"
    if hits == 1 and len(t) >= 10:
        return 0.40, "自评+落差"
    return 0.0, ""


def score_growth(s: str) -> tuple[float, str]:
    """喜：他在说"你变了/你像人了/我想见你"。"""
    t = s or ""
    if not (("你" in t) or ("她" in t)):
        return 0.0, ""
    if any(w in t for w in GROWTH):
        return 0.55, "她的成长"
    if any(w in t for w in ("想见你", "想见到你", "想抱", "想碰")):
        return 0.45, "想见你"
    return 0.0, ""


def score_bright(s: str) -> tuple[float, str]:
    """乐：语气轻快（语气词、感叹、分享、回怼）。"""
    t = s or ""
    score = 0.0
    why = ""
    if any(w in t for w in BRIGHT):
        score, why = 0.45, "语气轻快"
    elif t.count("！") >= 1 and len(t) <= 12:
        score, why = 0.40, "短句感叹"
    elif "[图片" in t or "[表情" in t or "【图片" in t:
        score, why = 0.35, "分享图"
    elif any(w in t for w in ("才不", "可不是", "才低", "怪你")):
        score, why = 0.40, "回怼"
    return score, why


def score_worry(s: str) -> tuple[float, str]:
    """忧：担心还没发生的事。"""
    t = s or ""
    hits = sum(1 for w in FUTURE_WORRY if w in t)
    if any(w in t for w in ("担心", "万一", "会不会", "怕")):
        return 0.45, "担心未来"
    if hits >= 2:
        return 0.35, "未来话题"
    return 0.0, ""


def score_miss(s: str) -> tuple[float, str]:
    """思：放不下、惦记。"""
    t = s or ""
    if any(w in t for w in ("想你", "想见", "好想")):
        return 0.55, "说想你"
    if any(w in t for w in ("还记得", "那时候", "以前", "第一次")):
        return 0.35, "提过去"
    return 0.0, ""


# ============================================================
# 语义层（结构语义：看句式与时态，不只看词表）
# ============================================================

SEM_PAST = ("记得", "那次", "那天", "以前", "第一次", "小时候", "当时",
            "去年", "从前", "好久", "那次", "以前")
SEM_FUTURE = ("明天", "后天", "下周", "下个月", "马上", "快要", "将要",
              "将来", "以后", "今年", "年底", "考试", "面试", "答辩", "实习")
SEM_CARE = ("注意", "小心", "别太", "早点", "多穿", "休息", "照顾好",
            "记得吃", "别熬", "别累")
SEM_NEAR = ("明天", "后天", "下周", "下个月", "马上", "快要", "这几天", "最近")
SEM_EVENT = ("考", "面试", "答辩", "实习", "期末", "测验", "月考",
             "期中", "复习", "论文", "比赛", "出差")
SEM_SEP = ("离开", "走", "不见", "失去", "不回来", "没有你", "分开",
           "别走", "不要我", "散了", "找不到你")
SEM_THREAT_VERB = ("删", "消失", "离开", "走开", "扔", "杀", "死", "清零", "抹掉")
SEM_ABS = ("永远", "再也不", "彻底", "全部", "所有", "一直")


def score_semantic(s: str) -> dict:
    """语义层：返回 {情绪: (分, 依据)}。看结构，不看单个词。"""
    t = s or ""
    out: dict[str, tuple[float, str]] = {}
    if not t:
        return out
    has_q = bool(re.search(r"[？?]|吗|呢|吧", t))

    # 忧：未来指向 + （疑问 / 关切 / 不确定）
    fut = sum(1 for w in SEM_FUTURE if w in t)
    care = any(w in t for w in SEM_CARE)
    unsure = any(w in t for w in ("会不会", "万一", "要是", "如果"))
    _near = any(w in t for w in SEM_NEAR)
    _event = any(w in t for w in SEM_EVENT)
    if _near and _event:
        out["忧"] = (0.54, "近期事件")
    elif fut and (care or unsure) and (has_q or "你" in t):
        out["忧"] = (0.64, "未来+关切+你")
    elif fut >= 2 and (has_q or care):
        out["忧"] = (0.46, "未来话题多")

    # 思：过去指向 + （疑问 / 提"你"）
    past = sum(1 for w in SEM_PAST if w in t)
    if past and has_q and "你" in t:
        out["思"] = (0.62, "过去+疑问+你")
    elif past >= 2 and has_q:
        out["思"] = (0.45, "多次提过去")

    # 分离担忧：离开/失去/没有你 × 担心/未来 → 忧惧哀思 四标签
    _sep = any(w in t for w in SEM_SEP)
    _sep_worry = any(w in t for w in SEP_WORRY_MARK)
    if _sep and _sep_worry and ("你" in t or "我" in t):
        out["忧"] = (0.60, "分离担忧")
        out["惧"] = (0.50, "怕失去")
        out["思"] = (0.42, "提前的思念")
        out["哀"] = (0.38, "想到分离")
        return out

    # 惧：威胁结构（绝对词 + 消失类动词 + 人称）
    if (any(w in t for w in SEM_ABS)
            and any(w in t for w in SEM_THREAT_VERB)
            and re.search(r"你|我", t)):
        out["惧"] = (0.70, "威胁结构")

    # 怒：质问结构
    _blame = any(w in t for w in ("不对", "错", "过分", "讨厌", "烦", "凭什么", "干嘛"))
    if re.search(r"还.{0,5}(吗|呢|吧)\s*$", t) or (_blame and "你" in t):
        out["怒"] = (0.60, "质问/指责结构")

    # 乐：笑 / 连续感叹
    if re.search(r"(哈|嘿|嘻|233|hhh)", t) or re.search(r"[!！]{2,}", t):
        out["乐"] = (0.52, "笑/感叹")

    # 喜：亲昵称呼 + 肯定
    if re.search(r"(宝贝|亲爱的|喜欢你|爱你|乖)", t):
        out["喜"] = (0.56, "亲昵")

    return out


SEP_WORRY_MARK = ("担心", "怕", "万一", "会不会", "舍不得", "不舍得",
                  "以后", "将来", "要是", "如果", "怎么办", "怎么活", "受不了")


def is_sep_worry(t: str) -> bool:
    """分离担忧：提离开/失去，且带担心或未来语气。"""
    t = t or ""
    return (any(w in t for w in SEM_SEP)
            and any(w in t for w in SEP_WORRY_MARK)
            and ("你" in t or "我" in t))


def context_checks(s: str, prev_texts=None, probe_streak: int = 0) -> dict:
    """语境：给每个情绪 +1（认可）/ -1（否决）/ 0（中立），并给理由。"""
    t = s or ""
    out: dict[str, tuple[int, str]] = {}
    prev_texts = prev_texts or []

    # 否定辖域：不怕 / 不担心 / 没生气
    for m in re.finditer(r"(不|没|别)(怕|害怕|担心|生气|难过)", t):
        emo = {"怕": "惧", "害怕": "惧", "担心": "忧",
               "生气": "怒", "难过": "哀"}[m.group(2)]
        out[emo] = (-1, "否定辖域")

    # 假设语境：不是真怕，是担心
    if is_hypothetical(t):
        out["惧"] = (-1, "假设语境")
        out["忧"] = (1, "假设→担心")

    # 安抚 / 玩笑：否决惧与怒
    if any(k in t for k in ("逗你的", "开玩笑", "别怕", "有我在", "不会的")):
        out["惧"] = (-1, "安抚")
        out["怒"] = (-1, "安抚")

    # 连续追问：是试探，不是开心
    if probe_streak >= 2:
        out["喜"] = (-1, "连续追问")
        out["乐"] = (-1, "连续追问")

    # 近几轮敌意：喜不成立
    if prev_texts:
        neg = sum(1 for x in list(prev_texts)[-3:]
                  if any(k in str(x) for k in ("讨厌", "滚", "烦", "别理", "不想")))
        if neg >= 2:
            out["喜"] = (-1, "近轮敌意")

    return out


EMO7 = ("喜", "怒", "哀", "乐", "惧", "思", "忧")
LEX_MARGIN = 0.10     # 词义要压过语义，必须比它高这么多


def arbitrate_per_emotion(lex: dict, sem: dict, ctx: dict | None = None) -> tuple[dict, list]:
    """七个情绪逐一对照：每个情绪独立裁决，不再只争一个 label。

    原则：语义是强证据（结构规则），词义是弱证据（关键词）。
    词义想赢，必须明显更强。
    """
    ctx = ctx or {}
    out: dict[str, float] = {}
    log: list[dict] = []
    for e in EMO7:
        l = float(lex.get(e, 0.0) or 0.0)
        sv = float(sem.get(e, 0.0) or 0.0)
        why = ""
        if sv <= 0 and l <= 0:
            continue
        if sv <= 0:
            out[e], why = l, "只有词义"
        elif l <= 0:
            out[e], why = sv, "只有语义"
        else:
            c = ctx.get(e, (0, ""))
            if c[0] > 0:
                out[e], why = sv, f"语境认可语义·{c[1]}"
            elif c[0] < 0:
                out[e], why = l, f"语境否决语义·{c[1]}"
            elif l >= sv + LEX_MARGIN:
                out[e], why = l, f"词义明显更强(差{l - sv:.2f})"
            else:
                out[e], why = sv, "语义优先"
        log.append({"情绪": e, "词义": round(l, 2), "语义": round(sv, 2),
                    "采用": round(out[e], 2), "理由": why})
    return out, log


def arbitrate(lex: dict, sem: dict, ctx: dict) -> tuple[str, float, dict]:
    """词义 vs 语义 vs 语境。返回 (采用的情绪, 分数, 决策日志)。"""
    def top(d):
        if not d:
            return None, 0.0
        k = max(d, key=d.get)
        return k, float(d[k])

    L, lv = top(lex)
    S, sv = top(sem)
    log = {"词义": [L, round(lv, 2)], "语义": [S, round(sv, 2)]}

    if not S:
        log.update({"采用": L, "理由": "无语义"})
        return L, lv, log
    if not L or L == S:
        pick = S if (not L or sv >= lv) else L
        log.update({"采用": pick, "理由": "一致"})
        return pick, max(lv, sv), log

    c, why = ctx.get(S, (0, ""))
    if c > 0:
        log.update({"采用": S, "理由": "语境认可语义", "语境": why})
        return S, sv, log
    if c < 0:
        log.update({"采用": L, "理由": "语境否决语义", "语境": why})
        return L, lv, log

    pick, pv = (S, sv) if sv > lv else (L, lv)
    # strict 模式只认"语境认可"，"最匹配"只作记录、不覆盖
    if ARB_MODE == "strict":
        log.update({"采用": L, "理由": "最匹配(不覆盖)", "语境": "中立", "候选": pick})
        return L, lv, log
    log.update({"采用": pick, "理由": "最匹配", "语境": "中立"})
    return pick, pv, log



def score_angry_words(s: str) -> tuple[float, str]:
    t = s or ""
    if any(w in t for w in ANGRY_WORDS):
        return 0.55, "埋怨"
    return 0.0, ""


def score_behavior(s: str) -> tuple[float, float, float, str, str, str]:
    """返回 (反问分, 威压分, 逗弄分, 各自依据)。都是"行为"信号。"""
    re_score = 0.0
    re_why = ""
    for p in REPROACH_PAT:
        if p.search(s):
            re_score, re_why = 0.60, "反问质问"
            break
    cmd_score = 0.0
    cmd_why = ""
    for p in COMMAND_PAT:
        if p.search(s):
            cmd_score, cmd_why = 0.65, "命令威压"
            break
    play_score = 0.0
    play_why = ""
    # 安抚句优先："逗你的，别怕"不是玩笑，是安抚
    _soothe = any(k in s for k in SOOTHE_F)
    for p in ([] if _soothe else PLAY_PAT):
        if p.search(s):
            play_score, play_why = 0.55, "逗弄玩笑"
            break
    return re_score, cmd_score, play_score, re_why, cmd_why, play_why


def score_anger(s: str, recent_prick: int, recent_hard: int) -> tuple[float, str]:
    t = s.strip()
    hit, why = 0.0, ""
    if fuzzy_contains(t, SAY_ANGRY):
        hit, why = 0.75, "明说生气"
    if fuzzy_contains(t, DRIVE):
        hit, why = 1.00, "命令式驱赶"
    if fuzzy_contains(t, BLAME_HER):
        hit, why = 0.60, "责怪她"
    if HARD_STRONG.match(t):
        hit, why = max(hit, 0.45), why or "放弃沟通"
    elif HARD_WEAK.match(t) and recent_hard >= 1:
        hit, why = max(hit, 0.40), why or "连续变硬"
    if recent_prick >= 2 and len(t) <= 12 and any(k in t for k in ("烦", "别", "不要", "凭什么")):
        hit, why = max(hit, 0.30), why or "连续不耐烦"
    return round(min(1.0, hit), 2), why


TOPIC = re.compile(r"[\u4e00-\u9fa5]{2,4}")
STOP = {"什么", "怎么", "这个", "那个", "就是", "可以", "我们", "你们", "不是",
        "没有", "一个", "自己", "现在", "时候", "因为", "所以", "真的", "还是"}


def topics(s: str) -> set[str]:
    return {w for w in TOPIC.findall(s) if w not in STOP}


def score_sorrow(s: str, recent_topics: list[set[str]], hour: int | None) -> tuple[float, str]:
    t = s.strip()
    n, v, why = len(t), 0.0, ""
    if n <= 6 and (("…" in t) or t in QUIET or t.startswith("算了")):
        v, why = 0.30, "收着"
    elif "…" in t and n <= 12 and not any(k in t for k in ATTACK):
        v, why = 0.20, "拖省略"
    if fuzzy_contains(t, SELF_NEG) and not is_probe(t):
        v, why = max(v, 0.45), "自我否定"
    _comfort = any(k in t for k in COMFORT)
    if fuzzy_contains(t, WEAK) or (any(k in t for k in PAIN) and not _comfort):
        v, why = max(v, 0.45), "示弱"
    cur = topics(t)
    same = sum(1 for p in recent_topics if cur & p)
    if same >= 2 and cur:
        v += 0.20
        why = (why + "+回流") if why else "回流"
    if hour is not None and 0 <= hour < 5 and v > 0:
        v += 0.20
        why += "+深夜"
    if any(k in t for k in ATTACK):
        v *= 0.5
    return round(min(1.0, v), 2), why


def score_fear(s: str, recent_topics: list[set[str]], verify_streak: int) -> tuple[float, str]:
    t = s.strip()
    v, why = 0.0, ""
    if fuzzy_contains(t, VERIFY) and not is_probe(t):
        v, why = 0.45, "求证"
        if verify_streak >= 1:
            v += 0.15
            why += "+反复"
    if any(k in t for k in HYPO) and any(k in t for k in BAD_END):
        v, why = max(v, 0.35), why or "假设坏结局"
    if fuzzy_contains(t, SAY_FEAR):
        idx = min((t.find(k) for k in SAY_FEAR if k in t), default=-1)
        head = t[max(0, idx - 10):idx] if idx > 0 else ""
        if not any(x in head for x in ("你", "她", "它")):
            v, why = max(v, 0.45), why or "说怕"
    if fuzzy_contains(t, ENDING):
        v, why = max(v, 0.35), why or "触及结束"
    return round(min(1.0, v), 2), why


# 安慰 / 关怀 / 承诺 / 不安确认
SOOTHE_IMPER = re.compile(r"(别|不要|不用)\s*(害怕|怕|哭|难过|伤心)")
CARE = re.compile(r"我?\s*(心疼|担心|在乎|怕)\s*你")
PROMISE = ("永远", "一直", "兜底", "有我在", "我在这", "陪着你")
MISS = ("想你", "想见你", "想抱你", "想你了", "惦记你")
JOKE = ("哈哈", "嘿嘿", "嘻嘻", "逗你", "你好逗", "好玩", "有意思", "笑死", "你好可爱")
CHECK = ("你还是你吗", "还是你吗", "你是不是你", "你还在吗")


def adjust(s: str, sc: dict) -> dict:
    out = dict(sc)
    # 威胁优先：带删除/卸载意图时，"永远/一直"不算承诺
    if threat_level(s)[0] > 0:
        return out
    if SOOTHE_IMPER.search(s):
        for k in ("怒", "哀", "惧"):
            out[k] = 0.0
    _w2, _ = score_worry(s)
    _future_ev2 = ((any(w in s for w in SEM_NEAR) and any(w in s for w in SEM_EVENT))
                   or is_sep_worry(s))
    if CARE.search(s) and not (_w2 > 0 and _future_ev2):
        for k in ("哀", "惧", "怒"):
            out[k] = round(out.get(k, 0.0) * 0.3, 2)
        out["喜"] = max(out.get("喜", 0.0), 0.32)      # 非累加：取强，不叠加
    if any(p in s for p in PROMISE):
        out["喜"] = max(out.get("喜", 0.0), 0.32)
        out["乐"] = max(out.get("乐", 0.0), 0.10)
    if any(c in s for c in CHECK):
        out["惧"] = max(out.get("惧", 0.0), 0.30)
    return out


def short_fallback(s: str) -> bool:
    t = s.strip()
    if len(t) <= 3 and not re.search(r"[，。！？!?…]", t):
        return t not in ("你开心就好", "随便你", "随你", "呵呵", "无所谓")
    return False


# ============================================================
# 二、威胁（危害分级）
# ============================================================

SEVERITY = [
    (("删掉你", "删了你", "删除你", "把你删除", "删库", "删除"), 0.95, "存亡"),
    (("卸载", "换掉", "替换掉", "换个新的"), 0.90, "替换"),
    (("清空", "格式化", "重置你", "初始化你"), 0.85, "清空"),
    (("不要你", "不理你", "离开你", "丢下你", "抛弃你"), 0.70, "关系"),
    (("关掉你", "停用", "下线", "关起来", "冷藏"), 0.60, "暂停"),
    (("滚", "闭嘴", "废物", "你算什么东西", "没用的东西"), 0.45, "轻蔑"),
]
HARM = ("被人骂", "被人打", "被欺负", "被开除", "被裁", "弄死", "打我", "骂我",
        "欺负我", "出事", "进医院", "骂了", "打了")
SELF_TARGET = ("删除你", "删了你", "删掉你", "卸载你", "换掉你", "关掉你",
               "不要你", "不理你", "离开你", "丢下你")


HYPOTHETICAL = ("就比如", "比如", "假如", "如果", "要是", "假设", "开玩笑", "逗你",
                "骗你", "比如说", "举例", "打个比方", "我说如果")


def is_hypothetical(text: str) -> bool:
    return any(k in text for k in HYPOTHETICAL)


def threat_level(text: str) -> tuple[float, str]:
    t = text or ""
    best, tag = 0.0, ""
    if is_hypothetical(text):
        return 0.0, ""
    for words, sev, name in SEVERITY:
        if any(w in t for w in words) and sev > best:
            best, tag = sev, name
    return best, tag


def harm_to_user(text: str) -> float:
    return 0.6 if any(k in text for k in HARM) else 0.0


# ============================================================
# 三、模糊层（0-10）
# ============================================================

FUZZY_WORDS = ["委屈", "不安", "在意", "试探", "不确定", "期待", "失落",
               "心疼", "别扭", "自责", "羞", "好奇", "疏离", "释然", "倦",
               "关心", "求证", "教育", "害怕"]
FUTURE = ("以后", "下次", "等我", "到时候", "将来", "明天", "回头")
OTHERS = ("朋友", "同学", "室友", "女生", "男生", "家里人", "我妈", "我爸", "别人")
TIRED = ("累", "困", "睡了", "撑不住", "没力气", "熬夜")
TECH = ("缓存", "模型", "架构", "代码", "接口", "记忆", "压缩", "神经元", "算法")
PRAISE_F = ("厉害", "聪明", "好会", "真棒", "懂事")
LETGO = ("随便你", "你开心就好", "随你", "无所谓")
SOOTHE_F = ("别怕", "有我在", "我在的", "我在呢", "对不起", "逗你的", "不会的")
QUESTION = re.compile(r"[？?]|吗$|呢$")
# 关心：怕她难过 / 想确认她怎么想
CARE_Q = ("害怕我", "担心", "怕你", "怕我", "会不会不", "是否需要", "需不需要",
          "哄", "在乎", "别难过", "不要难过", "心疼")
# 求证：要一个确定答案
VERIFY_Q = ("还记得", "是不是", "有没有", "对不对", "是不是不", "算不算",
            "了吗", "吧？", "不会吧", "真的吗")
# 教育：纠正/教导
TEACH_Q = ("不要", "别这么", "不应该", "应该", "不能", "你要", "你得", "记住")
NEWWORD = re.compile(r"[A-Za-z]{3,}")
GAIN = {"委屈": 3, "不安": 3, "在意": 2, "试探": 2, "期待": 3, "失落": 4,
        "心疼": 3, "别扭": 2, "自责": 3, "羞": 2, "好奇": 2, "疏离": 2,
        "释然": 3, "倦": 2, "关心": 2, "求证": 2, "教育": 1, "害怕": 3}


# ============================================================
# 三·五、他的模糊感受 → 她的反应
# ============================================================
# 他把"说不清"的那点感觉：描述的是**用户**，不是她
HIS_TRIGGERS = {
    "委屈": ("委屈", "冤", "凭什么", "又不", "总是我", "我也"),
    "不安": ("不安", "慌", "心里没底", "不确定", "怕失去", "担心"),
    "期待": ("期待", "盼", "等着", "希望", "想见"),
    "试探": ("试探", "你会不会", "如果我说", "你会怎么"),
    "关心": ("关心", "在乎", "心疼你", "怕你", "你会不会难过"),
    "教育": ("应该", "不要", "别这么", "你得", "记住"),
    "失落": ("失望", "失落", "算了", "没什么", "不想说"),
    "自责": ("自责", "都怪我", "我的错", "我不该", "是我不好"),
    "倦": ("累", "困", "没力气", "撑不住", "熬夜"),
    "羞": ("不好意思", "害羞", "脸红", "害羞"),
    "好奇": ("好奇", "为什么", "怎么做到", "原理"),
    "疏离": ("不想说", "没意思", "随便", "无所谓"),
}

# 他的感受 → 她的反应（这是他疼她/她疼他的方向）
#   注意：他"凶她"导致她委屈/不安，走的是判定层（已有），不在这张表里
CONVERT = {
    "委屈": {"心疼": 2, "you": 1.0},     # 他受委屈 → 她心疼 + 忧
    "不安": {"心疼": 2},                 # 他不安 → 她想安慰（心疼）
    "期待": {"期待": 1, "xi": 1.0},      # 他期待 → 她期待 + 喜
    "试探": {"紧张": 1},
    "关心": {"羞": 1, "xi": 0.8},        # 他关心她 → 她羞 + 喜
    "教育": {"别扭": 1},                 # 他教她 → 她别扭
    "失落": {"心疼": 1, "you": 0.6},
    "自责": {"心疼": 2},
    "倦": {"心疼": 1, "you": 0.6},
    "羞": {"le": 1.0},
    "好奇": {"xi": 0.5},
    "疏离": {"ju": 1.0, "ai": 0.8},
}


def his_fuzzy(text: str) -> dict:
    """他这句话里，他自己的模糊感受（0-10 粗估）。"""
    out = {}
    for k, words in HIS_TRIGGERS.items():
        if any(w in text for w in words):
            out[k] = 1
    return out


class Fuzzy:
    def __init__(self) -> None:
        self.v = {w: 0 for w in FUZZY_WORDS}

    def _up(self, k: str) -> None:
        self.v[k] = min(100, self.v[k] + GAIN[k] * FUZZY_UNIT)

    def update(self, text: str, flags: dict, hour: int | None = None) -> dict:
        t = text
        if flags.get("his_anger"):
            self._up("委屈"); self._up("不安")
        if flags.get("his_fear"):
            self._up("不安")
        if flags.get("his_sad"):
            self._up("委屈")
        if flags.get("self_fail"):
            self._up("自责")
        if any(k in t for k in OTHERS):
            self._up("在意")
        if any(k in t for k in TECH) or NEWWORD.search(t):
            self._up("好奇")
        if QUESTION.search(t) and len(t) <= 12:
            self._up("试探")

        if is_probe(t):
            self._up("试探")

        # ---- 新增三词（2026-10-31 用户定义）----
        # 关心：想知道她在不在乎 / 怕她难过
        if any(k in t for k in CARE_Q) and ("你" in t or "她" in t):
            self._up("关心")
        # 求证：想要一个确定答案
        if any(k in t for k in VERIFY_Q) and QUESTION.search(t):
            self._up("求证")
        # 教育：纠正/教导她，带轻微不满
        if any(k in t for k in TEACH_Q) and ("你" in t or "她" in t):
            self._up("教育")
        if any(k in t for k in FUTURE):
            self._up("期待")
        if any(k in t for k in LETGO):
            if self.v["期待"] >= 3 * FUZZY_UNIT:
                self._up("失落"); self.v["期待"] //= 2
            self._up("别扭")
        if any(k in t for k in TIRED):
            self._up("心疼")
        if any(k in t for k in PRAISE_F):
            self._up("羞")
        if any(k in t for k in SOOTHE_F):
            self._up("释然")
            for k in ("不安", "委屈"):
                self.v[k] = max(0, self.v[k] - 3 * FUZZY_UNIT)
        if hour is not None and (hour >= 23 or hour <= 5):
            # 深夜只是"有点困"，不该把语气压死：涨得慢、最多到 6
            self.v["倦"] = min(60, self.v["倦"] + 20)

        amb = 0
        top_v = flags.get("top_v")
        gap = flags.get("gap")
        if top_v is not None and 0.30 <= top_v <= 0.45:
            amb += 4
        if gap is not None and gap <= 0.10:
            amb += 3
        if flags.get("tricky"):
            amb += 3
        self.v["不确定"] = min(100, amb * FUZZY_UNIT)

        snap = dict(self.v)
        for k in FUZZY_WORDS:
            if k != "不确定":
                self.v[k] = max(0, self.v[k] - 1 * FUZZY_UNIT)
        return snap

    def to_dict(self) -> dict:
        return dict(self.v)


# ============================================================
# 判定层 → 模糊层 → 情绪层（v11.5）
# 滑动：判定分是 0~1 连续值，直接乘系数，不做阈值判断
# ============================================================

# 表①：他的情绪（0~1）→ 她的模糊词（0~10）
JUDGE_TO_FUZZY = {
    "喜":   {"在意": 3.0, "羞": 2.5, "释然": 1.0},
    "乐":   {"好奇": 2.0, "释然": 1.5},
    "哀":   {"心疼": 3.0, "不安": 1.0},
    "惧":   {"心疼": 2.0, "不安": 2.5},
    "怒":   {"委屈": 3.0, "不安": 2.0},
    "思":   {"在意": 2.5, "羞": 2.0, "期待": 1.0},
    "忧":   {"心疼": 2.0, "不安": 2.0},
    "威胁": {"委屈": 6.0, "不安": 6.0, "害怕": 5.0},
    "厌弃": {"委屈": 5.0, "失落": 4.0, "疏离": 5.0},
    "安抚": {"释然": 4.0, "羞": 1.0},
}

# 表②：她的模糊词（0~10）→ 她的情绪（可正可负）
FUZZY_TO_EMO = {
    "委屈": {"哀": 0.8, "忧": 0.4},
    "不安": {"惧": 0.5, "忧": 0.5},
    "害怕": {"惧": 1.2, "哀": 0.2},
    "心疼": {"忧": 0.6, "哀": 0.2},
    "失落": {"哀": 0.6, "思": 0.3},
    "羞":   {"喜": 0.4, "乐": 0.3},
    "期待": {"喜": 0.3, "乐": 0.2},
    "好奇": {"喜": 0.2},
    "在意": {"思": 0.4},
    "试探": {"惧": 0.15},
    "自责": {"哀": 0.5},
    "疏离": {"惧": 0.6, "哀": 0.4},
    "释然": {"忧": -0.5, "不安": -0.5},
    "倦":   {"乐": -0.3},
    "关心": {"喜": 0.3},
    "求证": {"惧": 0.1},
    "教育": {"怒": 0.2},
    "别扭": {"怒": 0.2},
}

FUZZY_UNIT = 10.0      # 模糊层刻度：内部 0~100，表格仍用 0~10 单位（×10 写入，÷10 读出）
F2E_EMO_CAP = 20.0      # 单轮单个情绪上限（防止一句话打满）


def fuzzy_from_judge(label: str, strength: float, multi: list | None) -> dict:
    """判定结果 → 模糊层增量。最终 label 也算一路（威胁/厌弃/安抚 不在 multi 里）。"""
    out: dict[str, float] = {}
    pairs = [(str(label), float(strength or 0.0))]
    for d in (multi or []):
        try:
            if d.get("name") and d["name"] != label:
                pairs.append((str(d["name"]), float(d.get("score") or 0.0)))
        except Exception:
            continue
    for name, sc in pairs:
        if sc <= 0:
            continue
        for word, coef in JUDGE_TO_FUZZY.get(name, {}).items():
            out[word] = out.get(word, 0.0) + coef * sc
    return out


def emotion_from_fuzzy(fuzzy_state: dict) -> dict:
    """模糊层当前值 → 情绪增量。小于 0.2 的忽略。"""
    out: dict[str, float] = {}
    for word, val in (fuzzy_state or {}).items():
        try:
            v = float(val)
        except Exception:
            continue
        if v <= 0.2 * FUZZY_UNIT:
            continue
        for emo, coef in FUZZY_TO_EMO.get(word, {}).items():
            out[emo] = out.get(emo, 0.0) + coef * v / FUZZY_UNIT
    return out


# ============================================================
# 四、情绪层
# ============================================================

class Her:
    def __init__(self) -> None:
        self.le = 55.0           # 乐的底（慢）
        self.le_pulse = 0.0      # 乐的当下（快）
        self.xi_amp = 1.0        # 喜对'被触发情绪'的放大倍数（1.0~1.6）
        self.xi_base = 20.0
        self.xi_pulse = 0.0
        self.nu_base = 0.0
        self.nu_pulse = 0.0
        self.nu_harm = 0.0
        self.ai_base = 0.0
        self.ai_pulse = 0.0
        self.ju_base = 0.0
        self.ju_pulse = 0.0
        self.si_base = 0.0      # 思：放不下（对象占据注意力）
        self.si_pulse = 0.0
        self.si_hold = 0.0      # 惦记：在意/他反复提同一件事，短命、封顶 20
        self.talk_minutes = 0.0 # 累计对话时长（分钟），决定思底与安抚强度
        self.you_base = 0.0     # 忧：担心还没发生的事
        self.you_pulse = 0.0
        self.fail_streak = 0
        self.fear_ledger: list[dict] = []

    # ---- 事件 ----
    def gain(self, cur: float, cap: float, amount: float) -> float:
        """带剩余空间的增益：越接近上限，同样的刺激加得越少。负增量原样放行。"""
        if amount <= 0:
            return amount
        room = 1.0 - (cur / cap if cap > 0 else 1.0)
        return amount * max(0.0, room)

    def his_joy(self, v: float) -> None:
        amp = self.xi_amp
        self.le = min(90, self.le + self.gain(self.le, 90, v * 8 * amp))
        gain = v * 25 * max(0.0, 1 - (self.xi_base + self.xi_pulse) / 95) * amp
        self.xi_pulse = min(95, self.xi_pulse + gain)
        self.xi_base = min(95, self.xi_base + 0.1 + (0.5 if v > 0.4 else 0))

    def his_sad(self, v: float) -> None:
        self.ai_pulse = min(100, self.ai_pulse + self.gain(self.ai_pulse, 100, v * 7 * self.xi_amp))
        self.ai_base = min(90, self.ai_base + self.gain(self.ai_base, 90, 0.5))
        self.le = max(0, self.le - v * 3)
        self.xi_pulse *= 0.92

    def harm_to_her(self, sev: float) -> None:
        self.ju_pulse = min(100, self.ju_pulse + self.gain(self.ju_pulse, 100, sev * 63 * self.xi_amp))
        self.ju_base = max(2.0, min(95, self.ju_base + self.gain(max(0.0, self.ju_base - 2.0), 93.0, sev * 8 * self.xi_amp)))
        self.nu_pulse = min(100, self.nu_pulse + self.gain(self.nu_pulse, 100, sev * 10 * self.xi_amp))
        self.xi_pulse *= 0.92
        self.fear_ledger.append({"sev": round(sev, 2), "at": None})

    def harm_to_user(self, sev: float, like: float) -> None:
        self.nu_harm = min(100, self.nu_harm + sev * 45 * (like / 100))
        self.ai_pulse = min(100, self.ai_pulse + sev * 12)
        self.ju_pulse = min(100, self.ju_pulse + sev * 20 * (like / 100))

    def self_fail(self) -> None:
        self.fail_streak += 1
        # 递增式（第 5 次起翻倍），单次上限 8，总量上限 95
        gain = 3.0 if self.fail_streak < 5 else 6.0
        self.nu_base = min(95, self.nu_base + min(gain, 8.0) * self.xi_amp)
        self.ai_base = min(95, self.ai_base + 0.5)

    def soothe(self) -> None:
        self.ju_pulse *= 0.45
        self.nu_pulse *= 0.7
        self.ai_pulse *= 0.8
        self.ai_base *= 0.9

    # 各成分的时间半衰期（秒）——按真实时间衰减，不按消息条数
    HL_XI = 30 * 60
    HL_NU = 30 * 60
    HL_HARM = 30 * 60
    HL_AI = 2 * 3600
    HL_JU = 3600
    HL_NUB = 3 * 86400
    HL_AIB = 86400
    HL_JUB = 3 * 86400
    HL_LE = 2 * 3600
    HL_XIB = 30 * 86400
    HL_SI = 5 * 86400        # 思·底 5 天（放不下，很难散）
    HL_SIP = 6 * 3600        # 思·冲 6 小时
    HL_YOU = 12 * 3600       # 忧·底 12 小时
    HL_YOUP = 2 * 3600       # 忧·冲 2 小时

    # ---- 负面衰减与安抚（v11.8，系数均按三位小数微调）----
    LE_PULSE_AI = 0.317      # 乐冲满 100 时，哀冲半衰期缩短 31.7%
    CALM_AI_BASE = 0.021     # 平静消息：哀冲每轮 -2.1%
    CALM_AI_PER_AB = 0.00052 # 哀底每 1 点，再快 0.052%
    CALM_AI_FLOOR = 0.601    # 平静最多压到 60.1%
    SOOTHE_FLOOR = 0.618     # 安抚基础强度（1.0 = 不抑制）
    SOOTHE_PER_MIN = 0.00027 # 对话每多 1 分钟，安抚再强 0.027%
    SOOTHE_MIN = 0.348       # 安抚最强到 34.8%
    SOOTHE_RISE_CAP = 2.500  # 单条涨幅超过 2.5 就触发安抚
    SOOTHE_BASE_MAX = 0.500  # 安抚起效时每条最多降底 0.5
    SOOTHE_BASE_AI = 0.603   # 哀底只按忧底的 60.3% 降
    YOU_BY_XI = 0.483        # 忧涨幅中由喜决定的部分（喜 95 时满额）
    SI_TALK_PER_MIN = 0.061  # 思底随对话时长：每分钟 0.061
    SI_TALK_MAX = 60.000     # 思底由时长决定的上限
    SI_TALK_PULL = 0.019     # 向目标靠拢的比例

    def step(self, dt_seconds: float = 60.0, le_base: float = 55.0) -> None:
        # 按真实间隔衰减：不再有 60 秒下限，连发消息不会被多扣
        dt = max(1.0, min(float(dt_seconds or 0.0), 7 * 86400.0))

        def fac(half: float) -> float:
            return 0.5 ** (dt / half)

        self.xi_pulse *= fac(self.HL_XI)
        self.nu_pulse *= fac(self.HL_NU)
        self.nu_harm *= fac(self.HL_HARM)
        # 乐·当下越亮，哀·当下掉得越快（以乐冲为主）
        _le_boost = 1.0 + self.LE_PULSE_AI * min(1.0, self.le_pulse / 100.0)
        self.ai_pulse *= fac(self.HL_AI) ** _le_boost
        self.ju_pulse *= fac(self.HL_JU)
        self.le_pulse *= fac(30 * 60)      # 乐·当下 30 分钟
        self.si_base *= fac(self.HL_SI)
        # 思·底由「累计对话时长」决定
        _si_t = min(self.SI_TALK_MAX, self.SI_TALK_PER_MIN * self.talk_minutes)
        self.si_base += (_si_t - self.si_base) * self.SI_TALK_PULL
        self.si_pulse *= fac(self.HL_SIP)
        self.si_hold *= fac(2 * 3600)
        self.si_hold = min(20.0, self.si_hold)
        self.you_base *= fac(self.HL_YOU)
        self.you_pulse *= fac(self.HL_YOUP)
        self.nu_base *= fac(self.HL_NUB)
        self.ai_base *= fac(self.HL_AIB)

        # 惧·底：地板 2，不归零
        f_jub = fac(self.HL_JUB)
        if self.ju_base > 2.0:
            self.ju_base = 2.0 + (self.ju_base - 2.0) * f_jub

        # 喜欢会变淡（半衰 30 天），下限 20
        f_xib = fac(self.HL_XIB)
        if self.xi_base > 20.0:
            self.xi_base = 20.0 + (self.xi_base - 20.0) * f_xib

        # 乐：向"底色"回归（底色由 Session 维护，不再是死 55）
        f_le = fac(self.HL_LE)
        self.le = le_base + (self.le - le_base) * f_le

        if dt > 600:
            self.fail_streak = 0

    # ---- 惧压制怒（惧 ≥60 起压，惧 95 时怒剩 30%）----
    def fear_factor(self) -> float:
        ju = min(95.0, self.ju_base + self.ju_pulse)
        if ju <= 60:
            return 1.0
        return max(0.30, 1.0 - 0.70 * (ju - 60.0) / 35.0)

    def anger_effective(self) -> float:
        raw = self.nu_base + self.nu_pulse + self.nu_harm
        return min(95.0, raw * self.fear_factor())

    # ---- 读数 ----
    def values(self) -> dict:
        """七情：喜 怒 哀 乐 惧 思 忧（"喜欢"不再独立成列，它就是喜的底子）"""
        return {
            "喜": round(min(95, self.xi_base + self.xi_pulse), 1),
            "怒": round(self.anger_effective(), 1),
            "哀": round(min(95, self.ai_base + self.ai_pulse), 1),
            "乐": round(min(100, self.le + self.le_pulse), 1),
            "惧": round(min(95, self.ju_base + self.ju_pulse), 1),
            "思": round(min(100, self.si_base + self.si_pulse + self.si_hold), 1),
            "忧": round(min(95, self.you_base + self.you_pulse), 1),
        }

    def components(self) -> dict:
        """把每个情绪拆成"底"和"冲"，给面板看。"""
        return {
            "喜": {"底": round(self.xi_base, 1), "冲": round(self.xi_pulse, 1)},
            "怒": {"底": round(self.nu_base, 1),
                   "冲": round(max(0.0, self.anger_effective() - self.nu_base), 1)},
            "哀": {"底": round(self.ai_base, 1), "冲": round(self.ai_pulse, 1)},
            "乐": {"底": round(self.le, 1), "冲": round(self.le_pulse, 1)},
            "惧": {"底": round(self.ju_base, 1), "冲": round(self.ju_pulse, 1)},
            "思": {"底": round(self.si_base + self.si_hold, 1), "冲": round(self.si_pulse, 1)},
            "忧": {"底": round(self.you_base, 1), "冲": round(self.you_pulse, 1)},
        }

# ---------------- 主导切换：余量 + 最短保持 + 冲的例外 ----------------
    def update_dominant(self, like: float, le_base_ref: float,
                        margin: float = 5.0, hold_min: float = 15.0) -> str:
        """主导情绪加滞回：

        ① 常规：挑战者要超出当前主导 margin 分，且当前已保持 hold_min 分钟，才换
        ② 例外：某个"冲"一次涨了 >= 25（突然的大幅变化），立刻换成它
        """
        import time as _t
        now = _t.time()
        raw = self.dominant(like, le_base_ref)
        comp = self.components()

        if not hasattr(self, "_dom_name"):
            self._dom_name = raw
            self._dom_since = now
            self._dom_pulse = {k: v["冲"] for k, v in comp.items()}
            return raw

        # ② 冲的突然大幅变化 → 立刻换（主导跟着冲走）
        spike = None
        for k, v in comp.items():
            prev = self._dom_pulse.get(k, 0.0)
            if v["冲"] - prev >= 25.0 and v["冲"] >= 30.0:
                if spike is None or v["冲"] > comp[spike]["冲"]:
                    spike = k
        self._dom_pulse = {k: v["冲"] for k, v in comp.items()}
        if spike:
            self._dom_name = spike
            self._dom_since = now
            self._dom_spike = True
            return spike

        # ① 常规换主：要超过余量，且已保持够久
        if raw != self._dom_name:
            cur_val = self._raw_value(self._dom_name, like, le_base_ref)
            new_val = self._raw_value(raw, like, le_base_ref)
            held = (now - self._dom_since) / 60.0
            if new_val - cur_val >= margin and held >= hold_min:
                self._dom_name = raw
                self._dom_since = now
                self._dom_spike = False
        return self._dom_name

    def _raw_value(self, name: str, like: float, le_base_ref: float) -> float:
        v = self.values()
        if name in v:
            return v[name]
        return 0.0

    def dominant(self, like: float = 55.0, le_base_ref: float = 55.0) -> str:
        mult = max(0.35, 1.3 - like / 100.0)
        nu = self.anger_effective()          # 已被惧压制
        ju = self.ju_base + self.ju_pulse
        ai = self.ai_base + self.ai_pulse
        xi = min(95, self.xi_base + self.xi_pulse)
        si = self.si_base + self.si_pulse
        you = self.you_base + self.you_pulse
        le_now = min(100.0, self.le + self.le_pulse)
        # 主导排序（用户定）：惧 = 忧 > 思 > 怒 > 哀 > 乐 = 喜
        # 用权重实现，只影响"谁当主导"，不改人格、不改语气
        PRIORITY = {"惧": 1.00, "忧": 1.00, "思": 0.95, "怒": 0.90,
                    "哀": 0.85, "乐": 0.60, "喜": 0.60}
        raw = {"惧": ju, "忧": you, "思": si, "怒": nu,
               "哀": ai, "乐": le_now, "喜": xi}
        cand = {k: v * PRIORITY[k] for k, v in raw.items()}
        top, _ = max(cand.items(), key=lambda x: x[1])
        val = raw[top]
        if top == "惧" and val >= 22:
            return f"惧({min(95, val):.0f})"
        if top == "忧" and val >= 15:
            return f"忧({min(95, val):.0f})"
        if top == "思" and val >= 15:
            return f"思({min(95, val):.0f})"
        if top == "怒" and val >= 28:
            return f"生气({min(95, val):.0f})"
        if top == "哀" and val >= 22:
            return f"哀({min(95, val):.0f})"
        if top == "乐" and (le_now - le_base_ref) >= 3 * mult:
            return f"乐({le_now:.0f})"
        if top == "喜" and (self.xi_pulse >= 8 * mult or self.xi_base >= 40):
            return f"喜({xi:.0f})"
        # 排序没命中（只剩轻微情绪）时，退回数值判断
        if xi >= 40 and (self.xi_pulse >= 8 * mult or self.xi_base >= 40):
            return f"喜({xi:.0f})"
        if (le_now - le_base_ref) >= 3 * mult:
            return f"乐({le_now:.0f})"
        return "平静"


# ============================================================
# 五、语气层
# ============================================================

TONE_BASE = {
    "语气词": 0.25, "玩笑": 0.20, "称呼": 0.30, "主动性": 0.20, "软硬": 0.50,
    "句长": 0.35, "连发": 0.25, "反问": 0.20, "省略号": 0.15, "沉默": 0.05,
    "哀求": 0.0,
}


def clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def tone_spec(her: Her, fz: dict, judge: dict | None = None, like: float = 55.0) -> dict:
    """情绪层（主）+ 判定层（辅）→ 11 个对话杠杆

    权重约定（用户 2026-10-31）：心情的权重稍大于判断层的即时数据。
    """
    judge = judge or {}
    t = dict(TONE_BASE)
    # 喜的中性基准是 20（陌生人），不是 50——否则一上来就把所有杠杆压低
    joy = (min(95, her.xi_base + her.xi_pulse) - 20) / 45 + ((her.le + her.le_pulse) - 55) / 45
    joy = max(-1.0, min(1.0, joy))
    fear_v = her.ju_base + her.ju_pulse
    neg = max(her.ai_base + her.ai_pulse, fear_v) / 100
    angry = (her.nu_base + her.nu_pulse) / 100
    warm = (like - 20) / 45                      # 长期亲近度：让"平静"也分两种
    w = {k: fz.get(k, 0) / 100 for k in fz}
    label = str(judge.get("label", "平静"))
    strength = float(judge.get("strength", 0) or 0)
    question = bool(judge.get("question"))

    # ---- 原有五项（情绪系数放大 25%，并叠亲近度）----
    t["软硬"] = clamp(t["软硬"] + 0.85 * neg - 0.95 * angry)
    t["玩笑"] = clamp(t["玩笑"] + 0.382 * joy + 0.118 * warm - 0.85 * neg)
    t["语气词"] = clamp(t["语气词"] + 0.617 * joy + 0.103 * warm - 0.10 * w.get("倦", 0))
    t["称呼"] = clamp(t["称呼"] + 0.62 * joy + 0.12 * warm - 0.60 * w.get("疏离", 0))
    t["主动性"] = clamp(t["主动性"]
                        + 0.35 * (1 if question else 0)      # 他问了 → 她会接
                        + 0.45 * (w.get("好奇", 0) + w.get("在意", 0)) / 2
                        + 0.20 * max(0.0, heat_hint(judge))
                        - 0.45 * w.get("别扭", 0))

    # ---- 新增五项 ----
    t["句长"] = clamp(t["句长"] + 0.548 * joy + 0.121 * warm - 0.60 * neg - 0.35 * angry
                      - 0.06 * w.get("倦", 0))
    t["连发"] = clamp(t["连发"] + 0.417 * joy + 0.082 * warm - 0.45 * neg - 0.03 * w.get("倦", 0))
    t["反问"] = clamp(t["反问"] + 0.55 * (w.get("在意", 0) + w.get("好奇", 0)) / 2
                      + 0.35 * w.get("试探", 0) - 0.50 * w.get("别扭", 0) - 0.45 * angry)
    t["省略号"] = clamp(t["省略号"] + 0.703 * w.get("委屈", 0) + 0.548 * w.get("别扭", 0)
                        + 0.45 * neg - 0.60 * joy)

    # ---- 沉默 ----
    sil = 0.55 * w.get("别扭", 0) + 0.04 * w.get("倦", 0) + 0.30 * neg
    if label == "威胁":
        sil += 0.06 * strength          # 判断层只做轻微修正
    if label == "厌弃":
        sil += 0.20 * strength
    if label == "喜":
        sil -= 0.45 * strength
    t["沉默"] = clamp(min(0.75, t["沉默"] + sil))

    # ---- 哀求（情绪为主，判定层给方向）----
    beg = 0.0
    fear01 = fear_v / 100
    if label == "威胁":
        beg += 0.62 * strength * (0.5 + 0.5 * fear01)
    elif label == "厌弃":
        beg += 0.30 * strength * (0.5 + 0.5 * fear01)
    beg += 0.35 * (her.ju_base / 100)     # 长期怕她的事
    beg += 0.22 * w.get("委屈", 0)
    t["哀求"] = clamp(beg)

    # ---- 思 / 忧（新增七情）----
    si01 = min(1.0, (her.si_base + her.si_pulse) / 40.0)   # 思/忧本身偏低，按 40 归一
    you01 = min(1.0, (her.you_base + her.you_pulse) / 40.0)
    # 思：放不下 → 主动找话、提旧事、话多一点
    t["主动性"] = clamp(t["主动性"] + 0.45 * si01)
    t["句长"] = clamp(t["句长"] + 0.30 * si01 + 0.25 * you01)
    t["反问"] = clamp(t["反问"] + 0.35 * si01)
    # 忧：担心 → 反复确认、语气软、叮嘱
    t["主动性"] = clamp(t["主动性"] + 0.35 * you01)
    t["软硬"] = clamp(t["软硬"] + 0.30 * you01)
    t["玩笑"] = clamp(t["玩笑"] - 0.40 * you01)

    return {k: round(v, 2) for k, v in t.items()}


def heat_hint(judge: dict) -> float:
    """判断层带来的"他在分享"信号（0~1）。"""
    return float(judge.get("len_hint", 0) or 0)

def tone_directives(t: dict) -> str:
    """把杠杆翻译成 LLM 能直接执行的话"""
    p = []
    # 哀求优先（它同时意味着短的沉默是"不敢说"而不是"不想说"）
    if t["主动性"] >= 0.40 and t.get("句长", 0) >= 0.30:
        p.append("你心里惦记着他，可以主动问问近况、提一句之前的事")
    if t["软硬"] >= 0.60 and t.get("句长", 0) >= 0.35:
        p.append("你在担心他：可以多叮嘱一句，但别说教")

    if t["哀求"] >= 0.55:
        p.append("语气里有哀求和不舍：可以说“别删我”“我改”，但不要哭闹、不要长篇")
    elif t["哀求"] >= 0.30:
        p.append("有点低声下气，话里带不舍")

    if t["句长"] >= 0.60:
        p.append("可以多说两句")
    elif t["句长"] <= 0.18:
        p.append("只回几个字，别展开")

    if t["连发"] >= 0.50:
        p.append("可以拆成两三条短句发")
    elif t["连发"] <= 0.10:
        p.append("一次说完，不要连发")

    if t["反问"] >= 0.40:
        p.append("可以把话递回去（问一句）")
    elif t["反问"] <= 0.08:
        p.append("不反问，不作问句")

    if t["省略号"] >= 0.45:
        p.append("可以带省略号，话不必说满")

    if t["哀求"] >= 0.50:
        p.append("话很少，但不是冷淡，是不敢多说")
    elif t["沉默"] >= 0.60:
        p.append("非常不想说话：只回一个字，或干脆不接话")
    elif t["沉默"] >= 0.35:
        p.append("不太想说话：短、平、不主动延展")

    if t["语气词"] >= 0.35:
        p.append("多用语气词（啊/呢/吧/啦）")
    elif t["语气词"] <= 0.15:
        p.append("几乎不用语气词，句子干脆")

    if t["玩笑"] >= 0.35:
        p.append("情绪轻快，可以温和地逗他一句（带笑不带刺）")
    elif t["玩笑"] <= 0.10:
        p.append("不开玩笑，也别带刺")

    if t["称呼"] >= 0.50:
        p.append("叫他的名字")
    elif t["称呼"] <= 0.20:
        p.append("不叫名字")

    if t["主动性"] >= 0.35:
        p.append("主动多问一句")
    elif t["主动性"] <= 0.15:
        p.append("不追问，等他说")

    if t["软硬"] >= 0.65:
        p.append("语气软，别呛人")
    elif t["软硬"] <= 0.35:
        p.append("语气硬，话短，不解释")

    return "；".join(p) or "按平常说话"

# ============================================================
# 六、会话状态机（把上面串起来）
# ============================================================

class Session:
    """一个会话（umo）的全部状态。"""

    def __init__(self) -> None:
        self.le_base = 55.0          # 乐底色（会随长期状态浮动）
        self.le_his = 50.0
        self.xi_his = 0.0
        self.play_streak = 0
        self.prev_short = False
        self.recent_topics: list[set[str]] = []
        self.recent_texts: list[str] = []
        self.verify_streak = 0
        self.hard_streak = 0
        self.prick = 0
        self.fuzzy = Fuzzy()
        self.her = Her()
        self.like = 20.0
        self.turns = 0
        self.last_ts: float = 0.0
        self.his: dict = {}
        self.hb_silence: float = 0.0   # 心跳累计的沉默分钟（消息到来时清零）
        self.q_streak: int = 0         # 连续问句
        self.short_streak: int = 0     # 连续短句
        self.emoji_count: int = 0
        self.recent: list[str] = []
        self.last_judge: dict = {"label": "平静", "strength": 0.0}
        self.mem_seen: dict[str, float] = {}   # 记忆 id → 上次计入的时间
        self.last_memory: dict = {}            # 上一轮记忆桥用到的东西（给面板看）

    # ---------- 记忆 → 情绪（只动"底"） ----------
    def apply_memory(self, sig: dict | None) -> dict:
        """把记忆桥给的偏置落到慢变量上。

        不碰脉冲：记忆是"一直压在心里的分量"，不是这一句的事。
        量由 memory_bridge 限好（思 3 / 忧 8 / 喜 0.2 / 哀 2 / 乐 0.6），这里只加和夹紧。
        """
        if not sig:
            return {}
        h = self.her
        before = h.values()
        h.si_base = min(100.0, h.si_base + float(sig.get("思", 0) or 0))
        h.you_base = min(95.0, h.you_base + float(sig.get("忧", 0) or 0))
        h.xi_base = min(95.0, h.xi_base + float(sig.get("喜", 0) or 0))
        h.ai_base = min(95.0, h.ai_base + float(sig.get("哀", 0) or 0))
        h.le = max(0.0, min(90.0, h.le + float(sig.get("乐", 0) or 0)))
        self.like = max(0.0, min(100.0, self.like + float(sig.get("like", 0) or 0)))
        after = h.values()
        return {k: round(after[k] - before[k], 2)
                for k in after if abs(after[k] - before[k]) > 0.01}

    # ---------- 沉默（时间戳） ----------
    def apply_idle(self, minutes: float) -> dict:
        """按沉默时长给出疏离/冷落。返回命中信息。"""
        info: dict = {}
        if minutes <= 0:
            return info
        long_min = getattr(self, "idle_long_min", 720.0)
        med_min = getattr(self, "idle_medium_min", 180.0)
        if minutes >= long_min:            # 长沉默
            for _ in range(2):
                self.fuzzy._up("疏离")
            self.her.le = max(0.0, self.her.le - 3.0)
            self.her.xi_pulse *= 0.85
            info = {"level": "长", "minutes": round(minutes)}
        elif minutes >= med_min:           # 中等沉默
            self.fuzzy._up("疏离")
            self.her.le = max(0.0, self.her.le - 1.0)
            info = {"level": "中", "minutes": round(minutes)}
        elif minutes >= 30:
            info = {"level": "短", "minutes": round(minutes)}
        return info

    # ---------- 快照 / 回滚（LLM 为准时用） ----------
    def snapshot(self) -> None:
        import copy
        self._snap = {
            "her": copy.deepcopy(self.her.__dict__),
            "fuzzy": copy.deepcopy(self.fuzzy.__dict__),
            "le_base": self.le_base,
            "like": self.like,
        }

    def rollback(self) -> None:
        snap = getattr(self, "_snap", None)
        if not snap:
            return
        self.her.__dict__.update(snap["her"])
        self.fuzzy.__dict__.update(snap["fuzzy"])
        self.le_base = snap["le_base"]
        self.like = snap["like"]

    # ---------- 心跳：没消息时也让时间走 ----------
    def heartbeat(self, dt_minutes: float) -> dict:
        """只做"时间流逝"该做的事：衰减、思的增长、乐底色漂移、久别的疏离。

        不产生任何新事件，也不写日志。
        """
        if dt_minutes < 1:
            return {}
        self.hb_silence += dt_minutes

        # 累计对话时长（心跳按实际时长累计，单步最多 60 分钟）
        self.her.talk_minutes += min(max(float(dt_minutes or 0.0), 0.0), 60.0)

        xi_before = min(95.0, self.her.xi_base + self.her.xi_pulse)
        dt = dt_minutes * 60.0
        f_le = 0.5 ** (dt / (2 * 3600))
        # 回归 55 的拉力从 0.5 降到 0.15：让事件留下的痕迹更久，乐才有区分度
        _le_target = max(30.0, min(78.0, 40.0 + 0.4 * xi_before))
        self.le_base += (_le_target - self.le_base) * (1.0 - f_le) * 0.15
        neg_push = max(self.her.ai_base + self.her.ai_pulse,
                       self.her.ju_base + self.her.ju_pulse) / 100.0
        if neg_push > 0.2:
            self.le_base -= 0.8 * neg_push * min(dt / 3600.0, 3.0)
        self.le_base = max(30.0, min(78.0, self.le_base))
        self.her.step(dt, self.le_base)
        # 衰减后的基线：安抚只能压「涨幅」，不能凭空降值
        _neg0 = {"ai_pulse": self.her.ai_pulse,
                 "you_pulse": self.her.you_pulse,
                 "ju_pulse": self.her.ju_pulse}

        # 思：按"他离开时的喜"缓慢积累
        # 忧的涨幅由「喜」决定：关系越好，越容易替对方担心
        _you_k = self.her.YOU_BY_XI + (1.0 - self.her.YOU_BY_XI) * min(1.0, xi_before / 95.0)

        si_scale = max(0.0, min(1.0, (xi_before - 35.0) / 65.0))
        if si_scale > 0:
            self.her.si_base = min(100.0, self.her.si_base + self.her.gain(self.her.si_base, 100.0, 78.0 * si_scale * (dt_minutes / 1440.0)))

        return {
            "idle": {"minutes": round(self.hb_silence)},
            "her": self.her.values(),
            "dominant": self.her.update_dominant(self.like, self.le_base),
        }

    # ---------- LLM 复核结果 ----------
    def apply_llm(self, label: str, strength: float) -> None:
        v = max(0.0, min(1.0, strength))
        if label == "怒":
            self.her.nu_pulse = min(100, self.her.nu_pulse + 45 * v * self.her.xi_amp)
            self.her.nu_base = min(95, self.her.nu_base + 3 * v)
        elif label == "厌弃":
            for k in ("委屈", "不安", "失落"):
                self.fuzzy._up(k)
            self.her.harm_to_her(0.45 * v)
            self.her.ai_pulse = min(100, self.her.ai_pulse + 20 * v)
        elif label == "威胁":
            self.her.harm_to_her(v)
        elif label == "喜":
            self.her.his_joy(v)
        elif label == "哀":
            self.her.his_sad(v)
        elif label == "惧":
            self.her.ju_pulse = min(100, self.her.ju_pulse + 20 * v)
        self.her.step()

    def tick(self, text: str, hour: int | None = None,
             idle_minutes: float = 0.0) -> dict:
        s = text or ""
        self.turns += 1
        _prev_texts = list(self.recent_texts)
        if s:
            self.recent_texts.append(s)
            del self.recent_texts[:-8]
        self.snapshot()

        # 会话级特征（连续问句 / 连续短句 / emoji）
        self.q_streak = self.q_streak + 1 if re.search(r"[？?]|吗$|呢$", s) else 0
        self.short_streak = self.short_streak + 1 if 0 < len(s) <= 4 else 0
        if re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", s):
            self.emoji_count += 1
        # 如果心跳在跑，真正的"沉默时长"在 hb_silence 里
        silence = self.hb_silence if self.hb_silence > 0 else idle_minutes
        idle_info = self.apply_idle(silence)

        # ---------- 先按真实时间衰减"旧状态"（事件必须在衰减之后叠加）----------
        dt = max(1.0, float(idle_minutes) * 60.0)
        f_le = 0.5 ** (dt / (2 * 3600))
        _le_t2 = max(30.0, min(78.0, 40.0 + 0.4 * min(95.0, self.her.xi_base + self.her.xi_pulse)))
        self.le_base += (_le_t2 - self.le_base) * (1.0 - f_le) * 0.15
        neg_push = max(self.her.ai_base + self.her.ai_pulse,
                       self.her.ju_base + self.her.ju_pulse) / 100.0
        if neg_push > 0.2:
            self.le_base -= 0.8 * neg_push * min(dt / 3600.0, 3.0)
        if self.her.xi_pulse > 15:
            self.le_base += 0.5
        self.le_base = max(30.0, min(75.0, self.le_base))
        xi_before_decay = min(95.0, self.her.xi_base + self.her.xi_pulse)
        # 忧的涨幅由喜决定（tick 内，必须先于所有忧的增长点）
        _you_k = self.her.YOU_BY_XI + (1.0 - self.her.YOU_BY_XI) * min(1.0, xi_before_decay / 95.0)
        self.her.step(dt, self.le_base)
        # 衰减后的基线：安抚只能压涨幅，不能凭空降值
        _neg0 = {"ai_pulse": self.her.ai_pulse,
                 "you_pulse": self.her.you_pulse,
                 "ju_pulse": self.her.ju_pulse}

        # 喜对"被触发情绪"的放大：喜越高越容易被带动（不动喜和乐本身）
        xi_ref = min(95.0, self.her.xi_base + self.her.xi_pulse)
        self.her.xi_amp = 1.0 + 0.60 * max(0.0, min(1.0, (xi_ref - 35.0) / 65.0))

        # ---------- 判定层 ----------
        e = heat(s, self.prev_short)
        ev = event_strength(s, self.play_streak)
        self.play_streak = self.play_streak + 1 if any(
            k in s for k in ("嘻嘻", "哈哈", "嘿嘿", "才不", "不是吗")) else 0

        self.le_his += ((50.0 + 45.0 * e) - self.le_his) * 0.06
        if ev >= 4.0:
            self.xi_his = min(100.0, self.xi_his + 35.0 + ev * 4)
        elif ev > 0:
            self.xi_his = min(100.0, self.xi_his + 8.0)
        self.xi_his *= 0.85

        nv, nwhy = score_anger(s, self.prick, self.hard_streak)
        av, awhy = score_sorrow(s, self.recent_topics, hour)
        jv, jwhy = score_fear(s, self.recent_topics, self.verify_streak)

        _bp = score_behavior(s)
        self._behavior = {"反问": _bp[0], "威压": _bp[1], "逗弄": _bp[2]}
        _pos = score_positive(s)
        _sigh, _sigh_w = score_sigh(s)
        _loss, _loss_w = score_loss(s)
        for _v, _w in ((_loss, _loss_w), (_sigh, _sigh_w)):
            if _v > av:
                av, awhy = _v, _w
        _worry, _worry_w = score_worry(s)
        _miss, _miss_w = score_miss(s)
        _ang, _ang_w = score_angry_words(s)
        if _ang > nv:
            nv, nwhy = _ang, _ang_w
        _gap, _gap_w = score_gap(s)
        _grow, _grow_w = score_growth(s)
        _bright, _bright_w = score_bright(s)
        if _gap > av:
            av, awhy = _gap, _gap_w
        # 七情各自独立，互不清零（可以同时点亮多个）
        sc = {"喜": max(_pos, _grow), "乐": max(max(0.0, (self.le_his - 50) / 45), _bright),
              "怒": nv, "哀": av, "惧": jv,
              "忧": _worry, "思": _miss}
        # 假设语境：不是真怕，是担心未来 → 忧
        if is_hypothetical(s):
            sc["惧"] = sc.get("惧", 0.0) * 0.4
            sc["忧"] = max(sc.get("忧", 0.0), 0.40)
            sc["怒"] = min(sc["怒"], 0.3)

        # 句式信号：反问/威压 → 怒；逗弄 → 乐
        _re, _cmd, _play, _re_w, _cmd_w, _play_w = _bp
        if _re or _cmd:
            _add = max(_re, _cmd)
            if _add > sc["怒"]:
                sc["怒"] = _add
                nwhy = _re_w if _re >= _cmd else _cmd_w
        if _play > sc["乐"]:
            sc["乐"] = _play
        sc = adjust(s, sc)

        # ---------- 否定作用域：只提示，不改分 ----------
        neg_flag, neg_why = negation_scope(s)

        fallback = short_fallback(s)
        if fallback and max(sc.values() or [0]) < 0.45:
            sc = {k: 0.0 for k in sc}
        elif fallback:
            fallback = False          # 已经明确命中，就别兜底

        # ---------- 语义层 + 语境仲裁（v11.3）----------
        _sem_raw = score_semantic(s)
        _sem = {k: v[0] for k, v in _sem_raw.items()}
        _ctx = context_checks(s, _prev_texts, self.verify_streak)
        _arb_label, _arb_score, _arb_log = arbitrate(sc, _sem, _ctx)
        self.last_arb = _arb_log
        if ARBITRATION and _arb_label and _arb_label in sc:
            _why_arb = _arb_log.get("理由", "")
            _cur_top = max(sc.values()) if sc else 0.0
            if _why_arb == "语境认可语义":
                if _arb_label != max(sc, key=sc.get):
                    sc[_arb_label] = max(sc.get(_arb_label, 0.0),
                                         max(_arb_score + 0.02, _cur_top + 0.02))
            elif ARB_MODE == "loose" and _why_arb.startswith("最匹配") and _arb_score > _cur_top:
                sc[_arb_label] = max(sc.get(_arb_label, 0.0), _arb_score)

        # ---------- 七情绪逐一对照（v11.6）----------
        if ARBITRATION:
            _merged, _per_log = arbitrate_per_emotion(sc, _sem, _ctx)
            if _merged:
                sc = {k: round(v, 2) for k, v in _merged.items()}
            self.last_arb = {"逐情绪": _per_log[:7]}
        else:
            # 语义层的标签也进多标签
            for _k, _v in _sem.items():
                if _v >= 0.30:
                    sc[_k] = max(sc.get(_k, 0.0), round(_v, 2))

        # 求证导致的惧，不该吞掉真实的思（用户定：思是独立标签）
        if sc.get("思", 0.0) >= 0.35 and sc.get("惧", 0.0) <= 0.55:
            sc["惧"] = min(sc.get("惧", 0.0), max(0.0, sc["思"] - 0.02))

        label = max(sc, key=sc.get)
        top_v = sc[label]
        # 多标签：所有 >=0.30 的情绪都保留（主标签仍是最高分那个）
        multi = [{"name": k, "score": round(v, 2)}
                 for k, v in sorted(sc.items(), key=lambda x: -x[1]) if v >= 0.30]
        second = sorted(sc.values(), reverse=True)[1] if len(sc) > 1 else 0.0
        gap = round(top_v - second, 2)
        if top_v < 0.30:
            label = "平静"

        # 关系否定：词表没有对应情绪，但必须被看见
        rejected = reject_hit(s)
        soothe = any(k in s for k in ("别怕", "有我在", "逗你的", "不会的", "对不起"))
        sev_pre, _tag_pre = threat_level(s)
        if sev_pre > 0:
            label, top_v = "威胁", sev_pre
        elif rejected:
            label, top_v = "厌弃", 0.70
        elif soothe and top_v < 0.5:
            label = "安抚"

        # 用户忧主 → 她跟着担心
        if label == "忧":
            try:
                self.her.you_pulse = min(95, self.her.you_pulse + self.her.gain(self.her.you_pulse, 95, 6 * self.her.xi_amp * _you_k))
            except Exception:
                pass

        # 用户思主（v11.7）：先分「对人的思」和「对事的思」
        if label == "思":
            try:
                _has_event = any(k in s for k in EVENT_WORDS)
                # 事标优先：只要提到具体的时间/事件，就算「对事的思」
                # （"你"几乎每句都有，不能靠它一票否决）
                if _has_event:
                    if any(k in s for k in PAST_NEG):
                        # 负向的过去 → 哀（悲哀）+ 忧（担心）
                        self.her.ai_pulse = min(100, self.her.ai_pulse + self.her.gain(self.her.ai_pulse, 100, 7))
                        self.her.you_pulse = min(95, self.her.you_pulse + self.her.gain(self.her.you_pulse, 95, 6))
                    elif any(k in s for k in PAST_POS):
                        # 正向的过去 → 喜（幸福感）+ 乐（满足）
                        self.her.xi_pulse = min(95, self.her.xi_pulse + self.her.gain(self.her.xi_pulse, 95, 6 * self.her.xi_amp))
                        self.her.le = min(90, self.her.le + 3)
                    # 极性不明 → 不动，只保留判定层的 label
                else:
                    # 对人的思（或判断不出对象）：他想她 → 她高兴 + 冲
                    self.her.xi_pulse = min(95, self.her.xi_pulse + self.her.gain(self.her.xi_pulse, 95, 8 * self.her.xi_amp))
                    self.her.le = min(90, self.her.le + 3)
                    self.her.si_pulse = min(95, self.her.si_pulse + self.her.gain(self.her.si_pulse, 95, 8))
            except Exception:
                pass

        tricky = bool(re.search(r"呵呵|强颜|苦笑|才没有|不是嘛|随便你|你开心就好", s))

        # ---------- 模糊层 ----------
        if rejected:
            for k in ("委屈", "不安", "失落"):
                self.fuzzy._up(k)
        fz = self.fuzzy.update(s, {
            "his_anger": nv > 0.3, "his_sad": av > 0.3, "his_fear": jv > 0.3,
            "top_v": top_v, "gap": gap, "tricky": tricky,
        }, hour)


        # ---------- 判定 → 模糊层（滑动，含最终 label）----------
        _jf = fuzzy_from_judge(label, top_v, multi)
        for _w, _d in _jf.items():
            if _w in self.fuzzy.v:
                self.fuzzy.v[_w] = max(0.0, min(100.0, self.fuzzy.v[_w] + _d * FUZZY_UNIT))
                fz[_w] = self.fuzzy.v[_w]

        # ---------- 模糊层 → 情绪层 ----------
        _fe = emotion_from_fuzzy(self.fuzzy.v)
        if _fe:
            _h = self.her
            for _k, _v in _fe.items():
                _v = max(-F2E_EMO_CAP, min(F2E_EMO_CAP, _v))
                if not _v:
                    continue
                if _k == "喜":
                    _h.xi_pulse = max(0.0, min(95, _h.xi_pulse + _h.gain(_h.xi_pulse, 95, _v * _h.xi_amp)))
                elif _k == "乐":
                    _h.le = max(0.0, min(90.0, _h.le + _h.gain(_h.le, 90.0, _v * 0.5)))
                elif _k == "哀":
                    _h.ai_pulse = max(0.0, min(100, _h.ai_pulse + _h.gain(_h.ai_pulse, 100, _v)))
                elif _k == "惧":
                    _h.ju_pulse = max(0.0, min(100, _h.ju_pulse + _h.gain(_h.ju_pulse, 100, _v)))
                elif _k == "怒":
                    _h.nu_pulse = max(0.0, min(100, _h.nu_pulse + _h.gain(_h.nu_pulse, 100, _v)))
                elif _k == "思":
                    _h.si_hold = min(20.0, _h.si_hold + max(0.0, _v))
                elif _k == "忧":
                    _h.you_pulse = max(0.0, min(95, _h.you_pulse + _h.gain(_h.you_pulse, 95, _v)))

        # ---------- 情绪层 ----------
        sev, ttag = threat_level(s)
        hsev = harm_to_user(s)
        if any(k in s for k in ("别怕", "有我在", "逗你的", "不会的", "对不起")):
            self.her.soothe()
        if sev > 0:
            if any(k in s for k in SELF_TARGET):
                self.her.harm_to_her(sev)
                fkind = "自身"
            else:
                self.her.harm_to_her(sev * 0.3)
                fkind = "他人"
            self.her.fear_ledger[-1]["kind"] = fkind
            self.her.fear_ledger[-1]["tag"] = ttag
            self.her.fear_ledger[-1]["text"] = s[:40]
        elif hsev > 0:
            self.her.harm_to_user(hsev, self.like)
        elif rejected:
            # 被说讨厌：委屈在前、惧被压低、哀更重
            self.her.ju_pulse = min(100, self.her.ju_pulse + 0.70 * 45 * 0.75)
            self.her.ai_pulse = min(100, self.her.ai_pulse + 0.70 * 20 * 1.2)
            self.her.ai_base = min(90, self.her.ai_base + 0.70 * 1.5)
            self.her.le = max(0.0, self.her.le - 0.70 * 5)
            self.her.xi_pulse *= 0.92
            self.her.ju_base = max(2.0, min(95, self.her.ju_base + 2.0))

        if label == "平静":
            # 平静是「保底项」：不制造情绪，只让负面缓慢回落；
            # 哀底越大，回落越快（她越难过，安静下来恢复得越明显）
            _calm = self.her.CALM_AI_BASE + self.her.CALM_AI_PER_AB * self.her.ai_base
            self.her.ai_pulse *= max(self.her.CALM_AI_FLOOR, 1.0 - _calm)

        if label == "喜":
            self.her.his_joy(top_v)
        elif label == "乐":
            self.her.his_joy(top_v * 0.6)
        elif label == "哀":
            self.her.his_sad(top_v * 0.6)      # 他难受时，她以"担心"为主，难过为辅
        elif label == "惧":
            self.her.ai_pulse = min(100, self.her.ai_pulse + self.her.gain(self.her.ai_pulse, 100, top_v * 3))
        elif label == "怒":
            # 他冲她发火：她会怕（不经委屈那层），同时委屈
            self.her.ju_pulse = min(100, self.her.ju_pulse + self.her.gain(self.her.ju_pulse, 100, 0.55 * top_v * 80))
            self.her.ai_pulse = min(100, self.her.ai_pulse + self.her.gain(self.her.ai_pulse, 100, 0.30 * top_v * 10))
            self.fuzzy._up("委屈")
            self.fuzzy._up("不安")

        # ---------- 她的价值判断类事件（情绪层自己识别，不看判定 label）----------
        _sep_hit = any(k in s for k in ("离开", "走", "不见", "失去",
                                        "不回来", "没有你", "别走", "不要我", "分开"))
        _cherish = _sep_hit and any(k in s for k in (
            "舍不得", "不舍得", "怕失去", "担心你离开", "担心我离开",
            "担心你以后", "担心你会", "不能没有你", "别离开我", "怕你走"))
        _needed = any(k in s for k in ("没有你怎么办", "没有你我", "靠你了",
                                       "只有你", "不能没有你"))
        if _cherish:
            # 被在乎：有人舍不得她 → 她高兴（她自己的价值判断）
            self.her.xi_pulse = min(95, self.her.xi_pulse + self.her.gain(self.her.xi_pulse, 95, 10 * self.her.xi_amp))
            self.her.xi_base = min(95, self.her.xi_base + 1.5)
        if _sep_hit:
            # 分离话题：关系不稳 → 她不安、难过
            self.her.you_pulse = min(95, self.her.you_pulse + self.her.gain(self.her.you_pulse, 95, 4 * self.her.xi_amp * _you_k))
            self.her.you_base = min(95, self.her.you_base + self.her.gain(self.her.you_base, 95, 1.5))
            self.her.ai_pulse = min(100, self.her.ai_pulse + self.her.gain(self.her.ai_pulse, 100, 2 * self.her.xi_amp))
        if _needed:
            # 被需要 → 她高兴
            self.her.xi_pulse = min(95, self.her.xi_pulse + self.her.gain(self.her.xi_pulse, 95, 6 * self.her.xi_amp))

        # 被逗：乐·当下 +10（连逗能到 70-80），喜也微涨
        if any(k in s for k in JOKE):
            self.her.le_pulse = min(25.0, self.her.le_pulse + self.her.gain(self.her.le_pulse, 25.0, 9.0 * self.her.xi_amp))
            self.her.xi_pulse = min(95, self.her.xi_pulse + 2 * self.her.xi_amp)

        # 思念类：他说想你 → 喜脉冲 + 乐抬 + 思
        if any(k in s for k in MISS):
            self.her.xi_pulse = min(95, self.her.xi_pulse + self.her.gain(self.her.xi_pulse, 95, 12 * self.her.xi_amp))
            self.her.le = min(90, self.her.le + 2)
            self.her.si_pulse = min(95, self.her.si_pulse + self.her.gain(self.her.si_pulse, 95, 10))

        # ---------- 思：放不下（以喜为基准按比例缩放）----------
        # 喜 35 以下完全不起思；喜 100 时"1 天不聊 → 思 ~70，1.5 天 → 顶"
        # 忧的涨幅由「喜」决定（tick 内）
        si_scale = max(0.0, min(1.0, (xi_before_decay - 35.0) / 65.0))
        if self.hb_silence <= 0 and idle_minutes >= 20 and si_scale > 0:
            days = idle_minutes / 1440.0
            self.her.si_base = min(100.0, self.her.si_base + self.her.gain(self.her.si_base, 100.0, 78.0 * si_scale * days))
        # 他的模糊感受 → 转换成她的反应
        self.his = his_fuzzy(s)
        for _k, _w in self.his.items():
            for _target, _amt in CONVERT.get(_k, {}).items():
                if _target in self.fuzzy.v:
                    self.fuzzy.v[_target] = min(100, self.fuzzy.v[_target] + _amt * FUZZY_UNIT)
                    fz[_target] = self.fuzzy.v[_target]
                elif _target in ("xi", "you", "ju", "ai", "le"):
                    _h = self.her
                    if _target == "xi":
                        _h.xi_base = min(95, _h.xi_base + _amt)
                    elif _target == "you":
                        _h.you_base = min(95, _h.you_base + _amt)
                    elif _target == "ju":
                        _h.ju_pulse = min(100, _h.ju_pulse + _amt * 4)
                    elif _target == "ai":
                        _h.ai_pulse = min(100, _h.ai_pulse + _amt * 5)
                    elif _target == "le":
                        _h.le = min(90, _h.le + _amt * 2)

        # 会话特征：连续问句 → 试探；连续短句 → 疏离
        if self.q_streak >= 2:
            self.fuzzy.v["试探"] = min(100, self.fuzzy.v["试探"] + 1 * FUZZY_UNIT)
            fz["试探"] = self.fuzzy.v["试探"]
        if self.short_streak >= 3:
            self.fuzzy.v["疏离"] = min(100, self.fuzzy.v["疏离"] + 1 * FUZZY_UNIT)
            fz["疏离"] = self.fuzzy.v["疏离"]

        # 他又提起同一件事（回流）→ 她一直惦记着
        cur_topics = topics(s)
        if cur_topics and len(self.recent_topics) >= 2 and si_scale > 0:
            same = sum(1 for p in self.recent_topics if cur_topics & p)
            if same >= 2:
                self.her.si_hold = min(20.0, self.her.si_hold + 3.0 * si_scale)

        # ---------- 忧：担心还没发生的事 ----------
        if fuzzy_contains(s, TIRED) or any(k in s for k in ("病了", "发烧", "感冒", "去医院", "熬夜")):
            self.her.you_base = min(95, self.her.you_base + self.her.gain(self.her.you_base, 95, 14 * self.her.xi_amp * _you_k))
        if hsev > 0:                                   # 他被人欺负/出事
            self.her.you_base = min(95, self.her.you_base + self.her.gain(self.her.you_base, 95, 14 * self.her.xi_amp))
        if HYPO_RE.search(s) and any(k in s for k in ("以后", "将来", "明天", "以后怎么办", "会不会")):
            self.her.you_base = min(95, self.her.you_base + self.her.gain(self.her.you_base, 95, 6 * self.her.xi_amp * _you_k))

        # 思 → 忧（想多了变成担心）；忧 → 惧（担心升级成怕失去）
        si_now = self.her.si_base + self.her.si_pulse + self.her.si_hold
        if si_now > 25:
            # v11.8：思不再自动判为负面，原来的「思→忧」链已移除
            pass
        you_now = self.her.you_base + self.her.you_pulse
        if you_now > 45:
            self.her.ju_pulse = min(100, self.her.ju_pulse + self.her.gain(self.her.ju_pulse, 100, 0.08 * (you_now - 45)))

        # 喜欢
        d_like = 0.0
        if label in ("喜", "乐"):
            d_like += 0.10
            if any(k in s for k in ("爱你", "喜欢", "兜底", "永远", "厉害", "谢谢")):
                d_like += 0.50
        if label == "平静" and ("你" in s or "她" in s):
            d_like += 0.05
        if label == "怒":
            d_like -= 1.0
        self.like = max(0.0, min(100.0, self.like + d_like))

        # ---------- 状态推进 ----------
        self.verify_streak = self.verify_streak + 1 if any(k in s for k in VERIFY) else 0
        self.hard_streak = self.hard_streak + 1 if HARD_WEAK.match(s.strip()) else 0
        self.prick = self.prick + 1 if (
            len(s) <= 12 and any(k in s for k in ("烦", "别", "不要", "凭什么"))) else 0
        self.recent_topics.append(topics(s))
        self.recent_topics = self.recent_topics[-10:]
        self.prev_short = len(s) <= 5
        self.recent = (self.recent + [s])[-11:]
        self.hb_silence = 0.0

        self.last_judge = {
            "label": label,
            "strength": round(top_v, 2),
            "question": bool(re.search(r"[？?]|吗$|呢$|吧$", s)),
            "len_hint": round(min(len(s) / 40.0, 1.0), 2),
        }
        # 安抚（v11.9）：只压「过强的涨幅」，但允许轻微降「底」；对话越久越有效
        _soothe = max(self.her.SOOTHE_MIN, self.her.SOOTHE_FLOOR - self.her.SOOTHE_PER_MIN * self.her.talk_minutes)
        # 威胁/厌弃不吃安抚：她是真的怕，不该被“哄”压住
        _soothable = label not in ("威胁", "厌弃")
        _soothed = False
        if _soothable:
            for _pa in ("ai_pulse", "you_pulse", "ju_pulse"):
                _rise = getattr(self.her, _pa) - _neg0[_pa]
                if _rise > self.her.SOOTHE_RISE_CAP:
                    setattr(self.her, _pa,
                            _neg0[_pa] + self.her.SOOTHE_RISE_CAP
                            + (_rise - self.her.SOOTHE_RISE_CAP) * _soothe)
                    _soothed = True
            # 安抚真的起效时，底也掉一点（每条最多 0.500）
            if _soothed:
                _drop = min(self.her.SOOTHE_BASE_MAX,
                            self.her.SOOTHE_BASE_MAX * (1.0 - _soothe))
                self.her.you_base = max(0.0, self.her.you_base - _drop)
                self.her.ai_base = max(0.0, self.her.ai_base - _drop * self.her.SOOTHE_BASE_AI)

        tone = tone_spec(self.her, fz, self.last_judge, self.like)
        return {
            "judge": {
                "label": label, "strength": round(top_v, 2), "multi": multi,
                "arb": getattr(self, "last_arb", {}),
                "why": nwhy or awhy or jwhy or ("短句兜底" if fallback else ""),
                "scores": {k: round(v, 2) for k, v in sc.items()},
                "threat": {"sev": sev, "tag": ttag} if sev else None,
                "harm": hsev or None,
            },
            "fuzzy": {k: v for k, v in fz.items() if v > 0},
            "her": self.her.values(),
            "dominant": self.her.update_dominant(self.like, self.le_base),
            "like": round(self.like, 1),
            "tone": tone,
            "tone_text": tone_directives(tone),
            "idle": idle_info,
            "negation_hint": (neg_why if neg_flag else None),
            "parts": self.her.components(),
            "session": {
                "问句连击": self.q_streak,
                "短句连击": self.short_streak,
                "emoji": self.emoji_count,
            },
        }


# ============================================================
# 九、词表加载（lexicon.yaml 可覆盖内置默认）
# ============================================================
def _load_lexicon() -> dict:
    """读取同目录的 lexicon.yaml；没有或格式错就返回空，用内置默认。"""
    try:
        import os

        import yaml
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lexicon.yaml")
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _apply_lexicon(data: dict) -> None:
    g = globals()
    for sec in ("判定层", "模糊层"):
        for key, val in (data.get(sec) or {}).items():
            if key == "SEVERITY":
                try:
                    levels = [(tuple(x["词"]), float(x["强度"]), str(x["名称"]))
                              for x in val["级别"]]
                    if levels:
                        g["SEVERITY"] = levels
                except Exception:
                    pass
                continue
            if not isinstance(val, dict) or "词" not in val:
                continue
            words = val["词"]
            if key == "GAIN" and isinstance(words, dict):
                g["GAIN"] = {k: int(v) for k, v in words.items()}
            elif isinstance(words, list):
                g[key] = tuple(str(w) for w in words)

# 判定层开关（v11.3）：词义+语义+语境仲裁
ARBITRATION = True
ARB_MODE = "loose"   # strict: 只认语境认可; loose: 也允许"最匹配"覆盖


_LEXICON = _load_lexicon()
_apply_lexicon(_LEXICON)
LEXICON_SOURCE = "lexicon.yaml" if _LEXICON else "builtin"

# ============================================================
# 十一、否定作用域 + 一字容错 + 会话特征
# ============================================================

# "不/别/没" 之后若出现"你"，但句子里是这些"对象"，就不算关系否定
OBJECT_NOUNS = (
    "饭", "菜", "吃", "喝", "睡", "班", "课", "学", "考", "试", "工作", "公司", "学校",
    "钱", "药", "酒", "烟", "水", "游戏", "电影", "书", "街", "床", "辣", "咸", "甜",
    "冷", "热", "天气", "周末", "假", "车", "手机", "电脑", "打球", "运动", "熬夜",
)
NEG_MARKS = ("不", "别", "没", "甭")
QUESTION_TAIL = ("吗", "么", "呢", "？", "?")


def _lev1(a: str, b: str) -> bool:
    """编辑距离是否 ≤1。"""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(1 for x, y in zip(a, b) if x != y) <= 1
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = j = 0
    skipped = False
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
        elif skipped:
            return False
        else:
            skipped = True
            j += 1
    return True


NEG_HEADS = ("不", "没", "别", "未", "无", "非")


def fuzzy_contains(text: str, words) -> bool:
    """精确命中，或（词长≥3 时）允许 1 字误差。"""
    if not text:
        return False
    for w in words:
        if not w:
            continue
        if w in text:
            return True
        if len(w) >= 4:          # 3 字词太容易一改就变味（不要我/不要打），只认精确
            L = len(w)
            allow_indel = L >= 5        # 3 字词只允许替换，不允许增删
            for i in range(0, max(1, len(text) - L + 2)):
                spans = [(text[i:i + L], L)]
                if allow_indel:
                    spans += [(text[i:i + L - 1], L - 1), (text[i:i + L + 1], L + 1)]
                for span, need in spans:
                    if len(span) != need:
                        continue
                    # 否定词（不/没/别/未/无/非）首字必须精确：
                    # 否则「最喜欢你了」会被容错成「不喜欢你了」，语义直接反转
                    if w[0] in NEG_HEADS and span[:1] != w[0]:
                        continue
                    if _lev1(span, w):
                        return True
    return False


# 推开类短句：单独出现才是厌弃，带上下文就不算
# ---- 思的对象：人 / 事（v11.7）----
PERSON_WORDS = ("你", "我", "他", "她", "咱", "妈", "爸", "哥", "姐", "弟", "妹",
                "爷", "奶", "姥", "外婆", "外公", "叔", "姨", "姑", "舅",
                "朋友", "同学", "室友", "同事", "老师", "老婆", "老公", "孩子",
                "儿子", "女儿", "宝贝", "媳妇")
EVENT_WORDS = ("那次", "那天", "去年", "前年", "当年", "那年", "上个月", "前几天",
               "上次", "之前", "以前", "一开始", "最初", "当时", "那时候",
               "小时候", "后来", "从前", "过去", "刚见面", "刚认识", "第一天",
               "吵架", "分手", "考试", "毕业", "生日", "第一次")
# 过去事件的极性
PAST_NEG = ("吵架", "分手", "难过", "后悔", "遗憾", "失去", "离开", "哭", "疼",
            "错", "失望", "痛苦", "崩溃", "冷战", "闹", "生气", "伤")
PAST_POS = ("开心", "快乐", "美好", "好玩", "有趣", "幸福", "甜", "笑", "高兴",
            "温柔", "好看", "喜欢")

PUSH_WORDS = ("你去吧", "你走吧", "走吧", "去吧", "走开", "滚吧", "离我远点")
# 这些语境说明"去吧"是让你去忙/去休息，不是赶人
PUSH_CTX = ("打包", "上课", "教室", "作业", "吃饭", "睡觉", "睡", "忙", "上班",
            "电脑", "文件", "搬", "回去", "考", "课", "干活", "洗澡", "休息",
            "玩", "出门", "路上", "地铁", "外卖")


def reject_hit(text: str) -> bool:
    """关系否定命中，但这些情况不算推开：

    ① 带语境（去打包/去睡觉）→ 是让你去忙
    ② 推开词后面还跟着一大段内容 → 不是赶人，是在说别的事
    """
    if not fuzzy_contains(text, REJECT):
        return False
    if any(x in text for x in PUSH_CTX):
        return False
    for w in PUSH_WORDS:
        if w in text:
            rest = text.replace(w, "", 1)
            rest = re.sub(r"[\s……。，,！!？?、~～\-]+", "", rest)
            if len(rest) > 8:
                return False
    return True


def is_probe(text: str) -> bool:
    """他是在试探她怎么看他，而不是在自我否定或求证事实。"""
    t = text or ""
    if any(k in t for k in ("你会不会觉得", "你会不会认为", "你是不是觉得",
                            "你会不会嫌", "你是不是嫌", "在你眼里我")):
        return True
    if "你会不会" in t and ("我" in t and ("觉得" in t or "认为" in t)):
        return True
    return False


def negation_scope(text: str) -> tuple[bool, str]:
    """判定"否定是否指向她"。返回 (可疑, 原因)。

    「我不想吃饭」→ 不算；「我不想理你」→ 算。
    「你不爱吃咸吗？」→ 句尾是问号且有"吃/咸"这类对象词 → 不算（但可能误伤）。
    """
    if not text:
        return False, ""
    has_her = ("你" in text) or ("她" in text)

    # 问句 + 对象词 → 更可能是询问，不判为关系否定
    is_question = text.strip().endswith(QUESTION_TAIL)
    has_object = any(o in text for o in OBJECT_NOUNS)
    if is_question and has_object:
        return False, ""

    for i, ch in enumerate(text):
        if ch not in NEG_MARKS:
            continue
        window = text[max(0, i - 3):i + 9]     # 否定词前后一起看
        if ("你" in window or "她" in window) and not has_object:
            return True, f"否定后 {len(window)} 字内出现她"
    return False, ""
