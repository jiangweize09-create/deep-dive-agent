"""请求级事件总线（ContextVar）。

子 Agent（SQL / viz / 未来的 Insight）的过程事件走这里旁路给 SSE 层，
不进主 Agent 上下文 —— ToolMessage 里只留结论与用量，主图不随执行步数膨胀。

两条通道，按消费者区分：
- subagent_events（列表）：评测器 / 冒烟测试消费。委派结束后一次性批量取走。
- subagent_queue（asyncio.Queue，快车道）：live SSE 的合并循环实时消费 ——
  子 Agent 每产生一个事件当场入队，前端看到的是直播而不是结尾整段回放。
  两条互不干扰：live 只设 queue，评测只设 events；都设或都不设也安全。

请求隔离约定（必须遵守）：
- 消费方在请求开始时 set，finally 中 reset(token)；
  不 reset 的话，任务复用会把上一个请求的事件串进下一个请求。
- 子 Agent 只在通道存在时写入（get() 为 None 时静默丢弃，便于独立调用）。
"""

import asyncio
from contextvars import ContextVar

subagent_events: ContextVar[list | None] = ContextVar("xihuang_subagent_events", default=None)
subagent_queue: ContextVar["asyncio.Queue | None"] = ContextVar("xihuang_subagent_queue", default=None)
