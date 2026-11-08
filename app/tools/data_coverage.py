"""数据覆盖范围工具：SQL 子 Agent 的第 10 个工具（按需查询，非常驻注入）。

使用场景：查询结果为 0 行时，AI 先调本工具看「各表实际数据的时间范围」，
再决定是修正时间范围重查，还是直接说明无数据——替代原来的「复查三连」
（复核区间 → 查全表 MIN/MAX → 查行数，几千 token），一次调用搞定。

区间在调用那一刻现算（不是快照）：活库新进数据、刚上传的表，下一次调用
自动就是最新值，不存在预注入的过时误导。
"""

import asyncio
import json

from langchain_core.tools import tool


def data_coverage_core() -> dict:
    """即时计算各表覆盖区间，返回结构化结果 + 给 AI 的紧凑文本。"""
    from ..semantic.coverage import coverage_note, coverage_rows

    rows = coverage_rows()
    if not rows:
        return {"ok": True, "tables": [],
                "note": "各表均无可识别的日期列，无法给出覆盖范围；请按语义层的字段自行判断"}
    return {"ok": True, "tables": rows,
            "note": "各表实际数据的时间范围（确定性事实）：\n" + coverage_note()
                    + "\n用户询问时段超出某表范围即为空：要么修正时间范围重查，要么直接说明无数据并给最近可答区间。"}


@tool
async def data_coverage() -> str:
    """查询每张表（含上传表）实际覆盖的时间范围（最早~最晚的日期列值）。
    当查询结果为 0 行、或不确定用户询问的时段是否有数据时调用本工具，
    据此决定：修正时间范围重查，或直接说明该时段无数据并给最近可答区间。
    不要在正常查询前例行调用（覆盖信息只在需要判断时才有用）。"""
    return json.dumps(await asyncio.to_thread(data_coverage_core), ensure_ascii=False)
