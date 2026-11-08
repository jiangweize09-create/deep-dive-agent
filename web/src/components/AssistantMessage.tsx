import { useState } from "react";
import ProcessCard, { buildGroups, TOOL_LABEL } from "./ProcessCard";
import ChartBox from "./ChartBox";
import { LogoMark } from "./Logo";
import { md, metaText } from "../lib/markdown";
import type { Msg, Ev } from "../types";

type ThinkEv = Extract<Ev, { type: "thinking" }>;

/**
 * 思考：模型思考链（reasoning_content，真实推理草稿）+ 步骤播报行。
 * 流式中实时滚动（思考链取尾部），结束后收成一行「深度思考 ▾」。
 * 它和执行过程不是一类东西——思考是内心独白，默认收起；
 * 执行过程每步有真实回执，值得留入口。
 */
function Thinking({
  thoughts,
  reasoning,
  live,
}: {
  thoughts: ThinkEv[];
  reasoning?: string;
  live: boolean;
}) {
  const [open, setOpen] = useState(false);
  if (!thoughts.length && !reasoning) return null;

  if (live) {
    // 思考链取尾部滚动 + 播报只留最后两条，否则一轮下来堆十几行
    return (
      <div className="thinking live">
        {reasoning ? <div className="think-line think-reasoning">{reasoning.slice(-260)}</div> : null}
        {thoughts.slice(-2).map((t, i) => (
          <div key={i} className="think-line">
            {t.text}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="think-fold">
      <button className="think-toggle" onClick={() => setOpen((o) => !o)}>
        <span className="think-mark" />
        深度思考{reasoning ? " · 含模型推理" : ""}
        <span className="think-caret">{open ? "▴" : "▾"}</span>
      </button>
      {open ? (
        <div className="thinking">
          {reasoning ? <div className="think-reasoning full">{reasoning}</div> : null}
          {thoughts.map((t, i) => (
            <div key={i} className="think-line">
              {t.text}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ClarifyCard({
  msg,
  onAnswer,
}: {
  msg: Msg;
  onAnswer: (a: { value?: string; label?: string; text?: string }) => void;
}) {
  const c = msg.clarify;
  if (!c) return null;

  if (msg.clarifyAnswered) {
    return <div className="clarify-answered">口径已确认：{msg.clarifyAnswered}</div>;
  }

  const options = c.options || [];
  return (
    <div className="clarify-card">
      <div className="clarify-q">{c.question}</div>
      <div className="clarify-opts">
        {options.map((o) => (
          <button
            key={o.value}
            className="clarify-opt"
            onClick={() => onAnswer({ value: o.value, label: o.label })}
          >
            <span className="co-label">{o.label}</span>
            {o.hint ? <span className="co-hint">{o.hint}</span> : null}
          </button>
        ))}
      </div>
    </div>
  );
}

type PlanEv = Extract<Ev, { type: "plan" }>;

/**
 * 计划审批卡（HITL）：复合分析任务先出计划，用户批准/修改/取消后才继续。
 * 与口径澄清卡同一交互模式 —— 点按钮 resume，流式继续。
 */
function PlanCard({
  plan,
  answered,
  onAnswer,
}: {
  plan: PlanEv;
  answered?: string;
  onAnswer: (a: { action: string; text?: string }) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");

  if (answered) {
    return <div className="clarify-answered">计划已处理：{answered}</div>;
  }

  const steps = plan.steps || [];
  return (
    <div className="plan-card">
      <div className="clarify-q">{plan.question}</div>
      <ol className="plan-steps">
        {steps.map((s, i) => (
          <li key={i}>
            <span className="plan-tool">{TOOL_LABEL[s.tool] || s.tool}</span>
            <span className="plan-task">{s.task}</span>
          </li>
        ))}
      </ol>
      {plan.note ? <div className="plan-note">{plan.note}</div> : null}

      {editing ? (
        <div className="plan-edit">
          <textarea
            className="plan-edit-input"
            value={text}
            placeholder="想怎么调整这个计划？（如：先只看华南区，出环比而不出趋势图）"
            onChange={(e) => setText(e.target.value)}
            rows={3}
          />
          <div className="plan-actions">
            <button
              className="plan-btn primary"
              disabled={!text.trim()}
              onClick={() => onAnswer({ action: "edit", text: text.trim() })}
            >
              提交修改并执行
            </button>
            <button className="plan-btn" onClick={() => setEditing(false)}>
              返回
            </button>
          </div>
        </div>
      ) : (
        <div className="plan-actions">
          <button className="plan-btn primary" onClick={() => onAnswer({ action: "approve" })}>
            批准执行
          </button>
          <button className="plan-btn" onClick={() => setEditing(true)}>
            修改计划
          </button>
          <button className="plan-btn" onClick={() => onAnswer({ action: "cancel" })}>
            取消
          </button>
        </div>
      )}
    </div>
  );
}

export default function AssistantMessage({
  msg,
  onAnswer,
  onPlan,
}: {
  msg: Msg;
  onAnswer: (a: { value?: string | null; label?: string | null; text?: string }) => void;
  onPlan?: (a: { action: string; text?: string }) => void;
}) {
  const events = msg.events || [];
  const thoughts = events.filter((e) => e.type === "thinking") as ThinkEv[];
  const groups = buildGroups(events);
  const charts = events.filter(
    (e) =>
      e.type === "result" &&
      (e.tool === "generate_chart" || e.tool === "render_chart") &&
      e.content?.ok &&
      e.content?.option
  ) as any[];
  const errors = events.filter((e) => e.type === "error") as Extract<Ev, { type: "error" }>[];

  // done 事件会写入 meta：用它判断「这一轮结束」，避免失败时 dots 一直转；
  // aborted（用户终止）同样视为结束——否则过程卡头部与思考行会在终止后永远转圈
  const ended = !!msg.meta || !!msg.aborted;
  const waiting = !ended && !msg.final && !msg.clarify && !msg.plan && !msg.streaming;

  return (
    <div className="msg assistant">
      <div className="avatar">
        <LogoMark size={18} />
      </div>
      <div className="msg-body">
        {/* 结论在前：用户来问数是来看答案的，不是来看执行链的 */}
        <Thinking thoughts={thoughts} reasoning={msg.reasoning} live={waiting} />

        {errors.map((e, i) => (
          <div key={i} className="error-banner">
            {e.message}
          </div>
        ))}

        <ClarifyCard msg={msg} onAnswer={onAnswer} />

        {msg.plan && onPlan ? (
          <PlanCard plan={msg.plan} answered={msg.planAnswered} onAnswer={onPlan} />
        ) : null}

        {/* 执行过程在正文上方：阅读顺序 = 先过程后结论；
            流式中展开实时滚动，出结果后自动收起成一行摘要 */}
        <ProcessCard groups={groups} live={waiting} ms={msg.meta?.ms} liveThink={msg.liveThink} aborted={msg.aborted} />

        {/* token 流式中先渲染增量文本（带光标），final 到达后被完整 Markdown 替换 */}
        {msg.streaming && !msg.final ? (
          <div
            className="final final-md streaming"
            dangerouslySetInnerHTML={{ __html: md(msg.streaming) }}
          />
        ) : null}

        {msg.final ? (
          <div className="final final-md" dangerouslySetInnerHTML={{ __html: md(msg.final) }} />
        ) : null}

        {charts.length > 0 && (
          <div className="charts">
            {charts.map((c, i) => (
              <ChartBox key={i} option={c.content.option} title={c.content.title} />
            ))}
          </div>
        )}

        {/* 产物资产卡：本轮交付物（报告/Excel），服务端登记持久可回查 */}
        {(msg.artifacts || []).length > 0 && (
          <div className="artifacts">
            {(msg.artifacts || []).map((a) => (
              <div key={a.id} className="artifact-card">
                <div className="art-glyph">▤</div>
                <div className="art-info">
                  <div className="art-title">{a.title}</div>
                  <div className="art-sub">
                    {[
                      a.meta?.charts ? `${a.meta.charts} 张图表` : "",
                      a.meta?.insights ? `${a.meta.insights} 条洞察` : "",
                      a.created_at ? a.created_at.replace("T", " ") : "",
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                </div>
                <div className="art-actions">
                  {a.url ? (
                    <a className="art-btn primary" href={a.url} target="_blank" rel="noreferrer">
                      打开报告
                    </a>
                  ) : null}
                  {a.excel_url ? (
                    <a className="art-btn" href={a.excel_url}>
                      下载 Excel
                    </a>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}

        {waiting && !groups.length ? (
          <div className="dots">
            <span />
            <span />
            <span />
          </div>
        ) : null}

        {msg.regenerated ? <div className="regen-tag">↻ 已重新生成</div> : null}
        {msg.meta ? <div className="meta">{metaText(msg.meta)}</div> : null}
      </div>
    </div>
  );
}
