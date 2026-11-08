"""语音输入（ASR）：浏览器录音 -> 阿里云百炼 Paraformer-v2 -> 文字。

选型对比（面试可直接讲，这是本模块存在的理由）：
  阿里百炼 paraformer-v2   0.288 元/小时，免费额度每月 1 日自动重发 10 小时 <- 选它
  阿里百炼 qwen3-asr 实时   约 1.1 元/小时，免费额度一次性，抗噪/方言更好但贵 4 倍
  火山豆包 录音文件识别      0.8 元/小时，需把音频传公网 URL，链路更重
  本地 faster-whisper        0 元，但要下模型且吃 CPU，1 秒音频要 0.5~3 秒出结果

关键设计：
1. 走 OpenAI 兼容的 /audio/transcriptions（multipart 上传），不引入 dashscope SDK，
   与项目「任意 OpenAI 兼容端点」的思路一致，换供应商只改 base_url。
2. 未配置 key 时不报错崩溃，而是返回结构化错误码，前端据此提示并聚焦键盘输入
   （项目原则：零配置也要能跑通全链路，语音属于可选增强）。
3. 失败一律返回 {ok: false, code, message}，不抛异常——与 tools层同一套错误约定。
"""

import time

from .config import settings

DASHSCOPE_TRANSCRIBE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/audio/transcriptions"

# Paraformer-v2 官方支持容器格式；MediaRecorder 在 Chrome/Edge 出 webm/opus，在 Safari 出 mp4
ALLOWED_EXTS = {"webm", "mp4", "m4a", "ogg", "oga", "wav", "mp3", "mpeg", "flac", "aac"}

NOT_CONFIGURED_MSG = (
    "语音服务未配置：请在项目根目录 .env 填入 DASHSCOPE_API_KEY（或 XIHUANG_ASR_API_KEY），"
    "保存后重启后端即可。参考 .env.example。也可以直接用键盘输入。"
)


def asr_ready() -> bool:
    """是否已配置语音识别凭证。"""
    return bool(settings.asr_api_key)


def asr_info() -> dict:
    """给前端 /api/health 用的状态块（不泄露key）。"""
    return {
        "ready": asr_ready(),
        "model": settings.asr_model if asr_ready() else "",
        "max_seconds": settings.asr_max_seconds,
    }


def _fail(code: str, message: str, status: int = 400) -> dict:
    return {"ok": False, "code": code, "message": message, "status": status}


def transcribe(data: bytes, filename: str, content_type: str = "") -> dict:
    """把一段录音转成文字。返回 {"ok": True, "text": ..., "ms": ...} 或结构化失败。"""
    if not asr_ready():
        return _fail("asr_not_configured", NOT_CONFIGURED_MSG, 503)
    if not data:
        return _fail("empty_audio", "没有收到音频数据，请重新录音。")
    if len(data) > settings.asr_max_bytes:
        mb = settings.asr_max_bytes // 1024 // 1024
        return _fail("audio_too_large", f"音频超过 {mb}MB，请缩短录音时长（上限 {settings.asr_max_seconds} 秒）。")

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTS:
        return _fail(
            "unsupported_format",
            f"不支持的音频格式 .{ext or '未知'}，请用 Chrome / Edge 录音（webm）或 Safari（mp4）。",
        )

    import httpx  # 延迟导入：没配语音时不白占启动时间

    started = time.perf_counter()
    try:
        resp = httpx.post(
            DASHSCOPE_TRANSCRIBE_URL,
            headers={"Authorization": f"Bearer {settings.asr_api_key}"},
            files={"file": (filename, data, content_type or "application/octet-stream")},
            data={"model": settings.asr_model},
            timeout=60.0,
        )
    except httpx.TimeoutException:
        return _fail("asr_timeout", "语音识别超时（>60s），请检查网络后重试。", 504)
    except httpx.HTTPError as e:
        return _fail("asr_network", f"语音服务请求失败：{e}", 502)

    if resp.status_code == 401 or resp.status_code == 403:
        return _fail("asr_bad_key", "语音服务鉴权失败，请检查 .env 里的 API Key 是否正确且已开通百炼。", 502)
    if resp.status_code == 429:
        return _fail("asr_quota", "语音服务额度已用尽或限流，请稍后再试（免费额度每月 1 日自动重发）。", 429)
    if resp.status_code >= 400:
        return _fail("asr_upstream_error", f"语音服务返回错误（HTTP {resp.status_code}）：{resp.text[:200]}", 502)

    try:
        payload = resp.json()
    except ValueError:
        return _fail("asr_bad_response", f"语音服务返回非JSON 内容：{resp.text[:200]}", 502)

    text = (payload.get("text") or "").strip()
    if not text:
        return _fail("asr_empty_text", "没有识别到有效语音，请靠近麦克风重说一次。")

    ms = int((time.perf_counter() - started) * 1000)
    return {
        "ok": True,
        "text": text,
        "ms": ms,
        "model": settings.asr_model,
        "audio_bytes": len(data),
    }