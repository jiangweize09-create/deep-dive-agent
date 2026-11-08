"""Agent 层共用的消息工具函数（无依赖，供 sql_agent / viz_agent 复用）。"""


def _content_text(m) -> str:
    c = getattr(m, "content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(str(x) for x in c)
    return str(c)


def _parse_content(c):
    import json
    if isinstance(c, str):
        try:
            return json.loads(c)
        except Exception:
            return c
    return c


def _chunk_text(chunk) -> str:
    """从 AIMessageChunk 提取文本增量（content 为 str 或 text 分片列表）。"""
    c = getattr(chunk, "content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(p.get("text", "") for p in c if isinstance(p, dict))
    return ""
