import { useEffect, useRef, useState } from "react";
import Icon from "../components/Icon";
import { LogoMark } from "../components/Logo";
import AssistantMessage from "../components/AssistantMessage";
import ErrorBoundary from "../components/ErrorBoundary";
import type { useChat } from "../hooks/useChat";

const LS_PROFILES = "xihuang_llm_profiles_v1";

type Profile = {
  id: string;
  name: string;
  vendor: string;
  baseUrl: string;
  apiKey: string;
  model: string;
  active?: boolean;
};

/** 上传草稿：数据文件解析结果，确认后才入库（HITL） */
type Draft = {
  draft_id: string;
  table: string;
  filename: string;
  rows: number;
  columns: { name: string; type: string; pii?: boolean; sample?: unknown[] }[];
  pii_columns?: string[];
  roles_guess?: { time: string | null; amount: string | null; dims: string[] };
  warnings?: string[];
};

/** 输入框上方的附件 chip：状态机 uploading → pending → ready / doc / error */
type Chip = {
  id: string;
  file: string;
  status: "uploading" | "pending" | "ready" | "doc" | "error";
  error?: string;
  att?: { kind: "data"; table: string; rows?: number; file: string } | { kind: "doc"; name: string; file: string };
};

function loadProfiles(): Profile[] {
  try {
    const v = JSON.parse(localStorage.getItem(LS_PROFILES) || "[]");
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

const HERO = [
  {
    cls: "blue",
    icon: "bar",
    title: "指标查询",
    desc: "用大白话问业务指标，自动翻成语义层 SQL 并真实执行",
    q: "上个月 GMV 多少？",
  },
  {
    cls: "green",
    icon: "trend",
    title: "趋势分析",
    desc: "多时间粒度的订单量与销售额走势，自动出折线图",
    q: "近 6 个月每月订单量趋势",
  },
  {
    cls: "pink",
    icon: "pie",
    title: "占比分析",
    desc: "各地区、各类目的销售贡献分布，生成环形图",
    q: "各地区销售额占比",
  },
  {
    cls: "purple",
    icon: "doc",
    title: "分析报告",
    desc: "查数落表 → 报告 Agent 洞察/图表/Excel，产出可离线打开的 HTML 报告",
    q: "近 6 个月 GMV 分析报告",
  },
  {
    cls: "orange",
    icon: "shield",
    title: "护栏拦截",
    desc: "涉及 PII 字段的请求会被 AST 级护栏当场拦下并解释原因",
    q: "导出所有用户手机号",
  },
];

const CHIP_LABEL: Record<Chip["status"], string> = {
  uploading: "上传中…",
  pending: "待确认",
  ready: "已入库",
  doc: "已入知识库",
  error: "失败",
};

/** 数据文件草稿确认面板（HITL）：核对表名 + 标注时间/金额/维度列后入库 */
function DraftPanel({
  draft,
  onConfirmed,
  onCancel,
}: {
  draft: Draft;
  onConfirmed: (att: { kind: "data"; table: string; rows?: number; file: string }) => void;
  onCancel: () => void;
}) {
  const [table, setTable] = useState(draft.table);
  const [time, setTime] = useState(draft.roles_guess?.time || "");
  const [amount, setAmount] = useState(draft.roles_guess?.amount || "");
  const [dims, setDims] = useState<string[]>(draft.roles_guess?.dims || []);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const colNames = draft.columns.map((c) => c.name);
  const numericCols = draft.columns
    .filter((c) => /INT|DOUBLE|FLOAT|DECIMAL|REAL|HUGEINT/i.test(c.type))
    .map((c) => c.name);

  function toggleDim(n: string) {
    setDims((d) => (d.includes(n) ? d.filter((x) => x !== n) : d.length >= 3 ? d : [...d, n]));
  }

  async function confirm() {
    setBusy(true);
    setErr("");
    try {
      const body: Record<string, unknown> = { draft_id: draft.draft_id, table: table.trim() };
      if (time) body.roles = { time, amount: amount || null, dims };
      const r = await fetch("/api/upload/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(60_000),
      });
      const d = await r.json();
      if (!r.ok || !d.ok) throw new Error(d.detail?.reason || d.reason || `HTTP ${r.status}`);
      onConfirmed({ kind: "data", table: d.table, rows: d.rows, file: draft.filename });
    } catch (e: any) {
      setErr(e?.message || "入库失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="up-panel">
      <div className="up-head">
        <span className="up-title">确认上传「{draft.filename}」</span>
        <span className="up-meta">
          {Number(draft.rows || 0).toLocaleString()} 行 · {draft.columns.length} 列
          {(draft.pii_columns || []).length > 0 &&
            ` · ${draft.pii_columns!.length} 列疑似隐私（${draft.pii_columns!.join("、")}）`}
        </span>
      </div>
      {draft.roles_guess?.time || draft.roles_guess?.amount ? (
        <div className="up-auto">✓ 已自动识别列角色，直接点「确认入库」即可，不合适再手动调整</div>
      ) : (
        <div className="up-auto warn">没认出日期列：仍可入库并提问，但这份数据生成不了趋势看板</div>
      )}
      {(draft.warnings || []).map((w, i) => (
        <div className="up-warn" key={i}>{w}</div>
      ))}
      <div className="up-row">
        <label className="up-field">
          <span className="up-q">入库表名</span>
          <input value={table} onChange={(e) => setTable(e.target.value)} spellCheck={false} />
          <span className="up-ex">以后提问用这个名字点名这份数据，改不改都行</span>
        </label>
        <label className="up-field">
          <span className="up-q">时间列 · 哪一列是日期？</span>
          <select value={time} onChange={(e) => setTime(e.target.value)}>
            <option value="">（没有日期列）</option>
            {colNames.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
          <span className="up-ex">月度趋势图、看板时间范围靠它 · 如：日期、下单时间</span>
        </label>
        <label className="up-field">
          <span className="up-q">金额列 · 哪一列是钱？</span>
          <select value={amount} onChange={(e) => setAmount(e.target.value)}>
            <option value="">（没有金额列）</option>
            {numericCols.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
          <span className="up-ex">看板的「金额合计 / 平均单笔」靠它 · 如：销售额、营业额</span>
        </label>
      </div>
      <div className="up-dims">
        <span className="up-dims-label">维度列 · 哪些是分类？（用于看板的占比饼图 / 排行榜，最多 3 个）</span>
        {colNames
          .filter((n) => n !== time && n !== amount)
          .map((n) => (
            <button
              key={n}
              type="button"
              className={"up-dim" + (dims.includes(n) ? " on" : "")}
              onClick={() => toggleDim(n)}
            >
              {n}
            </button>
          ))}
      </div>
      {err ? <div className="up-err">{err}</div> : null}
      <div className="up-actions">
        <button className="btn-primary" onClick={() => void confirm()} disabled={busy || !table.trim()}>
          {busy ? "入库中…" : "确认入库"}
        </button>
        <button className="btn-db-ghost" onClick={onCancel} disabled={busy}>
          取消
        </button>
        <span className="up-hint">确认后 AI 自动生成看板 · Agent 立即可查这张表</span>
      </div>
    </div>
  );
}

export default function ChatPage({
  active,
  chat,
}: {
  active: boolean;
  chat: ReturnType<typeof useChat>;
}) {
  const [text, setText] = useState("");
  const [profiles, setProfiles] = useState<Profile[]>(loadProfiles);
  const [popOpen, setPopOpen] = useState(false);
  const [envModel, setEnvModel] = useState("");
  const [envLive, setEnvLive] = useState(false);
  const [chips, setChips] = useState<Chip[]>([]);
  const [editingIdx, setEditingIdx] = useState<number | null>(null);   // 正在编辑的用户消息
  const [editDraft, setEditDraft] = useState("");
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [asrReady, setAsrReady] = useState(false);
  const [recSec, setRecSec] = useState<number | null>(null);   // null = 未录音
  const [recBusy, setRecBusy] = useState(false);               // 识别中
  const [asrMsg, setAsrMsg] = useState<{ text: string; kind: "err" | "warn" } | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const modelWrapRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const asrMaxRef = useRef(60);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<number | null>(null);
  const stoppingRef = useRef(false);
  const startAtRef = useRef(0);
  const mimeRef = useRef("");

  const activeProfile = profiles.find((p) => p.active) || null;

  // 一次性拉取后端状态：.env 生效模型名（弹层第一项直接显示真名，不再写"跟随后端"）
  useEffect(() => {
    fetch("/api/health")
      .then((r) => r.json())
      .then((h) => {
        setEnvLive(!!h.live_env);
        setEnvModel(h.live_model || "");
        if (h.asr) {
          asrMaxRef.current = h.asr.max_seconds || 60;
          setAsrReady(!!h.asr.ready);
        }
      })
      .catch(() => {});   // 接口不可用保持占位文案，不影响聊天
  }, []);

  // 点了外面就关弹层
  useEffect(() => {
    if (!popOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (modelWrapRef.current && !modelWrapRef.current.contains(e.target as Node)) {
        setPopOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [popOpen]);

  /** 切换激活配置：active 只留一条，写回 localStorage —— useChat 每次发消息时读它 */
  const chooseProfile = (id: string | null) => {
    const next = profiles.map((p) => ({ ...p, active: p.id === id }));
    setProfiles(next);
    try {
      localStorage.setItem(LS_PROFILES, JSON.stringify(next));
    } catch {
      /* 隐私模式 */
    }
    setPopOpen(false);
  };

  const messages = chat.current?.messages || [];
  const empty = messages.length === 0;
  // 最后一条用户消息的下标：「重新生成」只挂在它身上
  let lastUserIdx = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "user") { lastUserIdx = i; break; }
  }

  // 新消息或流式内容增长时贴底
  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const doSend = (raw?: string) => {
    const v = (raw ?? text).trim();
    if (!v) return;
    setText("");
    if (boxRef.current) boxRef.current.style.height = "auto";
    // 已就绪的附件随消息注入 Agent 上下文；发送后 chips 清空（上下文已带走）
    if (recorderRef.current) stopRec();   // 发送时静默结束未完成的录音
    const atts = chips.map((c) => c.att).filter(Boolean) as NonNullable<Chip["att"]>[];
    void chat.send(v, atts.length ? { attachments: atts } : undefined);
    setChips([]);
  };

  /** 上传一个文件：数据文件出草稿（等确认），文档直接入知识库 */
  async function uploadFile(f: File) {
    const id = "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    setChips((cs) => [...cs, { id, file: f.name, status: "uploading" }]);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch("/api/upload", {
        method: "POST",
        body: fd,
        signal: AbortSignal.timeout(120_000),
      });
      const d = await r.json();
      if (!r.ok || !d.ok) {
        throw new Error(d.detail?.reason || d.reason || d.detail || `HTTP ${r.status}`);
      }
      if (d.kind === "doc") {
        setChips((cs) =>
          cs.map((x) => (x.id === id ? { ...x, status: "doc", att: { kind: "doc", name: d.file, file: f.name } } : x))
        );
      } else {
        setDrafts((ds) => [...ds, { ...d, filename: f.name }]);
        setChips((cs) => cs.map((x) => (x.id === id ? { ...x, status: "pending" } : x)));
      }
    } catch (e: any) {
      const msg = e?.name === "TimeoutError" ? "上传超时（120s）" : e?.message || "上传失败";
      setChips((cs) => cs.map((x) => (x.id === id ? { ...x, status: "error", error: msg } : x)));
    }
  }

  /* ---------- 语音输入：点击开始 / 再点停止识别，识别文字追加进输入框 ---------- */

  function pickMime(): string {
    const cands = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
    if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return "";
    return cands.find((m) => MediaRecorder.isTypeSupported(m)) || "";
  }

  function extOf(mime: string): string {
    if (mime.includes("mp4")) return "m4a";
    if (mime.includes("ogg")) return "ogg";
    if (mime.includes("wav")) return "wav";
    return "webm";
  }

  function releaseStream() {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }

  function stopRec() {
    const rec = recorderRef.current;
    if (!rec || stoppingRef.current) return;
    stoppingRef.current = true;
    if (timerRef.current) { window.clearInterval(timerRef.current); timerRef.current = null; }
    try {
      if (rec.state !== "inactive") rec.stop();
    } catch {
      releaseStream();
      recorderRef.current = null;
      setRecSec(null);
    }
  }

  async function submitRecording(mime: string) {
    const blob = new Blob(chunksRef.current, { type: mime || "audio/webm" });
    chunksRef.current = [];
    recorderRef.current = null;
    setRecSec(null);
    releaseStream();

    if (blob.size < 1200) {
      setAsrMsg({ text: "没有录到声音（音频太短或麦克风被静音），再试一次或直接用键盘输入。", kind: "err" });
      return;
    }
    setRecBusy(true);
    setAsrMsg({ text: "识别中…", kind: "warn" });
    try {
      const fd = new FormData();
      fd.append("audio", blob, "recording." + extOf(mime || ""));
      const r = await fetch("/api/asr", { method: "POST", body: fd });
      const j = await r.json().catch(() => ({}));
      if (!r.ok && r.status === 422) {
        setAsrMsg({ text: "语音接口调用异常，请刷新页面后重试。", kind: "err" });
        return;
      }
      const payload = j?.detail?.code ? j.detail : j;   // 5xx 错误体在 detail 里
      if (!payload?.ok) {
        setAsrMsg({ text: payload?.message || "语音识别失败，请重试或改用键盘输入。", kind: "err" });
        return;
      }
      setText((t) => (t.trim() ? t.trim() + " " + payload.text : payload.text));
      boxRef.current?.focus();
      setAsrMsg({
        text: "识别成功（" + (payload.ms || 0) + "ms · " +
              (payload.audio_bytes ? Math.round(payload.audio_bytes / 1024) : 0) + "KB）· 确认后点发送。",
        kind: "warn",
      });
    } catch (e: any) {
      setAsrMsg({ text: "语音识别请求失败：" + (e?.message || e), kind: "err" });
    } finally {
      setRecBusy(false);
    }
  }

  async function startRec() {
    if (recorderRef.current) { stopRec(); return; }    // 录音中再点 = 停止识别
    if (!asrReady) {
      setAsrMsg({ text: "语音识别未配置：请在项目根目录 .env 填 XIHUANG_ASR_API_KEY（或 DASHSCOPE_API_KEY）后重启后端。可直接用键盘输入。", kind: "err" });
      boxRef.current?.focus();
      return;
    }
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      setAsrMsg({ text: "当前浏览器不支持录音（MediaRecorder），请改用 Chrome / Edge。", kind: "err" });
      return;
    }
    try {
      streamRef.current = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e: any) {
      setAsrMsg({
        text: e?.name === "NotAllowedError"
          ? "麦克风权限被拒绝。请在地址栏右侧允许麦克风访问后重试。"
          : "无法打开麦克风：" + (e?.message || e),
        kind: "err",
      });
      return;
    }
    const mime = pickMime();
    mimeRef.current = mime;
    let rec: MediaRecorder;
    try {
      rec = mime ? new MediaRecorder(streamRef.current, { mimeType: mime })
                 : new MediaRecorder(streamRef.current);
    } catch (e: any) {
      releaseStream();
      setAsrMsg({ text: "录音初始化失败：" + (e?.message || e), kind: "err" });
      return;
    }
    recorderRef.current = rec;
    chunksRef.current = [];
    stoppingRef.current = false;
    rec.ondataavailable = (ev) => { if (ev.data?.size) chunksRef.current.push(ev.data); };
    rec.onstop = () => { void submitRecording(mimeRef.current); };
    rec.start();
    startAtRef.current = Date.now();
    setRecSec(0);
    setAsrMsg({ text: "正在录音…说完再点一次麦克风结束并识别。", kind: "warn" });
    timerRef.current = window.setInterval(() => {
      const sec = Math.floor((Date.now() - startAtRef.current) / 1000);
      setRecSec(sec);
      if (sec >= asrMaxRef.current) {
        setAsrMsg({ text: "已达 " + asrMaxRef.current + " 秒上限，自动停止并识别。", kind: "warn" });
        stopRec();
      }
    }, 250);
  }

  /** 移除 chip：只从本条消息的上下文摘除，不删后端的表/文档（管理在「数据」页） */
  function removeChip(id: string) {
    setChips((cs) => {
      const chip = cs.find((c) => c.id === id);
      if (chip?.status === "pending") {
        const name = chip.file;
        setDrafts((ds) => ds.filter((d) => d.filename !== name));
      }
      return cs.filter((c) => c.id !== id);
    });
  }

  return (
    <section className="page" hidden={!active}>
      <div className="thread" id="thread" ref={threadRef}>
        <div className="thread-inner">
          {empty ? (
            <div className="hero">
              <div className="hero-logo">
                <LogoMark size={44} />
              </div>
              <h1>Deep Dive · AI 数据助理</h1>
              <div className="sug-grid">
                {HERO.map((s) => (
                  <button key={s.title} className="sug-card" type="button" onClick={() => doSend(s.q)}>
                    <span className={"sug-icon " + s.cls}>
                      <Icon name={s.icon} size={19} />
                    </span>
                    <span className="sug-text">
                      <span className="sug-title">{s.title}</span>
                      <span className="sug-desc">{s.desc}</span>
                    </span>
                    <span className="sug-go">›</span>
                  </button>
                ))}
              </div>
</div>
          ) : (
            messages.map((m, i) =>
              m.role === "user" ? (
                <div className="msg user" key={i}>
                  {(m.files || []).length > 0 && (
                    <div className="msg-files">
                      {m.files!.map((f, k) => (
                        <span
                          key={k}
                          className={"msg-file " + f.kind}
                          title={f.sub || f.name}
                        >
                          <Icon name={f.kind === "doc" ? "doc" : "data"} size={12} />
                          {f.name}
                          {f.sub ? <em>{f.sub}</em> : null}
                        </span>
                      ))}
                    </div>
                  )}
                  {editingIdx === i ? (
                    <div className="user-edit">
                      <textarea
                        className="plan-edit-input"
                        value={editDraft}
                        onChange={(e) => setEditDraft(e.target.value)}
                        rows={3}
                        autoFocus
                      />
                      <div className="plan-actions">
                        <button
                          className="plan-btn primary"
                          disabled={!editDraft.trim()}
                          onClick={() => {
                            setEditingIdx(null);
                            void doSend(editDraft.trim());
                          }}
                        >
                          以新问题发送
                        </button>
                        <button className="plan-btn" onClick={() => setEditingIdx(null)}>
                          取消
                        </button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="user-bubble">{m.text}</div>
                      <div className="user-actions">
                        {m.ts ? <span className="ua-time">{new Date(m.ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</span> : null}
                        <button title="复制问题" onClick={() => {
                          navigator.clipboard?.writeText(m.text || "").then(() => {
                            setCopiedIdx(i);
                            window.setTimeout(() => setCopiedIdx((c) => (c === i ? null : c)), 1200);
                          }).catch(() => {});
                        }}>
                          <Icon name={copiedIdx === i ? "check" : "copy"} size={14} />
                        </button>
                        {i === lastUserIdx && m.text && !chat.streaming ? (
                          <button title="重新生成回答" onClick={() => chat.regenerate(m.text || "")}>
                            <Icon name="undo" size={14} />
                          </button>
                        ) : null}
                        <button title="以修改后的问题追加发送（原对话保留）" onClick={() => { setEditingIdx(i); setEditDraft(m.text || ""); }}>
                          <Icon name="pencil" size={14} />
                        </button>
                        <button title="撤回（连同回答一并移除）" onClick={() => chat.recallMessage(i)}>
                          <Icon name="trash" size={14} />
                        </button>
                      </div>
                    </>
                  )}
                </div>
              ) : (
                <ErrorBoundary key={i} label={"assistant-" + i}>
                  <AssistantMessage
                    msg={m}
                    onAnswer={(a) => void chat.resume(a)}
                    onPlan={(a) => void chat.resume(a, "plan")}
                  />
                </ErrorBoundary>
              )
            )
          )}
        </div>
      </div>

      <div className="composer">
        <div className="composer-inner">
          {drafts.map((d) => (
            <DraftPanel
              key={d.draft_id}
              draft={d}
              onConfirmed={(att) => {
                setDrafts((ds) => ds.filter((x) => x.draft_id !== d.draft_id));
                setChips((cs) =>
                  cs.map((c) =>
                    c.file === d.filename && c.status === "pending" ? { ...c, status: "ready", att } : c
                  )
                );
              }}
              onCancel={() => {
                setDrafts((ds) => ds.filter((x) => x.draft_id !== d.draft_id));
                setChips((cs) => cs.filter((c) => !(c.file === d.filename && c.status === "pending")));
              }}
            />
          ))}

          {(recSec != null || recBusy) && (
            <div className="rec-bar">
              <span className="rec-dot" />
              <span className="rec-text">{recBusy ? "识别中…" : "正在录音"}</span>
              <span className="rec-time">
                {String(Math.floor((recSec || 0) / 60)).padStart(2, "0")}:{String((recSec || 0) % 60).padStart(2, "0")}
              </span>
              <span className="rec-hint">再次点击麦克风结束并识别</span>
            </div>
          )}
          {asrMsg ? <div className={"asr-tip " + asrMsg.kind}>{asrMsg.text}</div> : null}

          {chips.length > 0 && (
            <div className="ctx-chips">
              {chips.map((c) => (
                <span
                  key={c.id}
                  className={"ctx-chip " + c.status}
                  title={
                    c.error ||
                    (c.att?.kind === "data" ? `已入库 ${c.att.table}，发送时自动带入上下文` : undefined) ||
                    (c.att?.kind === "doc" ? `已入知识库 ${c.att.name}` : undefined) ||
                    c.file
                  }
                >
                  {c.file}
                  <em>{CHIP_LABEL[c.status]}</em>
                  <button className="chip-x" onClick={() => removeChip(c.id)} title="移除">
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}

          <div className="composer-box" id="composerBox">
            <textarea
              id="input"
              ref={boxRef}
              rows={2}
              placeholder="问个数据问题，例如：上个月 GMV 多少？"
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                const el = e.target;
                el.style.height = "auto";
                el.style.height = Math.min(el.scrollHeight, 132) + "px";
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  doSend();
                }
              }}
            />
            <div className="composer-tools">
              <div className="ct-left">
                <button
                  className="btn-tool"
                  title="上传文件：CSV/Excel 入库可查，Word/PDF 入知识库"
                  onClick={() => fileRef.current?.click()}
                >
                  <Icon name="clip" size={14} />
                </button>
              </div>
              <div className="ct-right">
                <div className="model-wrap" ref={modelWrapRef}>
                  <button
                    className="btn-model"
                    id="btnModel"
                    title={activeProfile
                      ? `当前使用：${activeProfile.name || activeProfile.model}`
                      : envModel
                        ? `当前使用后端配置：${envModel}`
                        : "跟随后端 .env 配置"}
                    onClick={() => {
                      setProfiles(loadProfiles());   // 每次打开都重新读，LLM 页刚加的配置能立刻看到
                      setPopOpen((v) => !v);
                    }}
                  >
                    <Icon name="chip" size={14} />
                    <span id="modelLabel">{activeProfile?.model || envModel || "默认模型"}</span>
                    <Icon name="caret" size={12} className="caret" />
                  </button>
                  {popOpen ? (
                    <div className="model-pop">
                      <div className="mp-head">选择模型</div>
                      <button
                        className={"mp-item" + (activeProfile ? "" : " on")}
                        onClick={() => chooseProfile(null)}
                      >
                        <span className="mp-name">
                          {envModel ? `${envModel}（后端 .env）` : "跟随后端 .env"}
                        </span>
                        <span className="mp-sub">
                          {envLive ? "服务端配置的默认模型" : "后端未配 LLM，将走演示模式"}
                        </span>
                      </button>
                      {profiles.map((p) => (
                        <button
                          key={p.id}
                          className={"mp-item" + (p.active ? " on" : "")}
                          onClick={() => chooseProfile(p.id)}
                        >
                          <span className="mp-name">{p.model || p.name || "未命名"}</span>
                          <span className="mp-sub">
                            {p.vendor}
                            {p.baseUrl ? " · " + p.baseUrl.replace(/^https?:\/\//, "") : " · 默认端点"}
                          </span>
                        </button>
                      ))}
                      {profiles.length === 0 ? (
                        <div className="mp-empty">还没有配置模型。去侧栏「LLM 配置」添加一条。</div>
                      ) : null}
                      <div className="mp-foot">切换后下一条消息生效</div>
                    </div>
                  ) : null}
                </div>
                <button
                  className={"btn-mic" + (recSec != null ? " rec" : "")}
                  title={recSec != null ? "停止录音并识别" : recBusy ? "识别中…" : "语音输入（点击开始，再点结束识别）"}
                  disabled={recBusy}
                  onClick={() => void startRec()}
                >
                  <Icon name="mic" size={14} />
                </button>
                <button
                  className="btn-send"
                  id="btnSend"
                  title={chat.streaming ? "停止" : "发送"}
                  onClick={() => (chat.streaming ? chat.stop() : doSend())}
                >
                  {chat.streaming ? <span className="stop-square" /> : <Icon name="send" size={14} />}
                </button>
              </div>
            </div>
          </div>
          
          <input
            ref={fileRef}
            type="file"
            hidden
            multiple
            accept=".csv,.xlsx,.txt,.md,.json,.docx,.pptx,.pdf"
            onChange={(e) => {
              for (const f of e.target.files || []) void uploadFile(f);
              e.target.value = "";
            }}
          />
        </div>
      </div>
    </section>
  );
}
