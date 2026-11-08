"""语义层检索工具：search_schema / search_metrics。

core 函数返回 dict（demo 模式与测试直接调用）；
@tool 包装层序列化为 JSON 字符串供 LLM 消费。
"""

import json

from langchain_core.tools import tool

from ..semantic.loader import get_catalog


def search_schema_core(query: str) -> dict:
    return get_catalog().search_tables(query)


def search_metrics_core(query: str) -> dict:
    return get_catalog().search_metrics(query)


@tool
async def search_schema(query: str) -> str:
    """检索相关表结构。输入业务问题或关键词（如：订单、用户、地区），
    返回匹配表的字段清单（名称/类型/说明/样例）、join 关系与 PII 标记。
    写 SQL 前必须先调用本工具确认表和字段名。"""
    return json.dumps(search_schema_core(query), ensure_ascii=False)


@tool
async def search_metrics(query: str) -> str:
    """检索指标定义。输入指标关键词（如：GMV、成交额、客单价、退款率），
    返回匹配指标的表达式、required_filters（必带过滤条件）、可用维度与同义词。
    计算指标前必须先调用本工具，严格按返回的口径写 SQL。"""
    return json.dumps(search_metrics_core(query), ensure_ascii=False)
