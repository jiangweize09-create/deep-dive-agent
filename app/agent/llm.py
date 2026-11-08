"""LLM 客户端：ChatOpenAI 子类 + 思考链（reasoning）透传。

DeepSeek / GLM 等 OpenAI 兼容端点在流式 delta 里返回 `reasoning_content`
（模型内部推理草稿）。langchain-openai 1.x 的解析层不认识这个字段，直接丢弃。
本模块在「原始 chunk → AIMessageChunk」的转换点把它捞回来，挂到
`message_chunk.additional_kwargs["reasoning_content"]`（逐段累加），供
SSE 层转发成 reasoning 事件、前端渲染成「深度思考」折叠区。

注意：`_convert_chunk_to_generation_chunk` 是 langchain-openai 的内部方法，
升级该库时若签名变化，此处会显式报错（TypeError），冒烟测试兜得住。
"""

from langchain_openai import ChatOpenAI


class ReasoningChatOpenAI(ChatOpenAI):
    """ChatOpenAI + reasoning_content 透传（仅流式 astream/astream_events 路径生效）。"""

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class,
                                           base_generation_info):
        gen = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info)
        if gen is None:
            return None
        rc = _raw_reasoning(chunk)
        if rc:
            m = gen.message
            m.additional_kwargs["reasoning_content"] = \
                m.additional_kwargs.get("reasoning_content", "") + rc
        return gen


def _raw_reasoning(chunk: dict) -> str:
    """从原始 SSE chunk 里取 reasoning_content（兼容普通与 beta 两种包裹形状）。"""
    choices = chunk.get("choices") or (chunk.get("chunk") or {}).get("choices") or []
    for choice in choices:
        delta = choice.get("delta") or {}
        rc = delta.get("reasoning_content")
        if isinstance(rc, str) and rc:
            return rc
    return ""


def chunk_reasoning(m) -> str:
    """从 AIMessageChunk 取思考增量（子类透传后存放于 additional_kwargs）。"""
    kw = getattr(m, "additional_kwargs", None) or {}
    rc = kw.get("reasoning_content")
    return rc if isinstance(rc, str) else ""
