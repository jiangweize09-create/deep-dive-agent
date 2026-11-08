import { useCallback, useEffect, useRef, useState } from "react";
import { readSSE } from "../api/sse";
import type { Msg, Session, Usage } from "../types";

const LS_SESSIONS = "xihuang_sessions_v1";
const LS_TOOLS = "xihuang_tools_v2";

function loadSessions(): Session[] {
  try {
    const raw = JSON.parse(localStorage.getItem(LS_SESSIONS) || "[]");
    if (!Array.isArray(raw)) return [];
    // streaming / liveThink 是瞬态字段：中途关页会残留半截文本，载入时一律清掉
    return raw.map((s: Session) => ({
      ...s,
      messages: (s.messages || []).map((m) => ({ ...m, streaming: "", liveThink: "" })),
    }));
  } catch {
    return [];
  }
}

/** 工具白名单：null = 全开（与后端 get_tools 语义一致） */
function loadSkills(): string[] | null {
  try {
    const v = JSON.parse(localStorage.getItem(LS_TOOLS) || "null");
    return Array.isArray(v) ? v : null;
  } catch {
    return null;
  }
}

/** 激活中的 LLM 配置：不传给后端的话，「LLM 配置」页配了也不生效 */
function activeLlm(): { base_url: string; model: string; api_key: string } | null {
  try {
    const list = JSON.parse(localStorage.getItem("xihuang_llm_profiles_v1") || "[]");
    if (!Array.isArray(list)) return null;
    const p = list.find((x: any) => x?.active);
    if (!p) return null;
    return { base_url: p.baseUrl || "", model: p.model || "", api_key: p.apiKey || "" };
  } catch {
    return null;
  }
}

function llmBody() {
  const p = activeLlm();
  if (!p) return {};
  // 只传填了的字段：留空表示跟随后端 .env
  const out: Record<string, string> = {};
  if (p.base_url) out.base_url = p.base_url;
  if (p.model) out.model = p.model;
  if (p.api_key) out.api_key = p.api_key;
  return out;
}

export function useChat() {
  const [sessions, setSessions] = useState<Session[]>(loadSessions);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const saveTimer = useRef<number | null>(null);

  // 流式期间每来一个事件都写 localStorage 会卡，debounce 200ms
  useEffect(() => {
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      try {
        localStorage.setItem(LS_SESSIONS, JSON.stringify(sessions));
      } catch {
        /* 超配额或隐私模式 */
      }
    }, 200);
  }, [sessions]);

  const current = sessions.find((s) => s.id === currentId) || null;

  const patch = useCallback((sid: string, index: number, fn: (m: Msg) => Msg) => {
    setSessions((prev) =>
      prev.map((s) =>
        s.id !== sid
          ? s
          : { ...s, messages: s.messages.map((m, i) => (i === index ? fn(m) : m)) }
      )
    );
  }, []);

  const stream = useCallback(
    async (body: Record<string, unknown>, sid: string, index: number) => {
      setStreaming(true);
      const ac = new AbortController();
      abortRef.current = ac;
      try {
        const r = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: ac.signal,
        });
        if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);

        for await (const ev of readSSE(r)) {
          if (!ev || typeof ev !== "object") continue;
          if (ev.type === "final") {
            patch(sid, index, (m) => ({ ...m, final: ev.markdown, streaming: "" }));
          } else if (ev.type === "delta") {
            // token 级流式：累积进瞬态字段，不进 events（避免持久化膨胀与重放错乱）
            patch(sid, index, (m) => ({ ...m, streaming: (m.streaming || "") + ev.text }));
          } else if (ev.type === "reasoning") {
            // 模型思考链：累计进持久字段（折叠区展示；只留尾部防超长撑爆存储）
            patch(sid, index, (m) => ({
              ...m,
              reasoning: ((m.reasoning || "") + ev.text).slice(-4000),
            }));
          } else if (ev.type === "sub_delta") {
            // 子 Agent token 直播：过程卡顶部的实时思考行（只留尾部，防长结论撑爆）
            patch(sid, index, (m) => ({
              ...m,
              liveThink: ((m.liveThink || "") + ev.text).slice(-400),
            }));
          } else if (ev.type === "done") {
            patch(sid, index, (m) => ({
              ...m,
              streaming: "",
              liveThink: "",
              meta: { ms: ev.ms, usage: ev.usage as Usage | undefined },
            }));
          } else if (ev.type === "clarify") {
            patch(sid, index, (m) => ({ ...m, clarify: ev }));
          } else if (ev.type === "plan") {
            patch(sid, index, (m) => ({ ...m, plan: ev }));
          } else if (ev.type === "artifact") {
            // 产物资产：报告/文件卡。同一次报告只登记一条，按 id 去重
            patch(sid, index, (m) => {
              const list = m.artifacts || [];
              if (list.some((a) => a.id === ev.id)) return m;
              const { type: _t, ...art } = ev;
              return { ...m, artifacts: [...list, art] };
            });
          } else if (ev.type === "thinking") {
            // 播报文字此前已按 delta 流出过：转正为思考行时清流式区，避免同一句显示两遍
            patch(sid, index, (m) => ({ ...m, streaming: "", events: [...(m.events || []), ev] }));
          } else {
            // step/result 等过程事件落地：实时思考行随之固化清空
            patch(sid, index, (m) => ({ ...m, liveThink: "", events: [...(m.events || []), ev] }));
          }
        }
      } catch (e: any) {
        patch(sid, index, (m) => ({ ...m, streaming: "" }));
        if (e?.name !== "AbortError") {
          patch(sid, index, (m) => ({
            ...m,
            liveThink: "",
            events: [...(m.events || []), { type: "error", message: "连接中断：" + e.message }],
          }));
        } else {
          // 用户主动终止：清掉实时思考行（带 spinner），并打终止标记——
          // 否则没等到 result 的步骤会永远转圈，观感像「终止没生效」
          patch(sid, index, (m) => ({ ...m, liveThink: "", aborted: true }));
        }
      } finally {
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [patch]
  );

  const send = useCallback(
    async (text: string, context?: Record<string, unknown>) => {
      const t = text.trim();
      if (!t || streaming) return;

      const base = sessions;
      const existing = base.find((s) => s.id === currentId);
      // 附件挂在用户消息上（气泡上方的文件卡）：name 用原始文件名，sub 说明落到了哪
      const atts = Array.isArray((context as any)?.attachments)
        ? ((context as any).attachments as any[])
        : [];
      const files = atts.map((a: any) => ({
        name: String(a.file || (a.kind === "data" ? a.table : a.name) || "文件"),
        kind: a.kind === "doc" ? ("doc" as const) : ("data" as const),
        sub: a.kind === "data" ? `已入库 · ${a.table}` : "已入知识库",
      }));
      const now = Date.now();
      const userMsg: Msg = { role: "user", text: t, ts: now, ...(files.length ? { files } : {}) };
      const asstMsg: Msg = { role: "assistant", events: [], final: "", ts: now };

      let sid: string;
      let next: Session[];

      if (existing) {
        sid = existing.id;
        next = base.map((s) =>
          s.id === sid ? { ...s, messages: [...s.messages, userMsg, asstMsg] } : s
        );
      } else {
        sid = "s" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
        next = [
          {
            id: sid,
            title: t.slice(0, 18) || files[0]?.name || "新会话",
            createdAt: Date.now(),
            messages: [userMsg, asstMsg],
          },
          ...base,
        ];
        setCurrentId(sid);
      }

      setSessions(next);
      const idx = next.find((s) => s.id === sid)!.messages.length - 1;
      await stream(
        {
          message: t,
          session_id: sid,
          skills: loadSkills(),
          ...(context ? { context } : {}),
          ...llmBody(),
        },
        sid,
        idx
      );
    },
    [sessions, currentId, streaming, stream]
  );

  const resume = useCallback(
    async (
      answer: { value?: string | null; label?: string | null; text?: string; action?: string },
      kind: "clarify" | "plan" = "clarify"
    ) => {
      const s = sessions.find((x) => x.id === currentId);
      if (!s || streaming) return;
      const idx = s.messages.length - 1;
      const last = s.messages[idx];
      if (!last || last.role !== "assistant") return;

      if (kind === "plan") {
        const label =
          answer.action === "cancel" ? "已取消" : answer.text ? `已按你的意见调整` : "已批准";
        patch(s.id, idx, (m) => ({ ...m, planAnswered: label }));
        await stream(
          { message: "", session_id: s.id, plan_answer: answer, skills: loadSkills(), ...llmBody() },
          s.id,
          idx
        );
        return;
      }

      patch(s.id, idx, (m) => ({ ...m, clarifyAnswered: answer.label || answer.text || "" }));
      await stream(
        { message: "", session_id: s.id, clarify_answer: answer, skills: loadSkills(), ...llmBody() },
        s.id,
        idx
      );
    },
    [sessions, currentId, streaming, patch, stream]
  );

  /** 撤回：移除某条用户消息（及其回答）。仅前端会话移除——
      服务端 LangGraph 历史仍保留（后续追问的模型上下文里还能看到），界面上不再显示。 */
  const recallMessage = useCallback(
    (index: number) => {
      const s = sessions.find((x) => x.id === currentId);
      if (!s || streaming) return;
      const msgs = s.messages;
      const m = msgs[index];
      if (!m || m.role !== "user") return;
      let end = index + 1;
      if (msgs[end] && msgs[end].role === "assistant") end += 1;
      const tip = end > index + 1 ? "撤回这条消息？（连同它的回答一并移除）" : "撤回这条消息？";
      if (!window.confirm(tip)) return;
      const keep = msgs.filter((_, k) => k < index || k >= end);
      setSessions((prev) =>
        prev.map((x) => (x.id === s.id ? { ...x, messages: keep } : x))
      );
    },
    [sessions, currentId, streaming]
  );

  /** 重新生成：同一问题重问（后端注入「换角度重新组织」提示）。
      不追加新的用户气泡——原问题保留，只新增一条带 regenerated 标记的回答。 */
  const regenerate = useCallback(
    async (question: string) => {
      const q = question.trim();
      if (!q || streaming) return;
      const existing = sessions.find((s) => s.id === currentId);
      if (!existing) {
        await send(q);
        return;
      }
      const asstMsg: Msg = { role: "assistant", events: [], final: "", regenerated: true, ts: Date.now() };
      const next = sessions.map((s) =>
        s.id === existing.id ? { ...s, messages: [...s.messages, asstMsg] } : s
      );
      setSessions(next);
      const idx = next.find((s) => s.id === existing.id)!.messages.length - 1;
      await stream(
        { message: q, session_id: existing.id, regenerate: true, skills: loadSkills(), ...llmBody() },
        existing.id,
        idx
      );
    },
    [sessions, currentId, streaming, send, stream]
  );

  const newSession = useCallback(() => {
    abortRef.current?.abort();
    setCurrentId(null);
  }, []);

  /** 中途停止：中断流式请求——过程事件保留，已流出的增量正文清空（见 catch 的 AbortError 分支） */
  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const selectSession = useCallback((id: string) => setCurrentId(id), []);

  const deleteSession = useCallback(
    (id: string) => {
      setSessions((prev) => prev.filter((s) => s.id !== id));
      setCurrentId((cur) => (cur === id ? null : cur));
    },
    []
  );

  return {
    sessions,
    current,
    currentId,
    streaming,
    send,
    resume,
    regenerate,
    recallMessage,
    stop,
    newSession,
    selectSession,
    deleteSession,
  };
}
