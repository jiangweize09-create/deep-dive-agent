"""向量化层：双通道设计。

- API 通道：配置 XIHUANG_RAG_EMBEDDING_*（base_url/key 缺省复用 LLM 的 .env 配置）
  后走 OpenAI 兼容 /embeddings 端点，与项目「任意 OpenAI 兼容端点」思路一致。
- 本地通道（默认）：CJK 字符二元组 + 英文词元，经 crc32 稳定哈希进 256 维向量，
  纯确定性、无网络无依赖。语义强度明显弱于真向量，但保证 RAG 零配置开箱可用，
  且混合检索的 BM25 腿不受向量化质量影响——这也是「为什么要混合检索」的论据之一。

两个通道的向量空间不可混用：store 持久化 embedder 指纹，指纹变化自动全量重建索引。
"""

import math
import re
import zlib

from ..config import settings

DIM = 256

_WORD_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """检索共用分词：中文按字符二元组（召回稳定），英文/数字按词。

    例："退款率异常" -> [退款, 款率, 率异, 异常]；"GMV 口径" -> [gmv, 口径]。
    """
    s = (text or "").lower()
    tokens: list[str] = []
    for w in _WORD_RE.findall(s):
        tokens.append(w)
    cjk = re.sub(r"[^\u4e00-\u9fff]+", " ", s).split()
    for seg in cjk:
        if len(seg) == 1:
            tokens.append(seg)
        else:
            tokens.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    return tokens


def embedder_id() -> str:
    """向量化通道指纹：进 store，向量空间变化时触发全量重建。"""
    if settings.rag_embedding_model and settings.rag_embedding_base_url:
        return f"openai:{settings.rag_embedding_model}@{settings.rag_embedding_base_url}"
    return f"local-hash-{DIM}"


def _local_embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    for tok in tokenize(text):
        vec[zlib.crc32(tok.encode("utf-8")) % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _api_embed(texts: list[str]) -> list[list[float]]:
    """OpenAI 兼容 /embeddings。批量分小批发送，超时/失败抛异常由 ingest 兜底降级。"""
    import httpx

    base = settings.rag_embedding_base_url.rstrip("/")
    url = f"{base}/embeddings" if not base.endswith("/embeddings") else base
    out: list[list[float]] = []
    batch = 16
    for i in range(0, len(texts), batch):
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {settings.rag_embedding_api_key}"},
            json={"model": settings.rag_embedding_model, "input": texts[i:i + batch]},
            timeout=60.0,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        out.extend(item["embedding"] for item in sorted(data, key=lambda d: d.get("index", 0)))
    return out


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化。API 配置不完整或调用失败时自动降级本地通道（不阻塞建索引）。"""
    if settings.rag_embedding_model and settings.rag_embedding_base_url:
        try:
            return _api_embed(texts)
        except Exception:
            pass   # 降级：本地通道继续建索引，保证 RAG 可用
    return [_local_embed(t) for t in texts]


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]
