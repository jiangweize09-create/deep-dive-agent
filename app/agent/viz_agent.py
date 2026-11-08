"""viz/报告子 Agent（子图即工具）——第三个子 Agent。

职责：凭 SQL 子 Agent 落表返回的句柄取数，产出洞察、图表、Excel 与
单文件 HTML 分析报告。

与 SQL / RAG 子 Agent 的关键差异 —— 真代码 reflection 条件边：
ReAct 循环之外多一个 review 节点（纯 Python，无 LLM）。Agent 给出结论后
并不直接结束，而是先过确定性验收：
  1. 数据可绘图（≥2 行且有数值列）但一张图表都没登记 -> 打回补图表
  2. 没有调用 build_report 组装报告 -> 打回补报告
验收不过就把反馈作为消息注回 agent 节点返工（最多 1 轮，防振荡）。
「质量标准」写在代码里而不是 prompt 里，这就是与『套壳』的分界线。

安全边界不变：本 Agent 没有任何查库工具，只能凭句柄取数 —— 句柄由
scratch 层做正则 + 会话指纹双重校验，跨会话数据拿不到。
"""

import json

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing import Annotated, TypedDict

from .events import subagent_events, subagent_queue
from .messages import _chunk_text, _content_text, _parse_content
from .prompts import VIZ_AGENT_PROMPT, WRAP_UP_PROMPT

VIZ_MAX_STEPS = 6        # 取数 -> 图表 -> Excel -> 报告，4 步足够；防呆上限
VIZ_MAX_REPAIRS = 1      # review 打回次数上限（防振荡）


class VizAgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int
    repairs: int


def _review_feedback() -> list[str]:
    """确定性验收：读 report store 的登记状态，列出未完成项（纯代码，无 LLM）。"""
    from ..report import store
    rep = store.current_report.get()
    if rep is None:
        return ["报告上下文丢失，请直接说明无法生成报告。"]
    feedback = []
    if not rep["charts"] and rep["row_count"] >= 2 and rep["numeric_cols"]:
        feedback.append("结果集可绘图但你还没有生成任何图表。请用 render_chart "
                        f"基于列 {', '.join(rep['numeric_cols'][:6])} 生成至少一张图表。")
    if not rep["charts"] and not rep["summary_md"]:
        feedback.append("报告还没有任何产物。请完成：fetch_data 取数 -> render_chart "
                        "图表 -> build_report 组装。")
    return feedback


def build_viz_agent(base_url: str, api_key: str, model: str):
    """viz 子 Agent：agent <-> tools ReAct 循环 + review 条件边。"""
    from .llm import ReasoningChatOpenAI as ChatOpenAI

    from ..report.tools import build_report, export_excel, fetch_data, render_chart

    llm = ChatOpenAI(base_url=base_url, api_key=api_key, model=model, temperature=0, timeout=120)
    tools = [fetch_data, render_chart, export_excel, build_report]
    llm_tools = llm.bind_tools(tools)

    async def agent_node(state: VizAgentState):
        msgs = state["messages"]
        if not (msgs and getattr(msgs[0], "type", "") == "system"):
            msgs = [SystemMessage(content=VIZ_AGENT_PROMPT)] + list(msgs)
        steps = state.get("steps", 0)
        if steps >= VIZ_MAX_STEPS:
            resp = await llm.ainvoke(msgs + [SystemMessage(content=WRAP_UP_PROMPT)])
        else:
            resp = await llm_tools.ainvoke(msgs)
        return {"messages": [resp], "steps": steps + 1}

    def tools_route(state: VizAgentState):
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return "review"   # 结论不直接出图：先过确定性验收

    async def review_node(state: VizAgentState):
        """真代码 reflection：纯 Python 验收，未达标生成返工反馈。"""
        feedback = _review_feedback()
        if not feedback or state.get("repairs", 0) >= VIZ_MAX_REPAIRS:
            return {"repairs": state.get("repairs", 0)}
        return {"messages": [SystemMessage(content="【质量验收未通过，返工】" + "；".join(feedback))],
                "repairs": state.get("repairs", 0) + 1}

    def review_route(state: VizAgentState):
        last = state["messages"][-1]
        if isinstance(last, SystemMessage) and getattr(last, "content", "").startswith("【质量验收未通过"):
            return "agent"
        return END

    g = StateGraph(VizAgentState)
    g.add_node("agent", agent_node)
    g.add_node("tools", ToolNode(tools))
    g.add_node("review", review_node)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_route, {"tools": "tools", "review": "review"})
    g.add_edge("tools", "agent")
    g.add_conditional_edges("review", review_route, {"agent": "agent", END: END})
    return g.compile()   # 无 checkpointer：委派为一次性任务


async def run_viz_agent(graph, task: str, handle: str) -> dict:
    """执行一次 viz 子 Agent。过程事件旁路总线；报告链接从 store 取。"""
    from langgraph.errors import GraphRecursionError

    from ..report import store

    inputs = {"messages": [HumanMessage(content=(
        f"报告任务：{task}\n\n数据句柄：{handle}\n"
        "请完成：fetch_data -> render_chart（至少一张）-> export_excel -> build_report，"
        "并给出结论。"))], "steps": 0}
    config = {"recursion_limit": 24}
    events: list[dict] = []   # 留档（评测器总线），不含 sub_delta 碎片
    final = ""
    usage = {"steps": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

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
                # token 级直播：报告 Agent 正在生成的文字实时下发（只走快车道）
                msg_chunk, meta = chunk
                if isinstance(meta, dict) and meta.get("langgraph_node") == "agent":
                    from .llm import chunk_reasoning
                    r = chunk_reasoning(msg_chunk)
                    if r:
                        push({"type": "reasoning", "agent": "viz", "text": r})
                    text = _chunk_text(msg_chunk)
                    if text:
                        push({"type": "sub_delta", "agent": "viz", "text": text})
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
                    elif node == "review" and update.get("repairs", 0) > 0:
                        # 返工反馈也实时旁路给前端：让用户看到「验收打回」这一步
                        text = _content_text(update["messages"][-1])
                        ev = {"type": "thinking", "text": text}
                        events.append(ev)
                        push(ev)
                    elif node == "tools":
                        ev = {"type": "result", "tool": getattr(m, "name", ""),
                              "content": _parse_content(getattr(m, "content", ""))}
                        events.append(ev)
                        push(ev)
    except GraphRecursionError:
        ev = {"type": "error", "message": "报告子 Agent 已达最大轮次。"}
        events.append(ev)
        push(ev)
        if not final:
            final = "报告生成超限，未能完成。"

    # 留档总线：评测器 / 冒烟测试批量消费（live 进程只设了队列，此处自然跳过）
    bus = subagent_events.get()
    if bus is not None:
        bus.extend(events)

    rep = store.current_report.get() or {}
    report_info = None
    if rep.get("id") and rep.get("summary_md"):
        report_info = {"report_id": rep["id"], "title": rep.get("title") or rep.get("question", ""),
                       "url": f"/api/report/{rep['id']}",
                       "excel_url": f"/api/report/{rep['id']}/excel" if rep.get("excel") else "",
                       "charts": len(rep["charts"]), "insights": len(rep["insights"])}

    return {
        "ok": bool(final),
        "type": "subagent_report",
        "agent": "viz",
        "task": task,
        "final": final,
        "report": report_info,
        "usage": usage,
    }


def make_delegate_viz(base_url: str, api_key: str, model: str):
    """构造主 Agent 的报告委派工具。注册进 make_delegate_tools 即接入主图。"""
    viz_graph = build_viz_agent(base_url, api_key, model)

    @tool
    async def delegate_viz(task: str, handle: str) -> str:
        """把报告生成任务交给报告 Agent（viz）。它能：凭句柄读取结果集、
        计算确定性洞察（趋势/环比/异常/集中度）、生成图表、导出 Excel、
        组装可离线打开的单文件 HTML 分析报告。
        task 传报告主题（如：近 6 个月 GMV 趋势报告）；
        handle 必须传 delegate_sql 返回的结果集句柄（没有句柄请先 delegate_sql）。
        返回 JSON 报告：final 为结论，report 含报告链接 report.url。"""
        from ..report import store

        rep = store.register_report(question=task)
        token = store.current_report.set(rep)
        try:
            report = await run_viz_agent(viz_graph, task, handle)
            # 产物资产化：报告登记进 data/artifacts.db（会话绑定，重启不丢入口），
            # artifact dict 附进 report_info，SSE 层据此下发 artifact 事件。
            # 登记失败不影响报告本身——报告文件已落盘，仍可经 /api/report 访问
            info = report.get("report")
            if isinstance(info, dict) and info.get("report_id"):
                try:
                    from ..artifacts import register_artifact
                    from ..tools.scratch import current_session

                    art = register_artifact(
                        session_id=current_session.get(),
                        kind="report",
                        title=info.get("title") or task,
                        url=info.get("url", ""),
                        excel_url=info.get("excel_url", ""),
                        meta={"task": task, "charts": info.get("charts", 0),
                              "insights": info.get("insights", 0)},
                    )
                    info["artifact"] = art
                except Exception:
                    pass
        except Exception as e:
            err = {"type": "error", "message": f"报告子 Agent 执行失败：{e}"}
            bus = subagent_events.get()
            if bus is not None:
                bus.append(err)
            report = {"ok": False, "type": "subagent_report", "agent": "viz", "task": task,
                      "final": "", "report": None, "usage": {}}
        finally:
            store.current_report.reset(token)
        return json.dumps(report, ensure_ascii=False)

    return [delegate_viz]
