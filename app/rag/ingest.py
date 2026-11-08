"""语料切分与索引构建。

切分策略：Markdown 按标题层级切（# 父标题 + ## 小节保留进 chunk 元数据），
段落后合并到 ~chunk_size 字符，相邻 chunk 之间保留 overlap 字符重叠——
保证「口径 + 示例」这类跨段落的完整语义不被切断。

索引构建是确定性的：语料指纹（sha256）+ embedder 指纹都存进 store，
任一变化则下次 ensure_index() 自动全量重建。

CLI：python -m app.rag.ingest   （手动重建索引）
"""

import hashlib
import json
import sys
from pathlib import Path

from ..config import settings
from . import embeddings
from .store import RagStore

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
# 用户上传文档（app/uploads.py 文档路线落点）：与内置语料同格式、同一索引
USER_CORPUS_DIR = settings.root / "data" / "corpus_user"


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Markdown -> [(小节标题, 小节正文)]。# 级标题作为每节的父上下文。"""
    doc_title = ""
    sections: list[tuple[str, str]] = []
    cur_title, cur_lines = "", []
    for line in text.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            doc_title = line[2:].strip()
            continue
        if line.startswith("## "):
            if cur_lines:
                sections.append((cur_title, "\n".join(cur_lines).strip()))
            cur_title, cur_lines = line[3:].strip(), []
        else:
            cur_lines.append(line)
    if cur_lines:
        sections.append((cur_title, "\n".join(cur_lines).strip()))
    if doc_title:
        sections = [(f"{doc_title} · {t}" if t else doc_title, body) for t, body in sections]
    return [(t, b) for t, b in sections if b]


def _chunk(text: str, size: int, overlap: int) -> list[str]:
    """段落合并成块；超长块滑窗切分并带 overlap。"""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if buf and len(buf) + len(p) + 1 > size:
            chunks.append(buf)
            buf = buf[-overlap:] + "\n" + p if overlap else p
        else:
            buf = f"{buf}\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    out = []
    for c in chunks:
        if len(c) <= size * 1.5:
            out.append(c)
            continue
        step = max(size - overlap, 1)
        for i in range(0, len(c), step):
            piece = c[i:i + size]
            if len(piece) > 20:
                out.append(piece)
    return out


def build_corpus() -> list[dict]:
    """读取内置 corpus/ 与用户语料 data/corpus_user/ 的全部 Markdown，产出 chunk 列表。"""
    chunks: list[dict] = []
    files = sorted(CORPUS_DIR.glob("*.md")) + sorted(USER_CORPUS_DIR.glob("*.md"))
    for path in files:
        text = path.read_text(encoding="utf-8")
        for sec_title, body in _split_sections(text):
            for i, piece in enumerate(_chunk(body, settings.rag_chunk_size,
                                             settings.rag_chunk_overlap)):
                chunks.append({
                    "id": f"{path.stem}:{len(chunks) % 10000}.{i}",
                    "source": path.name,
                    "title": sec_title,
                    "text": piece,
                })
    return chunks


def corpus_fingerprint(chunks: list[dict]) -> str:
    payload = json.dumps([c["id"] + "\n" + c["text"] for c in chunks], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def rebuild() -> RagStore:
    chunks = build_corpus()
    store = RagStore()
    vecs = embeddings.embed_texts([c["text"] for c in chunks])
    for c, v in zip(chunks, vecs):
        store.add(c["id"], c["source"], c["title"], c["text"], v)
    store.fingerprint = corpus_fingerprint(chunks)
    store.embedder = embeddings.embedder_id()
    store.save(settings.rag_store_path)
    return store


def ensure_index() -> RagStore:
    """有索引且指纹未变则复用，否则自动重建（服务启动后首次检索会触发）。"""
    store = RagStore.load(settings.rag_store_path)
    if store and store.fingerprint == corpus_fingerprint(build_corpus()) \
            and store.embedder == embeddings.embedder_id():
        return store
    return rebuild()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    st = rebuild()
    print(f"RAG 索引已重建 -> {settings.rag_store_path}")
    print(f"chunks: {len(st)} | embedder: {st.embedder} | fingerprint: {st.fingerprint}")
    from collections import Counter
    for src, n in Counter(it['source'] for it in st.items).items():
        print(f"  {src:<28} {n} chunks")
