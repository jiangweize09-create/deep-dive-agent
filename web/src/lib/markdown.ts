import { marked } from "marked";
import DOMPurify from "dompurify";

/**
 * Markdown → 安全 HTML。
 * 内容来自 LLM，必须过 DOMPurify；报告链接统一新标签打开，
 * 否则点报告会把整个应用顶掉、只能靠浏览器后退回来。
 */
export function md(text: string): string {
  if (!text) return "";
  let html = "";
  try {
    html = DOMPurify.sanitize(marked.parse(text) as string);
  } catch {
    return "";
  }
  return html.replace(
    /<a href="(\/api\/report\/[^"]+)"/g,
    '<a href="$1" target="_blank" rel="noopener"'
  );
}

export function fmtTokens(n?: number): string {
  if (!n) return "0";
  return n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n);
}

export function metaText(meta: { ms?: number; usage?: any }): string {
  const parts: string[] = [];
  const u = meta.usage || {};
  if (u.steps) parts.push(`${u.steps} 步`);
  if (u.total_tokens) parts.push(`${fmtTokens(u.total_tokens)} tokens`);
  if (meta.ms) parts.push(`${(meta.ms / 1000).toFixed(1)}s`);
  return parts.join(" · ");
}
