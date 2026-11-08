"""LangGraph 主图：多 Agent 调度者（supervisor）。

架构（子图即工具）：
- 主 Agent（本文件）：只绑 delegate_sql 等委派工具。上下文里没有 6 个数据工具
  的 schema —— 工具隔离，选错率与 token 都下降。会话记忆（checkpointer）在主图。
- clarify 节点：口径澄清 HITL 入口 —— 歧义指标 interrupt 反问用户，
  resume 后把确认口径注回上下文（详见 clarify.py）。
- propose_plan 工具：计划审批 HITL —— 复合分析任务由主 Agent 先提交计划，
  用户批准/修改后才开委派（interrupt 发生在 tools 节点内，resume 语义与
  clarify 相同；必须单独调用，避免同轮其他工具在恢复时重复执行副作用）。
- SQL 子 Agent（sql_agent.py）：独立 ReAct 循环 + 全部数据工具 + 护栏。

演进方向：接 MCP / Insight 时在 sql_agent.make_delegate_tools 里追加委派工具即可，
主图不用动。

兼容性：get_graph / make_input 签名不变，eval 与 demo 不受影响。
"""

import json
from typing import Annotated, TypedDict
import hashlib

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

try:
    from langgraph.checkpoint.memory import MemorySaver
except ImportError:  # 兼容新版本改名
    from langgraph.checkpoint.memory import InMemorySaver as MemorySaver

from .clarify import (build_confirm_message, clarified_terms, detect_ambiguity,
                      detect_source_ambiguity, record_caliber_choice)
from ..memory import make_save_memory_tool, pref_keys
from .prompts import ROUTER_PROMPT, WRAP_UP_PROMPT
from .sql_agent import make_delegate_tools

MAX_DELEGATES = 3   # 单轮最多委派次数（对应 ROUTER_PROMPT 第 6 条纪律）

_PLAN_TOOLS = {"delegate_sql", "delegate_viz", "rag_retrieve", "generate_dashboard"}


def _make_propose_plan():
    """计划审批 HITL 工具：主 Agent 提交执行计划，用户批准后才开委派。

    为什么是工具而不是独立节点：计划由调度者生成最自然（它知道自己有哪些
    委派工具、上限几次），简单问题不调用 = 零额外往返；独立节点则要为每条
    消息先跑一次「要不要计划」的判断。interrupt 发生在 tools 节点内，
    resume 语义与 clarify 相同（Command(resume=answer) 从工具开头重放，
    interrupt() 处返回用户答案）——因此本工具必须单独调用，避免同轮其他
    工具在恢复时被重复执行。
    """

    @tool
    async def propose_plan(steps: list[dict], note: str = "",
                           config: RunnableConfig = None) -> str:
        """提交复合分析任务的执行计划，等用户批准后再开始委派（必须单独调用）。
        steps 按执行顺序排列，每项 {"tool": "delegate_sql"|"delegate_viz"|"rag_retrieve"|"generate_dashboard",
        "task": "这一步做什么（一句话）"}，最多 3 步；note 可选，说明计划依据（口径/时间范围）。
        只在需要 2 次及以上委派的复合任务（报告/多维归因/对比+趋势）时调用；
        单指标、寒暄、单一查询直接委派，禁止提交计划。
        返回 approved=true 时按计划委派；cancelled=true 时不要继续委派。"""
        # 计划形状收紧为白名单：多余字段剔除、非法委派工具剔除、步数封顶
        clean = []
        for s in (steps or [])[:MAX_DELEGATES]:
            if not isinstance(s, dict):
                continue
            t = str(s.get("tool") or "").strip()
            task = str(s.get("task") or "").strip()
            if t in _PLAN_TOOLS and task:
                clean.append({"tool": t, "task": task[:200]})
        if not clean:
            return json.dumps({"ok": False,
                               "reason": "计划为空：steps 每项需要 tool（delegate_sql/delegate_viz/rag_retrieve/generate_dashboard）与 task",
                               "suggestion": "按实际分析步骤重写 steps，或（简单任务）不提交计划直接委派"},
                              ensure_ascii=False)
        payload = {"kind": "plan", "term": "执行计划",
                   "question": "这是个多步分析任务，执行前请确认计划：",
                   "steps": clean, "note": (note or "")[:200]}
        if (config or {}).get("configurable", {}).get("hitl") == "off":
            return json.dumps({"ok": True, "approved": True, "auto": True,
                               "plan": clean,
                               "message": "无人值守模式，计划自动批准，直接按计划委派"},
                              ensure_ascii=False)
        answer = interrupt(payload)
        answer = answer if isinstance(answer, dict) else {}
        action = str(answer.get("action") or "").strip()
        text = str(answer.get("text") or "").strip()
        if action == "cancel":
            return json.dumps({"ok": True, "approved": False, "cancelled": True,
                               "message": "用户取消了该计划。请用一两句话回应，并询问用户想如何调整分析方向。"},
                              ensure_ascii=False)
        if action == "edit" and text:
            return json.dumps({"ok": True, "approved": True, "modified": True,
                               "plan": clean,
                               "message": f"用户修改了计划，请严格按以下要求执行：{text[:300]}"},
                              ensure_ascii=False)
        return json.dumps({"ok": True, "approved": True, "plan": clean,
                           "message": "用户已批准计划，请按计划顺序委派，不要偏离步骤。"},
                          ensure_ascii=False)

    return propose_plan


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int
    # 本轮消息附带的文件上下文（由 make_input 写入，随 checkpoint 持久）：
    # 只注入「有哪些文件 + 怎么读」的指引，不注入文件内容本身
    attachment_note: str
    # 用户偏好记忆（memory.note_for_prompt 每轮重读，跨会话生效；可空）
    memory_note: str
    # 本轮附加指令（如「重新生成」提示），仅本轮生效
    extra_note: str


_memory_checkpointer = MemorySaver()
_custom_checkpointer = None   # main.py lifespan 启动时注入（AsyncSqliteSaver，会话记忆持久化）


def set_checkpointer(saver) -> None:
    """服务启动时注入持久化 checkpointer；不注入则全程内存记忆（重启即失）。"""
    global _custom_checkpointer
    _custom_checkpointer = saver


def get_checkpointer():
    return _custom_checkpointer or _memory_checkpointer


_graph_cache: dict = {}


def build_graph(base_url: str, api_key: str, model: str, allow: list[str] | None = None):
    from .llm import ReasoningChatOpenAI as ChatOpenAI

    llm = ChatOpenAI(base_url=base_url, api_key=api_key, model=model, temperature=0, timeout=120)
    # 控制面工具：propose_plan（计划审批 HITL）+ save_memory（长期偏好写入）
    tools = [_make_propose_plan(), make_save_memory_tool()] + \
        make_delegate_tools(base_url, api_key, model, allow)
    llm_tools = llm.bind_tools(tools)

    async def agent_node(state: AgentState):
        msgs = state["messages"]
        if not (msgs and getattr(msgs[0], "type", "") == "system"):
            msgs = [SystemMessage(content=ROUTER_PROMPT)] + list(msgs)
        # 附件上下文 + 用户偏好记忆：紧跟系统头注入（轻量指引，不占数据面上下文）
        note = (state.get("attachment_note") or "").strip()
        mem = (state.get("memory_note") or "").strip()
        extra = (state.get("extra_note") or "").strip()
        extras = [SystemMessage(content=x) for x in (note, mem, extra) if x]
        if extras:
            msgs = [msgs[0], *extras] + list(msgs[1:])
        steps = state.get("steps", 0)
        if steps >= MAX_DELEGATES + 1:
            resp = await llm.ainvoke(msgs + [SystemMessage(content=WRAP_UP_PROMPT)])
        else:
            resp = await llm_tools.ainvoke(msgs)
        return {"messages": [resp], "steps": steps + 1}

    async def clarify_node(state: AgentState, config):
        """口径澄清 HITL：歧义指标 interrupt 反问用户，resume 后把确认口径注回上下文。

        config.configurable.hitl == "off" 时直通（评测器等无人值守场景，
        golden set 里的「销售额」不能把评测卡死在反问上）。
        """
        if (config or {}).get("configurable", {}).get("hitl") == "off":
            return {}
        msgs = state["messages"]
        human = next((m for m in reversed(msgs) if getattr(m, "type", "") == "human"), None)
        if human is None:
            return {}
        text = getattr(human, "content", "")
        if not isinstance(text, str):
            text = str(text)
        # 已确认集合 = 本会话口径确认标记 + 跨会话长期偏好（记住过的不再问）
        already = clarified_terms(msgs) | pref_keys()
        amb = (detect_ambiguity(text, already=already)
               or detect_source_ambiguity(text, already=already))
        if not amb:
            return {}
        # 无 resume 时抛 GraphInterrupt：线程停在 clarify，状态由 checkpointer 持有；
        # 用户作答后 Command(resume=answer) 续跑，此处拿到 answer 而不是暂停
        answer = interrupt(amb)
        # 口径卡上的明确选择即长期偏好：落库，下次永久跳过该术语的反问
        record_caliber_choice(amb["term"], answer)
        return {"messages": [SystemMessage(content=build_confirm_message(amb["term"], answer))]}

    def route(state: AgentState):
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return END

    g = StateGraph(AgentState)
    g.add_node("clarify", clarify_node)
    g.add_node("agent", agent_node)
    g.add_node("tools", ToolNode(tools))
    g.add_edge(START, "clarify")
    g.add_edge("clarify", "agent")
    g.add_conditional_edges("agent", route, {"tools": "tools", END: END})
    g.add_edge("tools", "agent")
    return g.compile(checkpointer=get_checkpointer())


def get_graph(base_url: str, api_key: str, model: str, allow: list[str] | None = None):
    # key 只取哈希指纹，避免把长度/后缀泄漏进缓存键与日志
    fp = hashlib.sha256(api_key.encode()).hexdigest()[:8]
    # 工具白名单必须进缓存键：否则勾掉某个工具后仍命中旧图，开关看起来没生效
    allow_key = ",".join(sorted(allow)) if allow else "*"
    key = f"{base_url}|{model}|{fp}|{allow_key}"
    if key not in _graph_cache:
        _graph_cache[key] = build_graph(base_url, api_key, model, allow)
    return _graph_cache[key]


def make_input(message: str, attachment_note: str = "", memory_note: str = "",
               extra_note: str = "") -> dict:
    return {"messages": [HumanMessage(content=message)], "steps": 0,
            "attachment_note": attachment_note or "", "memory_note": memory_note or "",
            "extra_note": extra_note or ""}
