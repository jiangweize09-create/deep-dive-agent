"""语义层加载与检索。

tables.yaml / metrics.yaml 是业务口径的唯一事实来源：
- 指标定义（expression + required_filters）约束 LLM 生成 SQL 的口径
- PII 标记供护栏层做字段级拦截
- join 关系供护栏层做连通性校验

生产切换点：检索打分当前是关键词/同义词匹配，可平滑替换为
pgvector 向量检索（Schema-First Retrieval），接口签名不变。
"""

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from ..config import settings
from .samples import load_samples

def _norm_joins(joins: list) -> list[dict]:
    """归一化 join 条目。

    YAML 1.1 会把裸键 `on` 解析成布尔 True（on/off/yes/no 是布尔字面量），
    所以 tables.yaml 里的 {table: users, on: "orders.user_id = users.id"}
    读出来是 {True: "..."}。这里统一改回字符串键 "on"，
    否则 edge 解析、给 LLM 的 schema JSON、前端数据源页全部拿不到 join 条件。
    """
    out = []
    for j in joins or []:
        if not isinstance(j, dict):
            continue
        fixed = {}
        for k, v in j.items():
            key = "on" if (k is True or str(k).strip().lower() == "on") else k
            fixed[key] = v
        out.append(fixed)
    return out


SEMANTIC_DIR = Path(__file__).resolve().parent


def _parse_join_on(t1: str, on: str) -> frozenset | None:
    """把声明 join 条件 'orders.user_id = users.id' 解析成 {(表,列)} 对。"""
    if not on or "=" not in on:
        return None
    sides = []
    for part in on.split("="):
        part = part.strip()
        if "." in part:
            t, c = part.split(".", 1)
        else:
            t, c = t1, part
        sides.append((t.strip(), c.strip()))
    if len(sides) != 2:
        return None
    return frozenset(sides)


@dataclass
class ColumnSpec:
    name: str
    type: str
    desc: str
    pii: bool = False
    sample: list | None = None


@dataclass
class TableSpec:
    name: str
    display: str
    description: str
    synonyms: list
    columns: list[ColumnSpec]
    joins: list[dict]


@dataclass
class MetricSpec:
    name: str
    display_name: str
    description: str
    expression: str
    grain: str
    dimensions: list
    synonyms: list
    required_filters: list


class Catalog:
    def __init__(self, tables: list[TableSpec], metrics: list[MetricSpec]):
        self.tables: dict[str, TableSpec] = {t.name: t for t in tables}
        self.metrics: list[MetricSpec] = metrics
        self.columns: dict[str, set] = {name: {c.name for c in t.columns} for name, t in self.tables.items()}
        self.pii: set[tuple[str, str]] = {
            (tname, c.name) for tname, t in self.tables.items() for c in t.columns if c.pii
        }
        self.edges: list[tuple[str, str]] = [
            (tname, j["table"]) for tname, t in self.tables.items() for j in t.joins
        ]
        # 声明的 join 键集合：{frozenset({(t1,col1),(t2,col2)})}，护栏校验 ON 条件用
        self.edge_keys: set[frozenset] = set()
        for tname, t in self.tables.items():
            for j in t.joins:
                key = _parse_join_on(tname, j.get("on", ""))
                if key:
                    self.edge_keys.add(key)

    # ---------- 检索 ----------

    @staticmethod
    def _score(query: str, keys: list[str], description: str) -> int:
        q = query.strip().lower()
        if not q:
            return 0
        score = 0
        for k in keys:
            k = k.lower().strip()
            if not k:
                continue
            if k in q:
                score += 3
            elif len(k) >= 2 and q in k:
                score += 2
        for tok in set(re.split(r"[\s,，。?？!！、的]+", q)):
            if len(tok) >= 2 and tok in description.lower():
                score += 1
        return score

    def search_tables(self, query: str, top_k: int = 3) -> dict:
        scored = []
        for t in self.tables.values():
            keys = [t.name, t.display] + list(t.synonyms)
            col_bonus = sum(1 for c in t.columns if c.name.lower() in query.lower())
            s = self._score(query, keys, t.description) + col_bonus
            scored.append((s, t))
        scored.sort(key=lambda x: -x[0])
        hits = [t for s, t in scored[:top_k] if s > 0]
        if not hits:
            return {
                "tables": [],
                "hint": "没有直接匹配的表。可用表：" + "、".join(
                    f"{t.name}({t.display})" for t in self.tables.values()
                ) + "。请换用业务同义词再试。",
            }
        return {"tables": [self._table_dict(t) for t in hits]}

    def _table_dict(self, t: TableSpec) -> dict:
        cols = []
        for c in t.columns:
            d = {"name": c.name, "type": c.type, "desc": c.desc}
            if c.pii:
                d["pii"] = True
            if c.sample:
                d["sample"] = c.sample
            cols.append(d)
        return {
            "name": t.name,
            "display": t.display,
            "description": t.description,
            "columns": cols,
            "joins": t.joins,
        }

    def search_metrics(self, query: str, top_k: int = 3) -> dict:
        scored = []
        for m in self.metrics:
            keys = [m.name, m.display_name] + list(m.synonyms)
            s = self._score(query, keys, m.description)
            scored.append((s, m))
        scored.sort(key=lambda x: -x[0])
        hits = [m for s, m in scored[:top_k] if s > 0]
        if not hits:
            return {
                "metrics": [],
                "hint": "没有匹配的指标。可用指标：" + "、".join(m.display_name for m in self.metrics),
            }
        return {
            "metrics": [
                {
                    "name": m.name,
                    "display_name": m.display_name,
                    "description": m.description,
                    "expression": m.expression,
                    "required_filters": m.required_filters,
                    "dimensions": m.dimensions,
                    "grain": m.grain,
                    "synonyms": m.synonyms,
                }
                for m in hits
            ]
        }

    # ---------- join 连通性（护栏用） ----------

    def joinable(self, used: list[str]) -> bool:
        """used 里的表是否通过已知 join 边互相连通。"""
        known = [t for t in set(used) if t in self.tables]
        if len(known) <= 1:
            return True
        seen = {known[0]}
        frontier = [known[0]]
        while frontier:
            cur = frontier.pop()
            for a, b in self.edges:
                if a == cur and b in known and b not in seen:
                    seen.add(b)
                    frontier.append(b)
                elif b == cur and a in known and a not in seen:
                    seen.add(a)
                    frontier.append(a)
        return set(known) <= seen


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    with open(SEMANTIC_DIR / "tables.yaml", encoding="utf-8") as f:
        tables_raw = yaml.safe_load(f)["tables"]
    with open(SEMANTIC_DIR / "metrics.yaml", encoding="utf-8") as f:
        metrics_raw = yaml.safe_load(f)["metrics"]

    tables = [
        TableSpec(
            name=t["name"],
            display=t["display"],
            description=t["description"],
            synonyms=t.get("synonyms", []),
            columns=[
                ColumnSpec(
                    name=c["name"], type=c.get("type", ""), desc=c.get("desc", ""),
                    pii=bool(c.get("pii", False)), sample=c.get("sample"),
                )
                for c in t["columns"]
            ],
            joins=_norm_joins(t.get("joins", [])),
        )
        for t in tables_raw
    ]
    metrics = [
        MetricSpec(
            name=m["name"], display_name=m["display_name"], description=m["description"],
            expression=m["expression"], grain=m.get("grain", ""), dimensions=m.get("dimensions", []),
            synonyms=m.get("synonyms", []), required_filters=m.get("required_filters", []),
        )
        for m in metrics_raw
    ]
    tables.extend(_load_user_tables())
    _overlay_samples(tables)
    return Catalog(tables, metrics)


def _overlay_samples(tables: list["TableSpec"]) -> None:
    """维度值样本注入：用 data/samples.json 的真实取值覆盖列 sample。

    生成样本来自真实数据（gen_samples / 上传入库时自动采样），永远比
    YAML 手写的演示样本更可信，因此生成值存在即覆盖；未生成的列
    （PII / 高基数自由文本）保留原 sample 不动。
    """
    data = load_samples()
    if not data:
        return
    for t in tables:
        col_samples = data.get(t.name)
        if not isinstance(col_samples, dict):
            continue
        for c in t.columns:
            vals = col_samples.get(c.name)
            if isinstance(vals, list) and vals:
                c.sample = vals


def _load_user_tables() -> list["TableSpec"]:
    """合并用户上传表（data/uploads/registry.json，由 app/uploads.py 维护）。

    上传表进入同一个 Catalog，意味着：护栏「未知表拦截 / PII 拦截 / LIMIT
    强制」对它们自动生效——语义层是唯一白名单来源，护栏代码零特判。
    """
    reg = settings.root / "data" / "uploads" / "registry.json"
    if not reg.exists():
        return []
    try:
        entries = json.loads(reg.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for u in entries:
        out.append(TableSpec(
            name=u["name"],
            display=u.get("display") or u["name"],
            description=u.get("description", ""),
            synonyms=u.get("synonyms", []),
            columns=[
                ColumnSpec(
                    name=c["name"], type=c.get("type", ""), desc=c.get("desc", ""),
                    pii=bool(c.get("pii", False)), sample=c.get("sample"),
                )
                for c in u.get("columns", [])
            ],
            joins=[],
        ))
    return out


def reset_catalog() -> None:
    """上传/删除表后清缓存；下次 get_catalog() 重建（护栏白名单动态生效）。"""
    get_catalog.cache_clear()
