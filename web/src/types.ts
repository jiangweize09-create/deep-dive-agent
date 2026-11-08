/** 服务端 SSE 事件（与 app/main.py 的事件协议一一对应） */
export type Usage = {
  steps?: number;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
};

export type ClarifyOption = { value: string; label: string; hint?: string };

/** 计划审批卡的一步（propose_plan 的 steps 项） */
export type PlanStep = { tool: string; task: string };

/** 产物资产（app/artifacts.db 登记，服务端持久——重启不丢入口） */
export type Artifact = {
  id: string;
  kind: string; // 目前 report
  session_id?: string;
  title: string;
  url: string;
  excel_url?: string;
  meta?: { charts?: number; insights?: number; task?: string };
  created_at?: string;
};

export type Ev =
  | { type: "thinking"; text: string }
  | { type: "delta"; text: string }
  | { type: "sub_delta"; agent?: string; text: string }
  | { type: "reasoning"; agent?: string; text: string }
  | { type: "step"; tool: string; args?: Record<string, unknown> }
  | { type: "result"; tool: string; content: any }
  | { type: "final"; markdown: string }
  | { type: "error"; message: string }
  | { type: "clarify"; term: string; question: string; options?: ClarifyOption[] }
  | { type: "plan"; term: string; question: string; steps?: PlanStep[]; note?: string }
  | ({ type: "artifact" } & Artifact)
  | { type: "done"; usage?: Usage; ms?: number };

export type Msg = {
  role: "user" | "assistant";
  /** 用户消息正文 */
  text?: string;
  /** 发送时间戳（撤回/复制操作条上显示） */
  ts?: number;
  /** 本条消息携带的附件（发送时注入上下文，气泡上方以文件卡展示） */
  files?: { name: string; kind: "data" | "doc"; sub?: string }[];
  /** 助手消息的过程事件流 */
  events?: Ev[];
  /** token 流式中的增量文本（瞬态：不进持久化语义，final 到达后清空） */
  streaming?: string;
  /** 用户点终止：未完成步骤显示「已终止」而非无限转圈 */
  aborted?: boolean;
  /** 子 Agent 实时思考行（瞬态：过程事件落地时固化清空，不进持久化） */
  liveThink?: string;
  /** 模型思考链（reasoning_content 累计，随会话持久；折叠区展示，超长留尾部） */
  reasoning?: string;
  final?: string;
  /** 本条回答是「重新生成」产物（前端角标展示） */
  regenerated?: boolean;
  meta?: { ms?: number; usage?: Usage };
  clarify?: Extract<Ev, { type: "clarify" }>;
  clarifyAnswered?: string;
  /** 计划审批（HITL）：复合任务先出计划，批准后继续执行 */
  plan?: Extract<Ev, { type: "plan" }>;
  planAnswered?: string;
  /** 本轮产出的交付物（报告/文件卡）：随会话持久，服务端 artifacts.db 同步登记 */
  artifacts?: Artifact[];
};

export type Session = {
  id: string;
  title: string;
  createdAt: number;
  messages: Msg[];
};
