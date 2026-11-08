"""日期解析工具：把中文时间词换算成确定的日期区间。

为什么做成工具而不是让 LLM 心算：跨年、月末、大小月正是 LLM
最容易算错的地方（如把「上个月」算成 31 天前）。本工具用纯
Python 日期库换算，SQL 中的日期一律以本工具返回值为准。

口径统一为左闭右开（start <= x < end）：
- 上个月（今天 2026-10-12）= 2026-09-01 ~ 2026-10-01
- 近 6 个月 = 最近 6 个完整自然月（2026-04-01 ~ 2026-10-01，不含本月）
- 近 7 天 = 含今天往前共 7 个自然日
"""

import calendar
import json
import re
from datetime import date, timedelta

from langchain_core.tools import tool

# 中文数字 -> 阿拉伯（够覆盖 N 个月 / N 天 / 季度；先长后短替换）
_CN_NUM = {"十一": "11", "十二": "12", "十": "10", "一": "1", "两": "2", "二": "2",
           "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}


def _add_months(d: date, n: int) -> date:
    """月份加 n（可为负），天数 clamp 到目标月月末。"""
    total = d.year * 12 + (d.month - 1) + n
    y, m0 = divmod(total, 12)
    m = m0 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _norm(expr: str) -> str:
    """归一化：去空白、中文数字转阿拉伯、统一小写。"""
    s = re.sub(r"\s+", "", expr or "")
    for k in sorted(_CN_NUM, key=len, reverse=True):
        if k in s:
            s = s.replace(k, _CN_NUM[k])
    return s.lower()


def _ok(expr: str, start: date, end: date, note: str) -> dict:
    return {
        "ok": True,
        "expr": expr,
        "today": date.today().isoformat(),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "interval": "左闭右开：start <= x < end",
        "sql_hint": f"<时间字段> >= '{start.isoformat()}' AND <时间字段> < '{end.isoformat()}'",
        "note": note,
    }


def _fail(expr: str) -> dict:
    return {
        "ok": False,
        "expr": expr,
        "reason": "无法识别的时间表达",
        "suggestion": "支持：上个月/本月/上上个月/近N个月/近N天/昨天/上周/本周/今年/去年/"
                     "今年Q3/上季度/2026-09/2026-09-15/2026-09-01~2026-10-01",
    }


def resolve_date_range_core(expr: str) -> dict:
    raw = (expr or "").strip()
    s = _norm(raw)
    today = date.today()
    first = today.replace(day=1)          # 本月 1 号
    monday = today - timedelta(days=today.weekday())   # 本周一

    def _day(txt: str):
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", txt)
        try:
            return date(int(m[1]), int(m[2]), int(m[3])) if m else None
        except ValueError:
            return None

    try:
        # 1) 显式区间：2026-09-01~2026-10-01 / 到 / 至
        m = re.fullmatch(r"(\d{4}-\d{1,2}-\d{1,2})[~到至](\d{4}-\d{1,2}-\d{1,2})", s)
        if m:
            a, b = _day(m[1]), _day(m[2])
            if a and b:
                if a > b:
                    a, b = b, a
                return _ok(raw, a, b, f"{a} 到 {b}（左闭右开，不含 {b} 当天）")

        # 2) 单日：2026-09-15 / 2026年9月15日
        m = re.fullmatch(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?", s)
        if m:
            d = date(int(m[1]), int(m[2]), int(m[3]))
            return _ok(raw, d, d + timedelta(days=1), f"{d} 一个自然日")

        # 3) 年月：2026-09 / 2026年9月
        m = re.fullmatch(r"(\d{4})[-/年.](\d{1,2})月?", s)
        if m:
            start = date(int(m[1]), int(m[2]), 1)
            return _ok(raw, start, _add_months(start, 1), f"{int(m[1])}-{int(m[2]):02d} 整个自然月")

        # 4) 季度：今年Q2 / 2026Q2 / 2026年2季度 / 二季度 / 去年Q4 / 上季度
        m = re.fullmatch(r"(?:(\d{4})年?|(今|去|前)年?)?q([1-4])", s) or \
            re.fullmatch(r"(?:(\d{4})年?|(今|去|前)年?)?([1-4])季度", s)
        if m:
            year = int(m[1]) if m[1] else (today.year - {"今": 0, "去": 1, "前": 2}[m[2]] if m[2] else today.year)
            start = date(year, 3 * int(m[3]) - 2, 1)
            return _ok(raw, start, _add_months(start, 3), f"{year} 年 Q{int(m[3])}")
        if s in ("本季度", "这个季度"):
            qn = (today.month - 1) // 3 + 1
            start = date(today.year, 3 * qn - 2, 1)
            return _ok(raw, start, _add_months(start, 3), f"{today.year} 年 Q{qn}")
        if s in ("上季度", "上个季度"):
            cur_q_start = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)  # 本季度首月 1 号
            start = _add_months(cur_q_start, -3)
            return _ok(raw, start, cur_q_start,
                       f"上季度 = {start.year} 年 Q{(start.month - 1) // 3 + 1}")

        # 5) 相对月
        if s in ("上个月", "上月"):
            start = _add_months(first, -1)
            return _ok(raw, start, first, f"上个月 = {start:%Y-%m} 整个自然月")
        if s in ("上上个月", "上上月"):
            start = _add_months(first, -2)
            return _ok(raw, start, _add_months(first, -1), f"上上个月 = {start:%Y-%m} 整个自然月")
        if s in ("本月", "这个月", "当月"):
            return _ok(raw, first, _add_months(first, 1),
                       f"本月 = {today:%Y-%m} 自然月（月末未到，数据可能不全）")
        m = re.fullmatch(r"(?:近|最近|过去)(\d{1,2})个?月", s)
        if m:
            n = int(m[1])
            return _ok(raw, _add_months(first, -n), first,
                       f"近 {n} 个月 = 最近 {n} 个完整自然月（不含本月）")
        if s in ("近半年", "最近半年", "半年", "半年内"):
            return _ok(raw, _add_months(first, -6), first, "近半年 = 最近 6 个完整自然月（不含本月）")

        # 6) 相对天
        if s in ("今天", "今日", "当天"):
            return _ok(raw, today, today + timedelta(days=1), f"今天（{today}）一个自然日")
        if s in ("昨天", "昨日"):
            y = today - timedelta(days=1)
            return _ok(raw, y, today, f"昨天 = {y} 一个自然日")
        if s == "前天":
            d = today - timedelta(days=2)
            return _ok(raw, d, today - timedelta(days=1), f"前天 = {d} 一个自然日")
        m = re.fullmatch(r"(?:近|最近|过去)(\d{1,4})天", s)
        if m:
            n = int(m[1])
            return _ok(raw, today - timedelta(days=n - 1), today + timedelta(days=1),
                       f"近 {n} 天 = 含今天往前共 {n} 个自然日")

        # 7) 周（周一为一周起点）
        if s in ("本周", "这周", "这一周"):
            return _ok(raw, monday, monday + timedelta(days=7), f"本周 = {monday} 起 7 天")
        if s in ("上周", "上一周"):
            start = monday - timedelta(days=7)
            return _ok(raw, start, monday, f"上周 = {start} 起 7 天")
        if s == "上上周":
            start = monday - timedelta(days=14)
            return _ok(raw, start, monday - timedelta(days=7), f"上上周 = {start} 起 7 天")

        # 8) 年
        if s in ("今年", "本年"):
            return _ok(raw, date(today.year, 1, 1), date(today.year + 1, 1, 1),
                       f"今年 = {today.year} 全年（年末未到，数据可能不全）")
        if s in ("去年", "上一年"):
            return _ok(raw, date(today.year - 1, 1, 1), date(today.year, 1, 1),
                       f"去年 = {today.year - 1} 全年")
        if s == "前年":
            return _ok(raw, date(today.year - 2, 1, 1), date(today.year - 1, 1, 1),
                       f"前年 = {today.year - 2} 全年")
    except ValueError:
        return _fail(raw)

    return _fail(raw)


@tool
async def resolve_date_range(expr: str) -> str:
    """把时间表达解析为确定的起止日期。支持：上个月/本月/上上个月/近N个月/
    近N天/昨天/上周/本周/今年/去年/今年Q3/上季度/2026-09/2026-09-15/
    2026-09-01~2026-10-01 等。返回 start_date、end_date（左闭右开）与
    sql_hint。写 SQL 时凡涉及时间范围，必须先调用本工具，SQL 中的日期
    一律使用返回值，禁止自行推算日期。"""
    return json.dumps(resolve_date_range_core(expr), ensure_ascii=False)
