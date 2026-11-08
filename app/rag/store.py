"""轻量向量库：JSON 持久化 + 纯 Python 余弦检索。

为什么不直接上 Chroma：演示语料只有几百个 chunk，纯 Python 余弦单次查询
毫秒级，零重依赖保证「零配置可跑」的项目原则。接口（add/search/save/load）
按向量库的通用形状设计，规模化时整体换成 Chroma/pgvector，上层 retrieve.py
不需要动 —— 与项目里「生产切换点」的一贯做法一致。
"""

import json
import math
from pathlib import Path


class RagStore:
    def __init__(self):
        self.items: list[dict] = []   # {id, source, title, text, vec}
        self.fingerprint: str = ""    # 语料指纹，变了自动重建
        self.embedder: str = ""       # 向量化通道指纹，换了自动重建

    # ---------- 持久化 ----------

    @classmethod
    def load(cls, path: Path) -> "RagStore | None":
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return None
        store = cls()
        store.items = raw.get("items", [])
        store.fingerprint = raw.get("fingerprint", "")
        store.embedder = raw.get("embedder", "")
        if not store.items:
            return None
        return store

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": self.fingerprint, "embedder": self.embedder,
                       "items": self.items}, f, ensure_ascii=False)

    # ---------- 写入 / 检索 ----------

    def add(self, cid: str, source: str, title: str, text: str, vec: list[float]) -> None:
        self.items.append({"id": cid, "source": source, "title": title,
                           "text": text, "vec": vec})

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (na * nb)

    def search(self, vec: list[float], k: int) -> list[tuple[dict, float]]:
        scored = [(item, self._cosine(vec, item["vec"])) for item in self.items]
        scored.sort(key=lambda x: -x[1])
        return scored[:k]

    def texts(self) -> list[str]:
        return [it["text"] for it in self.items]

    def __len__(self) -> int:
        return len(self.items)
