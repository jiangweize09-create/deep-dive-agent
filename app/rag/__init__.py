"""RAG 知识库：语料切分入库 -> 混合检索（向量 + BM25，RRF 融合）-> 知识子 Agent。

模块职责：
- corpus/          知识源（指标口径手册 + 分析 SOP，Markdown）
- ingest.py        切分（按标题层级，~500 字 / 80 重叠）与建索引（CLI: python -m app.rag.ingest）
- embeddings.py    向量化双通道：OpenAI 兼容 /embeddings API 或本地确定性哈希（零配置）
- store.py         轻量向量库（JSON 持久化 + 余弦检索；规模化切换点：Chroma/pgvector）
- retrieve.py      混合检索器：向量 top-N + BM25 top-N -> RRF 融合取 top_k
"""
