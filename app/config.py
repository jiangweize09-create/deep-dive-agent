import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class Settings:
    app_name = "Deep Dive Agent"

    root: Path = ROOT
    db_path: Path = Path(os.getenv("XIHUANG_DB_PATH", ROOT / "data" / "xihuang.duckdb"))
    frontend_dir: Path = ROOT / "frontend"

    # live 模式默认 LLM 配置（前端面板传入的优先）
    env_base_url: str = os.getenv("XIHUANG_LLM_BASE_URL", "").strip()
    env_api_key: str = os.getenv("XIHUANG_LLM_API_KEY", "").strip()
    env_model: str = os.getenv("XIHUANG_LLM_MODEL", "").strip()

    # 护栏与预算
    max_steps: int = int(os.getenv("XIHUANG_MAX_STEPS", "8"))
    max_rows: int = 1000          # 强制 LIMIT
    row_preview: int = 8          # 返回给 LLM 的预览行数

    # 句柄协议：子 Agent 之间只传句柄不传数据，数据落独立临时库（读写）。
    # 业务库连接保持只读不变；唯一写入口是 persist_result 的确定性落表。
    scratch_path: Path = Path(os.getenv("XIHUANG_SCRATCH_PATH", ROOT / "data" / "scratch.duckdb"))
    reports_dir: Path = ROOT / "data" / "reports"
    max_handles_per_session: int = 8

    # RAG 知识库：embedding 双通道 —— 配置了专用模型走 OpenAI 兼容 /embeddings，
    # 未配置则用本地确定性哈希向量化（零配置可用，BM25 腿不受影响）
    rag_embedding_model: str = os.getenv("XIHUANG_RAG_EMBEDDING_MODEL", "").strip()
    rag_embedding_base_url: str = (
        os.getenv("XIHUANG_RAG_EMBEDDING_BASE_URL", "").strip() or env_base_url
    )
    rag_embedding_api_key: str = (
        os.getenv("XIHUANG_RAG_EMBEDDING_API_KEY", "").strip() or env_api_key
    )
    rag_store_path: Path = ROOT / "data" / "rag_store.json"
    rag_chunk_size: int = int(os.getenv("XIHUANG_RAG_CHUNK_SIZE", "500"))
    rag_chunk_overlap: int = int(os.getenv("XIHUANG_RAG_CHUNK_OVERLAP", "80"))
    rag_top_k: int = int(os.getenv("XIHUANG_RAG_TOP_K", "5"))

    # 语音输入（可选增强）：阿里云百炼 Paraformer-v2，走 OpenAI 兼容转写端点
    # key 缺失时前端提示降级，不影响问答主链路
    asr_api_key: str = (
        os.getenv("XIHUANG_ASR_API_KEY", "").strip() or os.getenv("DASHSCOPE_API_KEY", "").strip()
    )
    asr_model: str = os.getenv("XIHUANG_ASR_MODEL", "paraformer-v2").strip()
    asr_max_seconds: int = int(os.getenv("XIHUANG_ASR_MAX_SECONDS", "60"))   # 前端自动停止上限
    asr_max_bytes: int = int(os.getenv("XIHUANG_ASR_MAX_BYTES", str(10 * 1024 * 1024)))


settings = Settings()
