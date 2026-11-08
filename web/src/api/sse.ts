/**
 * SSE 增量解析。
 *
 * 服务端每次写 `data: {json}\n\n`，但 TCP 分片不保证按事件边界到达，
 * 所以必须累积 buffer 再按 \n\n 切，不能假定一次 read 就是一条事件。
 */
export async function* readSSE(resp: Response): AsyncGenerator<any, void, unknown> {
  if (!resp.body) return;
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });

      let idx = buf.indexOf("\n\n");
      while (idx >= 0) {
        const raw = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 2);
        idx = buf.indexOf("\n\n");
        if (!raw.startsWith("data:")) continue;
        try {
          yield JSON.parse(raw.slice(5).trim());
        } catch {
          // 单条事件解析失败不应中断整条流
        }
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* 已释放 */
    }
  }
}
