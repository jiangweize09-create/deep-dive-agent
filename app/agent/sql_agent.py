"""SQL 子 Agent（子图即工具模式）。

多 Agent 架构：
- 主 Agent（graph.py）：调度者，绑 delegate_sql / rag_retrieve / delegate_viz，上下文干净
- SQL 子 Agent（本文件）：独立 ReAct 循环，持有全部数据工具（检索/日期/校验/执行/图表）

关键设计：
1. 子图即工具 —— 子 Agent 编译成独立 LangGraph 图，包装成 @tool 塞给主 Agent。
   主 Agent 的上下文里只有一个 delegate_sql 的 schema，看不到 6 个数据工具。
2. 只回结论 —— 子 Agent 的过程事件（thinking/step/result）写入请求级
   ContextVar 事件总线（subagent_events），由 SSE 层 / 评测器旁路消费；
   delegate_sql 的 ToolMessage 里只保留 final 结论与 usage，
   主 Agent 上下文不随执行步数膨胀。
3. 护栏仍在 run_sql 工具内部（不跨层）—— 子 Agent 内部怎么折腾都出不了安全边界。
4. 子图不带 checkpointer —— 每次委派是一次性任务；会话记忆在主图。

ContextVar 请求隔离约定（必须遵守）：
- SSE 层 / 评测器在请求开始时 subagent_events.set([])，finally 中 reset(token)；
  不 reset 的话，线程/任务复用会把上一个请求的事件串进下一个请求。
- run_sql_agent 只在总线存在时写入（get() 为 None 时静默丢弃，便于独立调用）。
"""

import asyncio
import json
from datetime import date
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from ..config import settings
from ..tools import get_tools
from .prompts import SQL_AGENT_PROMPT, SQL_WRAP_UP_PROMPT

SUB_RECURSION_LIMIT = 40   # 子 Agent 内部循环上限（含每次工具往返）

# 请求级事件总线已抽到 events.py（多个子 Agent 共用）；此处保留同名导出，
# main.py / eval.runner 的既有 import 不受影响
from .events import subagent_events, subagent_queue  # noqa: E402,F401
from .messages import _chunk_text, _content_text, _parse_content  # noqa: E402,F401


class SubAgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int


_prompt_cache: tuple[str, str] = ("", "")   # (日期, prompt)，跨天自动失效


def _sql_prompt() -> str:
    """SQL_AGENT_PROMPT + 当天日期。日期变了自动重建（服务常驻也不会过期）。"""
    global _prompt_cache
    today = date.today().isoformat()
    if _prompt_cache[0] != today:
        _prompt_cache = (today, f"{SQL_AGENT_PROMPT}\n\n今天是 {today}。")
    return _prompt_cache[1]


def build_sql_agent(base_url: str, api_key: str, model: str, allow: list[str] | None = None):
    """SQL 子 Agent：与旧版单 Agent 主图同构的两节点 ReAct 循环。

    allow 是工具白名单（前端「工具」页的开关），None/空表示全开。
    run_sql / validate_sql 由 get_tools 内部锁定，关不掉。
    """
    from .llm import ReasoningChatOpenAI as ChatOpenAI

    llm = ChatOpenAI(base_url=base_url, api_key=api_key, model=model, temperature=0, timeout=120)
    tools = get_tools(allow)
    llm_tools = llm.bind_tools(tools)
    max_steps = settings.max_steps

    async def agent_node(state: SubAgentState):
        msgs = state["messages"]
        if not (msgs and getattr(msgs[0], "type", "") == "system"):
            msgs = [SystemMessage(content=_sql_prompt())] + list(msgs)
        steps = state.get("steps", 0)
        if steps >= max_steps:
            resp = await llm.ainvoke(msgs + [SystemMessage(content=SQL_WRAP_UP_PROMPT)])
        else:
            resp = await llm_tools.ainvoke(msgs)
        return {"messages": [resp], "steps": steps + 1}

    def route(state: SubAgentState):
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return END

    g = StateGraph(SubAgentState)
    g.add_node("agent", agent_node)
    g.add_node("tools", ToolNode(tools))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route, {"tools": "tools", END: END})
    g.add_edge("tools", "agent")
    return g.compile()   # 无 checkpointer：委派为一次性任务


async def run_sql_agent(graph, task: str) -> dict:
    """执行一次 SQL 子 Agent。过程事件实时走快车道，批量留档进列表总线。"""
    inputs = {"messages": [HumanMessage(content=task)], "steps": 0}
    config = {"recursion_limit": SUB_RECURSION_LIMIT}
    events: list[dict] = []   # 留档（评测器总线 + 句柄提取），不含 sub_delta 碎片
    final = ""
    usage = {"steps": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    from langgraph.errors import GraphRecursionError

    q = subagent_queue.get()

    def push(ev: dict) -> None:
        """快车道：live SSE 的合并循环实时消费；评测/冒烟进程没有队列，静默跳过。"""
        if q is not None:
            q.put_nowait(ev)

    try:
        async for item in graph.astream(inputs, config=config, stream_mode=["updates", "messages"]):
            # 双模式产出 (mode, chunk)；伪图/旧测试桩可能直接吐 update dict，兜底当 updates
            if isinstance(item, tuple) and len(item) == 2 and item[0] in ("updates", "messages"):
                mode, chunk = item
            else:
                mode, chunk = "updates", item
            if mode == "messages":
                # token 级直播：子 Agent 正在生成的文字（播报句/结论）实时下发。
                # 只走快车道不进留档 —— 评测不需要 token 碎片
                msg_chunk, meta = chunk
                if isinstance(meta, dict) and meta.get("langgraph_node") == "agent":
                    from .llm import chunk_reasoning
                    r = chunk_reasoning(msg_chunk)
                    if r:
                        push({"type": "reasoning", "agent": "sql", "text": r})
                    text = _chunk_text(msg_chunk)
                    if text:
                        push({"type": "sub_delta", "agent": "sql", "text": text})
                continue
            for node, update in chunk.items():
                if not isinstance(update, dict) or "messages" not in update:
                    continue
                for m in update["messages"]:
                    if node == "agent":
                        usage["steps"] += 1
                        tcs = getattr(m, "tool_calls", None) or []
                        text = _content_text(m)
                        if text and tcs:
                            ev = {"type": "thinking", "text": text}
                            events.append(ev)
                            push(ev)
                        for tc in tcs:
                            ev = {"type": "step", "tool": tc.get("name", ""), "args": tc.get("args", {})}
                            events.append(ev)
                            push(ev)
                        if text and not tcs:
                            final = text
                        um = getattr(m, "usage_metadata", None)
                        if um:
                            usage["input_tokens"] += um.get("input_tokens") or 0
                            usage["output_tokens"] += um.get("output_tokens") or 0
                            usage["total_tokens"] += um.get("total_tokens") or 0
                    elif node == "tools":
                        ev = {"type": "result", "tool": getattr(m, "name", ""),
                              "content": _parse_content(getattr(m, "content", ""))}
                        events.append(ev)
                        push(ev)
    except GraphRecursionError:
        ev = {"type": "error", "message": "子 Agent 已达最大轮次，强制收尾。"}
        events.append(ev)
        push(ev)
        if not final:
            final = "子 Agent 执行超限，未能得到结论，请尝试缩小问题范围。"

    # 留档总线：评测器 / 冒烟测试批量消费（live 进程只设了队列，此处自然跳过）
    bus = subagent_events.get()
    if bus is not None:
        bus.extend(events)

    # 收集落表句柄：persist_result 的结果不进 LLM 结论（防复述进上下文），
    # 由报告 dict 显式带回给主 Agent，供 delegate_viz 使用
    handles = [e["content"]["handle"] for e in events
               if e.get("type") == "result" and e.get("tool") == "persist_result"
               and isinstance(e.get("content"), dict) and e["content"].get("ok")]

    return {
        "ok": bool(final),
        "type": "subagent_report",
        "agent": "sql",
        "task": task,
        "final": final,
        "handles": handles,
        "usage": usage,
    }


def make_delegate_tools(base_url: str, api_key: str, model: str, allow: list[str] | None = None) -> list:
    """构造主 Agent 的工具集：delegate_sql（查数）+ rag_retrieve（口径/方法）
    + delegate_viz（报告，需传 handle）。

    rag_retrieve 是普通工具而不是子 Agent：它只有一个动作、没有决策空间，
    包一层 ReAct 循环是伪多 Agent，白烧一倍 LLM 调用。子 Agent 只留给
    真正有多步决策的执行者（SQL / viz）。接入 MCP 等新子 Agent 时在此追加。"""
    sql_graph = build_sql_agent(base_url, api_key, model, allow)

    from ..rag import retrieve

    @tool
    async def rag_retrieve(query: str) -> str:
        """检索 Deep Dive 知识库（指标口径手册、方法论技能库：SWOT/波士顿矩阵/波特五力/各分析 SOP）。
        命中技能时按技能的执行步骤与输出模板完成任务。
        query 用业务关键词（如：GMV 口径 / 大促复盘 / 退款率异常 / 渠道分析）。
        返回 top-k 知识片段（含来源文件与小节标题），引用时必须标注来源；
        检索不到相关内容时如实说明，禁止编造。"""
        # 索引懒构建 + 检索都是同步 CPU 操作，丢线程池不卡事件循环
        result = await asyncio.to_thread(retrieve.search, query)
        return json.dumps(result, ensure_ascii=False)

    @tool
    async def generate_dashboard(table: str) -> str:
        """为上传的数据表生成 AI 分析看板（LLM 读列结构与样本值规划一次，
        之后看板页每次渲染都是确定性 SQL，零 token 秒开）。
        table 传上传表名（如 up_restaurant）；适合的表会返回看板标题与
        KPI/图表数量，用户可在「看板」页顶部数据源切换查看；
        不适合生成看板（缺时间列/数值列）会返回原因与建议。"""
        from ..dash_spec import generate_dashboard_spec
        result = await generate_dashboard_spec(table)
        return json.dumps(result, ensure_ascii=False)

    @tool
    async def delegate_sql(task: str) -> str:
        """把数据分析任务交给 SQL Agent 执行。它能：检索指标口径与表结构、
        解析时间词（上个月/近6个月/今年Q2）为准确日期、生成并执行受护栏保护
        的 SQL（只读、PII 拦截、自动 LIMIT）、生成图表；任务要求报告/导出时，
        它还会把结果集落成临时表并在返回值里给出句柄 handles。
        task 传用户的数据问题原文（如：上个月 GMV 多少？近 6 个月订单量趋势）。
        返回 JSON 报告：final 为结论（Markdown），handles 为结果集句柄（供
        delegate_viz 使用），usage 为用量统计。"""
        try:
            report = await run_sql_agent(sql_graph, task)
        except Exception as e:
            err = {"type": "error", "message": f"子 Agent 执行失败：{e}"}
            bus = subagent_events.get()
            if bus is not None:
                bus.append(err)
            report = {"ok": False, "type": "subagent_report", "agent": "sql", "task": task,
                      "final": "", "handles": [], "usage": {}}
        return json.dumps(report, ensure_ascii=False)

    from .viz_agent import make_delegate_viz

    return [delegate_sql, rag_retrieve, generate_dashboard] + make_delegate_viz(base_url, api_key, model)
