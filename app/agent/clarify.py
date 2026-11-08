"""口径澄清 HITL（Human-in-the-Loop）：歧义指标先反问用户，确认后再执行。

设计要点：
1. 检测是确定性的（纯代码扫语义层别名表，不耗 LLM）——同一术语同时命中
   多个口径不同的指标（expression / required_filters 不一致）才算歧义；
   "上个月GMV多少"这类无歧义问题一次都不会被打断。
2. 中断/恢复走 LangGraph interrupt/resume：主图 clarify 节点 interrupt() 暂停，
   线程状态由 checkpointer 持有；用户选择后以 Command(resume=...) 续跑，
   节点把「口径确认」写成 SystemMessage 注回上下文，主 Agent 按确认口径委派。
3. 同一会话同一术语只问一次：消息流里已有「口径确认（HITL）」标记的术语
   不再触发，防止无限反问循环。
4. 更具体的说法优先：用户说「净销售额」时不再追问「销售额」（长术语覆盖短术语）。
"""

import re
from functools import lru_cache

MARKER = "口径确认（HITL）"


def _short_display(display_name: str) -> str:
    """GMV（商品交易总额）-> GMV：选项标签用短名，口径细节放 hint。"""
    return re.split(r"[（(]", display_name, 1)[0].strip()


@lru_cache(maxsize=1)
def _alias_map() -> dict[str, list]:
    """术语 -> 命中的指标列表（display_name 全名/短名 + 同义词，长度 >= 2）。"""
    from ..semantic.loader import get_catalog

    amap: dict[str, list] = {}
    for m in get_catalog().metrics:
        for t in {_short_display(m.display_name), m.display_name, *m.synonyms}:
            t = (t or "").strip()
            if len(t) >= 2:
                amap.setdefault(t, []).append(m)
    return amap


def ambiguous_terms() -> dict[str, list]:
    """全部歧义术语表（术语 -> 口径不同的指标列表）。"""
    out = {}
    for term, mets in _alias_map().items():
        if len(mets) < 2:
            continue
        keys = {(m.expression, tuple(m.required_filters)) for m in mets}
        if len(keys) >= 2:
            out[term] = mets
    return out


def clarified_terms(messages) -> set:
    """消息流里已确认过口径的术语集合（解析口径确认 SystemMessage 标记）。"""
    out = set()
    for m in messages or []:
        content = getattr(m, "content", "")
        if getattr(m, "type", "") != "system" or not isinstance(content, str):
            continue
        if MARKER not in content:
            continue
        out.update(re.findall(r"「(.+?)」", content.split("\n", 1)[0]))
    return out


def detect_ambiguity(message: str, already=None) -> dict | None:
    """用户消息含歧义术语时返回澄清问题 payload（dict），否则 None。

    payload 即 SSE 事件 clarify 的 body：question + options[{value,label,hint}]，
    value 是指标名，resume 时原样回传给 interrupt()。
    """
    msg = message or ""
    if len(msg) < 2:
        return None
    skip = already or set()
    all_terms = _alias_map()
    for term in sorted(ambiguous_terms(), key=len, reverse=True):   # 长术语优先
        if term in skip or term not in msg:
            continue
        # 只是更长说法的组成部分（如「净销售额」里的「销售额」）不算本次提问对象
        if any(len(t) > len(term) and term in t and t in msg for t in all_terms):
            continue
        return {
            "term": term,
            "question": f"「{term}」在语义层存在两种口径，数字会完全不同。请确认按哪一种计算：",
            "options": [
                {"value": m.name, "label": _short_display(m.display_name),
                 "hint": (m.description or "").rstrip("。")}
                for m in _alias_map()[term]
            ],
        }
    return None


# 数据源歧义：用户泛指「这个数据」而系统里同时存在内置表与上传表时反问。
# 上不扫全库的关键一步——否则「这个数据有几个月」会把内置+上传表全部查一遍。
DATA_REF_RE = re.compile(r"这个数据|这份数据|这些数据|我上传的|我传的|上传的数据|我的数据|这个表|这张表")


def detect_source_ambiguity(message: str, already=None) -> dict | None:
    """「这个数据」类泛指 + 存在上传表 → 反问指内置演示数据还是哪张上传表。"""
    msg = message or ""
    if not DATA_REF_RE.search(msg):
        return None
    if "数据范围" in (already or set()):
        return None
    from ..uploads import list_uploads
    try:
        tables = list_uploads()["tables"] or []
    except Exception:
        return None
    if not tables:
        return None
    options = [{"value": "__builtin__", "label": "内置演示数据",
                "hint": "orders / users 等 6 张电商表（近 12 个月）"}]
    for t in tables[:4]:
        options.append({"value": t["name"], "label": t.get("display") or t["name"],
                        "hint": f"{t['name']} · 约 {t.get('rows', '?')} 行"})
    return {
        "term": "数据范围",
        "question": "你说的「这个数据」指的是哪一份？",
        "options": options,
    }


def record_caliber_choice(term: str, answer: dict) -> None:
    """口径卡上明确选择后记入长期偏好（跨会话，该术语不再反问）。

    只记指标口径——「数据范围」是逐题的（这轮问内置表、下轮可能问上传表），不入库。
    自由作答（value 为空）不算明确选择，也不记。
    """
    answer = answer if isinstance(answer, dict) else {}
    value = str(answer.get("value") or "")
    if not value or term == "数据范围":
        return
    met = next((m for m in _alias_map().get(term, []) if m.name == value), None)
    if met is None:
        return
    from ..memory import save_pref

    label = _short_display(met.display_name)
    caliber = met.expression + (
        f"（必须带 {' AND '.join(met.required_filters)}）" if met.required_filters else "")
    try:
        save_pref("caliber", term, f"{label}｜口径：{caliber}", source="hitl")
    except Exception:
        pass    # 记忆失败不影响本轮确认口径的正常注回


def build_confirm_message(term: str, answer: dict) -> str:
    """把用户的选择/自由说明落成注回主 Agent 的口径确认 SystemMessage。"""
    answer = answer if isinstance(answer, dict) else {}
    value = str(answer.get("value") or "")
    label = str(answer.get("label") or "")
    text = str(answer.get("text") or "").strip()

    if term == "数据范围":
        if value == "__builtin__":
            chosen = "内置演示数据（orders / users / products 等内置表）"
        elif value:
            chosen = f"上传表 {value}（只查这张表，不要去扫内置表）"
        elif text:
            chosen = f"用户自由说明：{text[:200]}"
        else:
            chosen = f"{label or '未识别的选择'}"
        return (f"{MARKER}：「数据范围」= {chosen}。后续委派与查询必须严格限定在该数据范围内，"
                f"最终回答中说明所用数据，不要再反问。")

    mets = {m.name: m for m in _alias_map().get(term, [])}
    m = mets.get(value)
    if m is not None:
        caliber = m.expression + (
            f"（必须带 {' AND '.join(m.required_filters)}）" if m.required_filters else "")
        chosen = f"{m.display_name}（{m.description}；口径：{caliber}）"
    elif text:
        chosen = f"用户自由说明：{text[:200]}"
    else:
        chosen = f"{label or '未识别的选择'}（按 SQL Agent 检索到的标准口径执行）"
    return (f"{MARKER}：「{term}」= {chosen}。后续委派与计算必须严格采用该口径，"
            f"最终回答中向用户重申此口径，不要再反问。")
