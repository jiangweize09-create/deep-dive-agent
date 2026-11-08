"""FastAPI 入口：SSE 流式聊天 + 静态前端。

事件协议（demo 与 live 模式统一）：
  {"type":"thinking","text":...}                 LLM 中间推理（一句话）
  {"type":"delta","text":...}                    token 级流式增量（仅 live；主 Agent 生成中，前端打字机渲染）
  {"type":"reasoning","text":...,"agent":...}    模型思考链增量（reasoning_content 透传，前端「深度思考」折叠区）
  {"type":"sub_delta","agent":...,"text":...}    子 Agent token 直播（仅 live；实时思考行，不持久化）
  {"type":"step","tool":...,"args":{...}}        工具调用开始
  {"type":"result","tool":...,"content":{...}}   工具结构化返回
  {"type":"clarify","term":...,"question":...,"options":[...]}   口径澄清反问（HITL）
  {"type":"plan","term":...,"steps":[...],"note":...}            执行计划审批（HITL：复合任务先出计划）
  {"type":"artifact","id":...,"title":...,"url":...,"excel_url":...,"meta":{...}}  产物资产（报告/文件）登记，前端渲染产物卡
  {"type":"final","markdown":...}                最终回答
  {"type":"done","usage":{...},"ms":...}         结束 + 用量
  {"type":"error","message":...}                 错误
"""

import asyncio
import json
import time

import httpx
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .asr import asr_info, transcribe
from .config import settings
from .demo.traces import run_demo
from .agent.messages import _content_text, _parse_content  # 消息工具：与子 Agent 共用同一实现

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """会话记忆持久化：启动时把 AsyncSqliteSaver 注入主图（data/checkpoints.db）。

    MemorySaver 重启即失忆——网页上的聊天记录还在（localStorage），但后端
    已不记得之前聊过什么，多轮追问会失效。落库后重启可完整续上（含 HITL
    中断点）。未安装 langgraph-checkpoint-sqlite 或打开失败时回退内存记忆，
    服务照常可用（冒烟测试 / 评测器独立进程即走此回退）。
    """
    cm = None
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        path = settings.root / "data" / "checkpoints.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        cm = AsyncSqliteSaver.from_conn_string(str(path))
        saver = await cm.__aenter__()
        await saver.setup()
        from .agent.graph import set_checkpointer
        set_checkpointer(saver)
        print(f"[Deep Dive] 会话记忆持久化已启用 -> {path}")
    except Exception as e:
        cm = None   # 持久化不可用：回退 MemorySaver（重启失忆）。必须显式示警，
        # 静默降级曾让「README 宣称持久记忆」实际长期没生效而无人察觉
        print(f"[Deep Dive] ⚠ 持久化会话记忆不可用，已回退内存记忆（重启失忆）：{e}")
    yield
    if cm is not None:
        try:
            await cm.__aexit__(None, None, None)
        except Exception:
            pass


app = FastAPI(title=settings.app_name, lifespan=lifespan)


class NoCacheStaticFiles(StaticFiles):
    """开发期静态资源不缓存。

    浏览器默认对 /static/* 做协商缓存，改完前端 F5 常常还拿到旧文件，
    表现为「代码改了但界面没变」，非常难排查。这里强制 no-store，
    保证每次刷新都拿服务端最新文件（生产环境应换成带 hash 的资源名）。
    """

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp


app.mount("/static", NoCacheStaticFiles(directory=str(settings.frontend_dir)), name="static")


class ChatRequest(BaseModel):
    message: str
    session_id: str
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    # HITL 口径澄清的作答：{value: 指标名, label: 显示名, text: 自由说明}。
    # 与 message 二选一 —— 点澄清卡片选项时只带它；用户直接打字时只带 message。
    clarify_answer: dict | None = None
    # HITL 计划审批的作答：{action: approve|edit|cancel, text: 修改说明}。
    # 与 clarify_answer 同理二选一；计划待批时用户直接打字视为修改意见。
    plan_answer: dict | None = None
    # 工具白名单：SQL 子 Agent 只装配勾选的工具（run_sql/validate_sql 内部锁定）。
    # 顺序敏感，前端「工具」页每次变更会重发；None 表示全开。
    skills: list[str] | None = None
    # 聊天框上传的附件上下文：{attachments: [{kind: data|doc, table/file/name/rows}]}。
    # 编译成系统提示注入主 Agent（uploads.attachment_note），文件内容不进上下文
    context: dict | None = None
    # 重新生成：同一问题重问。后端注入一条「换角度重新组织回答」的系统提示
    regenerate: bool = False


@app.get("/api/health")
async def health():
    from .db import db_ready, query
    from .semantic.loader import get_catalog

    payload = {"name": settings.app_name, "db_ready": db_ready()}
    if payload["db_ready"]:
        _, rows = query("SELECT COUNT(*) FROM orders")
        _, span = query(
            "SELECT strftime(MIN(created_at), '%Y-%m'), strftime(MAX(created_at), '%Y-%m') FROM orders"
        )
        payload["demo"] = {
            "tables": len(get_catalog().tables),
            "metrics": len(get_catalog().metrics),
            "orders": rows[0][0],
            "months": f"{span[0][0]} ~ {span[0][1]}",
        }
    live = bool(settings.env_base_url and settings.env_api_key and settings.env_model)
    payload["live_env"] = live
    payload["live_model"] = settings.env_model if live else ""
    payload["asr"] = asr_info()
    return payload


@app.get("/")
async def index():
    """返回 React 构建产物；未构建时给出明确的构建指引。"""
    dist = settings.frontend_dir / "dist" / "index.html"
    if not dist.exists():
        return JSONResponse(
            status_code=503,
            content={"detail": "前端未构建：请先执行 cd web && npm install && npm run build"},
        )
    return FileResponse(
        str(dist),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


class LlmTestRequest(BaseModel):
    """前端 LLM 配置卡片的连通性测试请求；留空字段回退 .env。"""

    base_url: str = ""
    api_key: str = ""
    model: str = ""


@app.post("/api/llm/test")
async def llm_test(req: LlmTestRequest):
    """用给定凭证发一次最小补全（max_tokens=8），返回时延或错误原因。

    只做连通性验证，不进入 Agent 链路；Key 不落盘、不记日志。
    """
    base = (req.base_url or settings.env_base_url).rstrip("/")
    key = req.api_key or settings.env_api_key
    model = req.model or settings.env_model
    if not (base and model):
        return {"ok": False, "error": "缺少 Base URL 或模型名（.env 也未配置）"}

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}"} if key else {},
                json={"model": model,
                      "messages": [{"role": "user", "content": "ping"}],
                      "max_tokens": 8},
            )
        ms = int((time.perf_counter() - t0) * 1000)
        if r.status_code >= 400:
            return {"ok": False, "latency_ms": ms,
                    "error": f"HTTP {r.status_code}: {(r.text or '')[:160]}"}
        data = r.json()
        reply = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        return {"ok": True, "latency_ms": ms, "reply": str(reply)[:40]}
    except Exception as e:  # 网络错误 / 超时 / 非 JSON 响应
        return {"ok": False, "error": str(e)[:200]}


def _sse(obj) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# 工具调用计数：SSE 转发处是所有执行模式（demo/live）的必经点，在这里计数
# 拿到的是真实执行流量。进程内存计数，重启归零 —— 只做轻量观测，不上数据库。
from collections import Counter

_TOOL_CALLS: Counter = Counter()


def _track_tool(ev) -> None:
    if isinstance(ev, dict) and ev.get("type") == "step" and ev.get("tool"):
        _TOOL_CALLS[ev["tool"]] += 1


def _is_subagent_report(c) -> bool:
    return isinstance(c, dict) and c.get("type") == "subagent_report"


@app.post("/api/chat")
async def chat(req: ChatRequest):
    async def gen():
        from .usage import record_usage

        started = time.time()
        tools_seen: list[str] = []
        base_url = (req.base_url or settings.env_base_url or "").strip()
        api_key = (req.api_key or settings.env_api_key or "").strip()
        model = (req.model or settings.env_model or "").strip()

        # 句柄协议的会话上下文：persist_result / fetch_data 用它做会话指纹校验；
        # 请求结束时精确清理本次创建的临时表（报告/Excel 已在请求内落盘，不依赖它们）
        from .tools.scratch import current_session, discard_created
        _created: list = []
        tok_sess = current_session.set(req.session_id or "adhoc")
        tok_created = _set_created_list(_created)
        try:
            if api_key and base_url and model:
                async for ev in _run_live(req, base_url, api_key, model, started):
                    _track_tool(ev)
                    if ev.get("type") == "step" and ev.get("tool"):
                        tools_seen.append(ev["tool"])
                    if ev.get("type") == "done":
                        u = ev.get("usage") or {}
                        try:
                            await asyncio.to_thread(record_usage, req.session_id,
                                u.get("input_tokens") or 0, u.get("output_tokens") or 0,
                                u.get("total_tokens") or 0, u.get("steps") or 0,
                                int(ev.get("ms") or 0), tools_seen)
                        except Exception:
                            pass    # 统计失败不影响问答
                    yield _sse(ev)
            else:
                async for ev in run_demo(req.message):
                    if ev.get("type") == "done":
                        ev = {**ev, "ms": int((time.time() - started) * 1000)}
                    _track_tool(ev)
                    yield _sse(ev)
        except Exception as e:  # 兜底：任何异常都以事件形式返回，不挂连接
            # 带上实际使用的端点/模型（key 不外露）：供应商 403/401 之类一眼定位是哪个配置在报错
            msg = f"处理请求时出错：{e}"
            if base_url:
                msg += f"（端点 {base_url} · 模型 {model or '未指定'}）"
            yield _sse({"type": "error", "message": msg})
            yield _sse({"type": "done", "ms": int((time.time() - started) * 1000)})
        finally:
            await asyncio.to_thread(discard_created)
            current_session.reset(tok_sess)
            _reset_created_list(tok_created)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _set_created_list(lst: list):
    from .tools.scratch import _created_this_request
    return _created_this_request.set(lst)


def _reset_created_list(tok):
    from .tools.scratch import _created_this_request
    _created_this_request.reset(tok)


_REPORT_ID_RE = None


def _valid_report_id(rid: str) -> bool:
    """报告 id 只允许 rep_ + 8 位 hex，杜绝路径穿越。"""
    global _REPORT_ID_RE
    if _REPORT_ID_RE is None:
        import re
        _REPORT_ID_RE = re.compile(r"^rep_[0-9a-f]{8}$")
    return bool(_REPORT_ID_RE.match(rid or ""))


@app.get("/api/report/{rid}")
async def report_html(rid: str):
    """打开单文件 HTML 分析报告（文件自包含，服务重启后仍可访问）。"""
    if not _valid_report_id(rid):
        raise HTTPException(status_code=404, detail="报告不存在")
    path = settings.reports_dir / f"{rid}.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="报告不存在或已过期")
    return FileResponse(str(path), media_type="text/html",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/report/{rid}/excel")
async def report_excel(rid: str):
    """下载报告附带的结果集 Excel。"""
    if not _valid_report_id(rid):
        raise HTTPException(status_code=404, detail="文件不存在")
    rep_dir = settings.reports_dir
    if not rep_dir.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    # excel 文件名前缀即报告 id，按前缀匹配（导出时可能带自定义名）
    hits = sorted(rep_dir.glob(f"{rid}_*.xlsx") or [])
    if not hits:
        hits = sorted(rep_dir.glob(f"{rid}*.xlsx"))
    if not hits:
        raise HTTPException(status_code=404, detail="Excel 不存在或未导出")
    path = hits[-1]
    return FileResponse(str(path), media_type=(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        filename=path.name)


@app.delete("/api/report/{rid}")
async def report_delete(rid: str):
    """删除一份历史报告：HTML 文件 + 附带 Excel + 产物登记记录 三同步。

    报告是可再生的产物（重新问一次就有），不该永久堆积在磁盘上。
    """
    if not _valid_report_id(rid):
        raise HTTPException(status_code=404, detail="报告不存在")
    rep_dir = settings.reports_dir
    files = 0
    if rep_dir.exists():
        html = rep_dir / f"{rid}.html"
        if html.exists():
            html.unlink()
            files += 1
        for f in rep_dir.glob(f"{rid}*.xlsx"):
            f.unlink(missing_ok=True)
            files += 1
    from .artifacts import delete_by_report

    removed = delete_by_report(rid)
    return {"ok": True, "files": files, "artifacts": removed}


# ---------- SQL 透明模式：手改 SQL 重跑（与 AI 完全同护栏） ----------

class ManualQueryRequest(BaseModel):
    sql: str


@app.post("/api/query")
async def manual_query(req: ManualQueryRequest):
    """透明模式端点：用户手改 SQL 后重跑。

    与 AI 的 run_sql 走**同一套代码路径**（validate→run_sql_core）：
    AST 校验、来源白名单、PII 拦截、强制 LIMIT 全部生效——透明不等于绕过护栏。
    结果只返回给用户展示，不进入 AI 上下文（避免污染对话）。
    """
    from .tools.sql import run_sql_core

    return run_sql_core(req.sql)


# ---------- 使用统计（设置 → 使用统计） ----------

@app.get("/api/usage")
async def usage_stats(days: int = Query(14, ge=1, le=90)):
    """近 N 天使用统计：逐日趋势 + 总量 + 工具排行（确定性聚合）。"""
    from .usage import summary

    return summary(days)


@app.get("/api/profile/{source}")
async def profile_source(source: str):
    """数据健康面板：指定数据源的逐表质量画像（只读统计，确定性计算）。

    source=builtin → 内置演示库全部表；up_xxx → 对应上传表。
    """
    import re as _re

    from .tools.profile import profile_table_core

    if source == "builtin":
        from .semantic.loader import get_catalog

        tables = [n for n in get_catalog().tables if not n.startswith("up_")]
        return {"ok": True, "source": "builtin",
                "profiles": [profile_table_core(t) for t in tables]}
    if not _re.fullmatch(r"up_[a-z0-9_]{1,39}", source):
        raise HTTPException(status_code=400, detail="非法数据源")
    return {"ok": True, "source": source, "profiles": [profile_table_core(source)]}


@app.post("/api/asr")
async def asr(audio: UploadFile = File(...)):
    """语音输入：上传浏览器录音片段，返回识别文字。

    与 /api/chat 一样遵循「不抛异常」约定：业务失败走 HTTP 200 + {ok:false, code, message}，
    只有请求本身不合法（没带文件）才用 422。
    """
    data = await audio.read()
    # transcribe 内部是同步 httpx（最长 60s），丢线程池执行，避免卡住事件循环
    result = await asyncio.to_thread(
        transcribe, data, audio.filename or "recording.webm", audio.content_type or ""
    )
    if result["ok"]:
        return result
    status = result.pop("status", 400)
    # 未配置属于环境问题，用 503 让前端能明确区分「没配key」和「识别失败」。
    # 注意：不能写成 `raise HTTPException(...) if status >= 500 else result` ——
    # 条件表达式整体是 raise 的操作数，status<500（限流/音频过大/格式不支持等）
    # 会变成 raise dict -> TypeError -> 500，破坏 {ok:false, code} 结构化约定。
    if status >= 500:
        raise HTTPException(status_code=status, detail=result)
    return result


async def _run_live(req: ChatRequest, base_url: str, api_key: str, model: str, started: float):
    from langgraph.errors import GraphRecursionError
    from langgraph.types import Command

    from .agent.events import subagent_queue
    from .agent.graph import get_graph, make_input
    from .agent.llm import chunk_reasoning
    from .agent.messages import _chunk_text

    graph = get_graph(base_url, api_key, model, req.skills)
    config = {"configurable": {"thread_id": req.session_id}, "recursion_limit": 40}
    usage = {"steps": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    # HITL 恢复：线程停在 clarify 中断点时，打字内容视为自由作答，
    # 点选项则用 clarify_answer 续跑；停在 plan 审批（tools 节点内的
    # propose_plan interrupt）同理 —— approve/edit/cancel 或打字当修改意见。
    # 必须用异步 aget_state：AsyncSqliteSaver 不支持同步 API，
    # 同步 get_state 恒抛异常会让 pending 恒为空、HITL 恢复分支全部失效
    try:
        state_snap = await graph.aget_state(config)
        pending = tuple(state_snap.next)
    except Exception:
        pending = ()
    if "clarify" in pending:
        answer = req.clarify_answer if req.clarify_answer is not None else {
            "value": None, "label": None, "text": (req.message or "").strip()}
        inputs = Command(resume=answer)
    elif "tools" in pending:
        # tools 节点内只会因 propose_plan 的 interrupt 而停驻（普通工具执行不停）
        if req.plan_answer is not None:
            answer = req.plan_answer
        else:
            text = (req.message or "").strip()
            answer = {"action": "edit", "text": text} if text else {"action": "approve"}
        inputs = Command(resume=answer)
    elif req.message:
        from .memory import note_for_prompt
        from .uploads import attachment_note
        atts = ((req.context or {}).get("attachments")) or []
        extra = ("（用户对上一次回答不满意，要求重新生成本题答案：请换一个更清晰的角度重新组织回答；"
                 "已确认的口径与数据范围沿用，不要再次反问）") if req.regenerate else ""
        inputs = make_input(req.message, attachment_note(atts), note_for_prompt(), extra)
    else:
        inputs = Command(resume=req.clarify_answer or {})

    # 快车道：子 Agent 过程事件实时入队，本循环与图更新双路监听、先到先转发 ——
    # 委派期间不再是"结尾整段回放"。旧的列表总线（subagent_events）保留给
    # 评测器/冒烟测试：live 只设队列，子 Agent 收尾的 bus.extend 自然跳过，不重复下发
    queue: asyncio.Queue = asyncio.Queue()
    token = subagent_queue.set(queue)
    graph_task: asyncio.Task | None = None
    q_task: asyncio.Task | None = None
    try:
        stream = graph.astream(inputs, config=config, stream_mode=["updates", "messages"])

        async def pull():
            return await stream.__anext__()

        graph_task = asyncio.create_task(pull())
        q_task = asyncio.create_task(queue.get())

        def take_ready() -> list[dict]:
            """非阻塞取走快车道上已就绪的事件（含监听任务手里已完成的那个）。"""
            nonlocal q_task
            out: list[dict] = []
            if q_task.done() and not q_task.cancelled():
                try:
                    out.append(q_task.result())
                except Exception:
                    pass
                else:
                    q_task = asyncio.create_task(queue.get())
            while True:
                try:
                    out.append(queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            return out

        while True:
            done, _ = await asyncio.wait({graph_task, q_task}, return_when=asyncio.FIRST_COMPLETED)
            # 先放行快车道已就绪的事件，再处理图事件：委派收尾时顺序才不会乱
            if q_task in done:
                try:
                    yield q_task.result()
                except Exception:
                    pass
                q_task = asyncio.create_task(queue.get())
            if graph_task in done:
                try:
                    item = graph_task.result()
                except StopAsyncIteration:
                    break
                graph_task = asyncio.create_task(pull())
                if isinstance(item, tuple) and len(item) == 2 and item[0] in ("updates", "messages"):
                    mode, chunk = item
                else:
                    mode, chunk = "updates", item
                if mode == "messages":
                    # 主 Agent token 流。子 Agent 的 LLM 调用不挂在外层回调上
                    # （它们跑自己的 astream），这里天然只有主 Agent 的增量。
                    # reasoning：模型内部推理草稿（深度思考），先于正文流出
                    msg_chunk, meta = chunk
                    if isinstance(meta, dict) and meta.get("langgraph_node") == "agent":
                        r = chunk_reasoning(msg_chunk)
                        if r:
                            yield {"type": "reasoning", "text": r}
                        text = _chunk_text(msg_chunk)
                        if text:
                            yield {"type": "delta", "text": text}
                    continue
                if "__interrupt__" in chunk:
                    # HITL 中断：clarify 节点反问 / tools 节点里的 propose_plan 计划审批。
                    # 前端渲染对应卡片；本轮到此结束（无 final），等用户作答 resume
                    for intr in chunk["__interrupt__"]:
                        payload = getattr(intr, "value", None)
                        if isinstance(payload, dict):
                            if payload.get("kind") == "plan":
                                yield {"type": "plan", **payload}
                            else:
                                yield {"type": "clarify", **payload}
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
                                yield {"type": "thinking", "text": text}
                            for tc in tcs:
                                yield {"type": "step", "tool": tc.get("name", ""), "args": tc.get("args", {})}
                            if text and not tcs:
                                yield {"type": "final", "markdown": text}
                            um = getattr(m, "usage_metadata", None)
                            if um:
                                usage["input_tokens"] += um.get("input_tokens") or 0
                                usage["output_tokens"] += um.get("output_tokens") or 0
                                usage["total_tokens"] += um.get("total_tokens") or 0
                        elif node == "tools":
                            name = getattr(m, "name", "")
                            content = _parse_content(getattr(m, "content", ""))
                            # 多 Agent：委派工具报告只含结论/用量；过程事件已在快车道
                            # 实时下发，这里先放行残留再补发委派回执（spinner 停止信号）
                            if _is_subagent_report(content):
                                sub_usage = content.get("usage") or {}
                                usage["steps"] += sub_usage.get("steps") or 0
                                usage["input_tokens"] += sub_usage.get("input_tokens") or 0
                                usage["output_tokens"] += sub_usage.get("output_tokens") or 0
                                usage["total_tokens"] += sub_usage.get("total_tokens") or 0
                                for ev in take_ready():
                                    yield ev
                                # 补发委派工具自身的 result：tool 用消息上的真实工具名
                                # （rag_retrieve 是主 Agent 直连普通工具，走 else 分支不过这里）
                                yield {"type": "result", "tool": name,
                                       "content": {"ok": bool(content.get("ok")),
                                                   "final": content.get("final", "")}}
                                # 产物资产：报告已登记进 artifacts.db，下发结构化
                                # 事件让前端渲染产物卡（不再只靠正文里的 Markdown 链接）
                                rep_info = content.get("report")
                                if isinstance(rep_info, dict) and rep_info.get("artifact"):
                                    yield {"type": "artifact", **rep_info["artifact"]}
                            else:
                                yield {"type": "result", "tool": name, "content": content}
        # 流结束：放行快车道残留的事件后收尾
        for ev in take_ready():
            yield ev
        yield {"type": "done", "usage": usage, "ms": int((time.time() - started) * 1000)}
    except GraphRecursionError:
        yield {"type": "error", "message": "已达最大轮次，本轮终止。请尝试缩小问题范围。"}
        yield {"type": "done", "usage": usage, "ms": int((time.time() - started) * 1000)}
    finally:
        # 双路监听收尾：监听任务取消（图迭代任务不能留悬），快车道必须复位
        # （asyncio 任务复用时，残留 token 会把事件串进下一个请求）
        for t in (graph_task, q_task):
            if t is not None and not t.done():
                t.cancel()
        subagent_queue.reset(token)


@app.get("/api/dashboard")
async def dashboard(
    range_key: str = Query("6m", alias="range",
                           pattern="^(1m|2m|3m|6m|12m|24m|36m|custom)$"),
    source: str | None = Query(None, pattern="^up_[a-z0-9_]{1,39}$"),
    start: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    """看板数据：4 个 KPI + 图表，一次算完。

    与 /api/chat 的关键差异：这里的 SQL 是 app/dashboard.py 里的固定常量
    （上传表则按用户标注的列角色参数化），不经过 LLM，
    因此秒级返回、零 token、结果可复现。口径仍取自语义层。
    range=custom 时用 start/end 自定义起止日期（止日含当天）。
    """
    from .dashboard import build_dashboard

    try:
        return build_dashboard(range_key, source, start, end)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/dashboard/spec/{table}")
async def dashboard_spec(table: str):
    """AI 生成看板 spec（LLM 规划一次，确定性渲染）。

    只在上传表入库后由用户显式触发：LLM 读 schema + 样本输出 KPI/图表规划，
    经白名单校验写回注册表；之后每次打开看板走确定性 SQL，不再调 LLM。
    遵循「不抛异常」约定：业务失败返回 {ok:false, error}，让前端能展示原因。
    """
    import re as _re

    if not _re.fullmatch(r"up_[a-z0-9_]{1,39}", table or ""):
        raise HTTPException(status_code=400, detail="非法表名")
    from .dash_spec import generate_dashboard_spec

    try:
        return await generate_dashboard_spec(table)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"生成失败：{e}"}


_REPORT_TOOLS = [
    {"name": "fetch_data", "label": "取数与洞察",
     "desc": "凭句柄读结果集，并用确定性统计算趋势 / 环比 / z-score 异常 / 集中度", "locked": True, "params": ["handle"]},
    {"name": "render_chart", "label": "报告图表",
     "desc": "凭句柄生成 ECharts 图表，前端内联渲染并写进报告文件", "locked": True, "params": ["handle", "chart_type", "x", "y"]},
    {"name": "export_excel", "label": "Excel 导出",
     "desc": "句柄结果集导出 xlsx（openpyxl），报告页出现下载按钮", "locked": True, "params": ["handle"]},
    {"name": "build_report", "label": "报告组装",
     "desc": "组装单文件 HTML 报告，echarts 内嵌，可离线打开", "locked": True, "params": ["title", "summary_md", "handle"]},
]


@app.get("/api/tools")
async def tools_catalog():
    """工具清单：SQL 子 Agent 的 8 个（可开关）+ viz 子 Agent 的 4 个（只读）。

    这份清单就是 Agent 实际装配的来源，不是前端自说自话的展示。
    """
    from .tools import LOCKED, tool_specs

    return {
        "groups": [
            {"key": "sql", "name": "数据工具",
             "desc": "SQL 子 Agent 持有 · ReAct 循环内调用 · 可开关",
             "tools": tool_specs()},
            {"key": "viz", "name": "报告工具",
             "desc": "viz 子 Agent 持有 · 凭句柄工作 · 不可关闭",
             "tools": _REPORT_TOOLS},
        ],
        "locked": sorted(LOCKED),
        # 真实调用计数（SSE 转发处累计），前端工具卡片展示「调用 N」
        "calls": dict(_TOOL_CALLS),
    }


@app.get("/api/artifacts")
async def artifacts_list(session_id: str | None = Query(None)):
    """产物资产清单：传 session_id 查单会话，不传查全部。

    服务端持久记录——聊天记录在前端 localStorage，清掉/换浏览器后报告入口
    仍可从这里回查（配合 /api/report/{rid} 文件始终可访问）。
    """
    from .artifacts import list_artifacts

    return {"artifacts": list_artifacts(session_id)}


# ---------- 用户偏好记忆（设置页「记忆」） ----------

class MemoryUpsertRequest(BaseModel):
    category: str   # caliber | workflow | profile
    key: str
    value: str


class MemoryValueRequest(BaseModel):
    value: str


@app.get("/api/memory")
async def memory_list():
    """全部偏好记忆 + 总开关状态（设置页「个性化」的数据源）。"""
    from .memory import is_enabled, list_prefs

    return {"enabled": is_enabled(), "prefs": list_prefs()}


class MemoryEnabledRequest(BaseModel):
    enabled: bool


@app.post("/api/memory/enabled")
async def memory_enabled(req: MemoryEnabledRequest):
    """个性化记忆总开关：关闭后不注入偏好、口径恢复每次反问、save_memory 停用。"""
    from .memory import set_enabled

    set_enabled(req.enabled)
    return {"ok": True, "enabled": req.enabled}


@app.post("/api/memory")
async def memory_add(req: MemoryUpsertRequest):
    """新增/覆盖一条偏好（前端手动添加 / 导入文件走同一入口）。"""
    from .memory import save_pref

    try:
        return {"ok": True, "pref": save_pref(req.category, req.key, req.value, source="manual")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/memory/{pid}")
async def memory_edit(pid: int, req: MemoryValueRequest):
    """编辑一条偏好的内容（来源改标为 manual）。"""
    from .memory import update_pref

    if not update_pref(pid, req.value):
        raise HTTPException(status_code=404, detail="记忆条目不存在")
    return {"ok": True}


@app.delete("/api/memory/{pid}")
async def memory_delete(pid: int):
    from .memory import delete_pref

    if not delete_pref(pid):
        raise HTTPException(status_code=404, detail="记忆条目不存在")
    return {"ok": True}


@app.post("/api/memory/reset")
async def memory_reset():
    """一键重置：清空全部偏好记忆。"""
    from .memory import clear

    return {"ok": True, "cleared": clear()}


@app.get("/api/workspace")
async def workspace():
    """工作空间：我上传的表 / 我的文档 / 历史报告。"""
    from .workspace import build_workspace

    return build_workspace()


@app.get("/api/datasources")
async def datasources():
    """已接入的数据资产：内置 DuckDB 表 + 上传表。"""
    from .datasources import list_sources

    return list_sources()


class UploadConfirmRequest(BaseModel):
    draft_id: str
    table: str | None = None
    description: str | None = None
    # 看板列角色标注（HITL）：{time, amount, dims}，均为主流列名的白名单校验
    roles: dict | None = None


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """聊天框真上传（原「上下文文件」为纯前端占位，本接口补齐后端）。

    csv/xlsx 走数据路线（返回语义层草稿，等 /api/upload/confirm 确认入库）；
    md/txt/json/docx/pptx/pdf 走文档路线（提取文本入 RAG，立即可检索）。
    """
    from pathlib import Path

    from .uploads import DATA_EXTS, DOC_EXTS, ingest_data_upload, ingest_doc_upload

    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    data = await file.read()
    if ext in DATA_EXTS:
        r = ingest_data_upload(filename, data)
    elif ext in DOC_EXTS:
        r = ingest_doc_upload(filename, data)
    else:
        r = {"ok": False,
             "reason": f"不支持的格式 {ext or '(无后缀)'}——数据：csv/xlsx；文档：md/txt/json/docx/pptx/pdf"}
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r)
    return r


@app.post("/api/upload/confirm")
async def upload_confirm(req: UploadConfirmRequest, background: BackgroundTasks):
    """确认入库：草稿 -> parquet + TEMP 视图 + 语义层注册（Agent 立即可查）。

    数据表入库成功后自动派发 Smart Dashboard 生成（后台任务，不阻塞响应）：
    LLM 规划 spec 只在新数据入库这一次发生，失败静默——数据页的
    Smart Dashboard 按钮可随时手动重试。
    """
    from .uploads import confirm_upload

    r = confirm_upload(req.draft_id, req.table, req.description, req.roles)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r)
    if r.get("kind") == "data" and r.get("table"):
        table = r["table"]

        async def _auto_spec() -> None:
            from .dash_spec import generate_dashboard_spec

            try:
                await generate_dashboard_spec(table)
            except Exception:  # noqa: BLE001  后台任务静默：失败可手动点按钮重试
                pass

        background.add_task(_auto_spec)
        r["spec_pending"] = True
    return r


@app.delete("/api/upload/{name}")
async def upload_delete(name: str):
    from .uploads import remove_upload

    r = remove_upload(name)
    if not r.get("ok"):
        raise HTTPException(status_code=404, detail=r)
    return r


