"""混合检索器：向量召回 + BM25 关键词召回 -> RRF 融合。

为什么混合而不是纯向量：中文业务术语（GMV、客单价、首单新客）在弱向量化
或通用 embedding 上召回不稳，BM25 的精确词匹配恰好补这条腿；反过来 SOP 里
的同义改述（"成交额掉" vs "GMV 下滑"）靠向量泛化兜住。两路各取 top-N，
用 Reciprocal Rank Fusion（1/(60+rank) 求和）融合，对两路分数量纲不敏感。

零配置约定：索引在首次检索时懒构建（ensure_index），语料/向量化通道变化
自动重建。检索是纯 CPU 同步操作，调用方（async 工具）需自行丢 to_thread。
"""

import threading
from collections import Counter
from math import log

from ..config import settings
from . import embeddings
from .ingest import ensure_index
from .store import RagStore

_RRF_K = 60
_BM25_K1 = 1.5
_BM25_B = 0.75

# 查询侧同义归一：口语说法 -> 语料里的规范写法。只改查询不改语料
#（语料本来就写的是规范词）。哈希向量与 BM25 都是字面匹配，接不住
# 「GMV 跌了怎么分析」vs「GMV 下滑归因」这种同义改述——本地哈希向量
# 没有泛化能力，这条腿是 README 承诺"靠向量兜住同义词"的真实补丁。
_QUERY_SYNONYMS: dict[str, str] = {
    "跌了": "下滑", "跌": "下滑", "掉了": "下滑", "掉": "下滑",
    "降了": "下滑", "下降": "下滑", "减少": "下滑",
}


def _expand_query(q: str) -> str:
    for slang, norm in _QUERY_SYNONYMS.items():
        if slang in q:
            q = q.replace(slang, norm)
    return q

_lock = threading.Lock()
_bm25_cache: tuple[str, list] = ("", [])   # (store fingerprint, [(tokens, len, tf)]) — 语料小，一次建全量


def _bm25_index(store: RagStore):
    global _bm25_cache
    if _bm25_cache[0] == store.fingerprint:
        return _bm25_cache[1]
    docs = []
    for it in store.items:
        toks = embeddings.tokenize(it["title"] + " " + it["text"])
        tf = Counter(toks)
        docs.append((toks, len(toks), tf))
    _bm25_cache = (store.fingerprint, docs)
    return docs


def _bm25_scores(query_tokens: list[str], docs) -> list[float]:
    """标准 BM25：IDF 取 log((N-df+0.5)/(df+0.5)+1)，长度饱和 k1/b。"""
    n = len(docs)
    if not n:
        return []
    avgdl = sum(d[1] for d in docs) / n or 1.0
    df = Counter()
    for _, _, tf in docs:
        for t in tf:
            df[t] += 1
    scores = []
    for toks, dl, tf in docs:
        s = 0.0
        for t in set(query_tokens):
            if t not in tf:
                continue
            idf = log((n - df[t] + 0.5) / (df[t] + 0.5) + 1.0)
            f = tf[t]
            s += idf * f * (_BM25_K1 + 1) / (f + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / avgdl))
        scores.append(s)
    return scores


def search(query: str, top_k: int | None = None) -> dict:
    """混合检索。返回结构化结果（ok/chunks/engine），不抛异常。"""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "reason": "检索词为空", "chunks": []}
    q = _expand_query(q)
    k = top_k or settings.rag_top_k

    with _lock:
        try:
            store = ensure_index()
        except Exception as e:
            return {"ok": False, "reason": f"索引构建失败：{e}", "chunks": []}

    n_pull = max(k * 4, 20)
    # 路线 1：向量召回
    try:
        qvec = embeddings.embed_query(q)
        vec_hits = store.search(qvec, n_pull)
        vec_rank = {it["id"]: r for r, (it, _) in enumerate(vec_hits)}
    except Exception:
        vec_rank = {}
    # 路线 2：BM25 关键词召回
    q_tokens = embeddings.tokenize(q)
    bm_scores = _bm25_scores(q_tokens, _bm25_index(store))
    bm_order = sorted(range(len(bm_scores)), key=lambda i: -bm_scores[i])
    bm_hits = [i for i in bm_order if bm_scores[i] > 0][:n_pull]
    bm_rank = {store.items[i]["id"]: r for r, i in enumerate(bm_hits)}

    # RRF 融合：两路 rank 的 1/(60+rank) 求和
    rrf: dict[str, float] = {}
    for cid in set(vec_rank) | set(bm_rank):
        s = 0.0
        if cid in vec_rank:
            s += 1.0 / (_RRF_K + vec_rank[cid])
        if cid in bm_rank:
            s += 1.0 / (_RRF_K + bm_rank[cid])
        rrf[cid] = s
    ranked = sorted(rrf.items(), key=lambda x: -x[1])[:k]
    by_id = {it["id"]: it for it in store.items}
    chunks = [{**by_id[cid], "score": round(s, 6)} for cid, s in ranked if cid in by_id]
    engine = embeddings.embedder_id() + " + bm25(rrf)"
    return {"ok": bool(chunks), "query": q, "engine": engine, "chunks": chunks}
