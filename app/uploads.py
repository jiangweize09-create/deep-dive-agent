"""用户文件上传：数据文件入库（Agent 可查询）+ 文档入知识库（RAG 可检索）。

聊天框上传，按扩展名分流两条路线：

数据路线（.csv / .xlsx）
    解析 -> 类型推断 -> 语义层草稿（含 PII 标记）-> 用户确认 ->
    写入 data/uploads/<表名>.parquet -> 注册表登记 -> TEMP 视图挂载 ->
    语义层合并（护栏白名单动态生效，护栏代码零改动）。

    为什么是 parquet + TEMP VIEW 而不是 ATTACH 第二个 duckdb 文件：
    只读连接 ATTACH 会被强制只读（写入没地方去），且同进程内 ATTACH 过的
    文件不允许再开独立写连接（Unique file handle conflict）。
    parquet 是无锁的纯文件：入库 = 内存连接 COPY 出 parquet；
    挂载 = 只读主连接 CREATE TEMP VIEW ... read_parquet（temp 目录可写，
    主库文件保持只读，「连接层兜底」不被破坏）。
    search_path = 'main,temp' 让主库表与上传表都免前缀查询。

文档路线（.md / .txt / .docx / .pptx / .pdf）
    提取纯文本 -> 落入 data/corpus_user/（RAG 用户语料目录）->
    ensure_index() 指纹变化自动重建 -> rag_retrieve 可检索、可引用。

草稿确认制（HITL）：自动推断的 schema 不直接生效，必须用户确认后才入库——
与项目「LLM 提案、人批准」的安全哲学一致。
"""

import csv
import io
import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

import duckdb

from .config import settings
from .db import jsonify_value

UPLOADS_DIR = settings.root / "data" / "uploads"
REGISTRY_PATH = UPLOADS_DIR / "registry.json"
USER_CORPUS_DIR = settings.root / "data" / "corpus_user"
TMP_DIR = settings.root / "data" / "tmp"

DATA_EXTS = {".csv", ".xlsx"}
DOC_EXTS = {".md", ".txt", ".json", ".docx", ".pptx", ".pdf"}
MAX_BYTES = 20 * 1024 * 1024          # 20MB
MAX_ROWS = 200_000                    # 超出截断（演示规模够用，防止内存爆）
MAX_DRAFTS = 20                       # 内存里最多保留的待确认草稿
MAX_PDF_PAGES = 120                   # PDF 逐页提取很慢，封顶防止前端无限等待
MAX_DOC_CHARS = 300_000               # 单文档入语料的字符上限

# 列名 PII 启发式：保守起见只按列名判断，宁漏勿滥（误标只是多拦，不误放）
_PII_RE = re.compile(
    r"手机|电话|phone|mobile|邮箱|email|身份证|idcard|id_card|证件"
    r"|地址|address|姓名|real_?name|联系方式",
    re.IGNORECASE,
)

# 看板列角色猜测：只做预选，最终以用户在确认面板的标注为准（HITL）
_TIME_RE = re.compile(r"date|time|day|month|日期|时间", re.IGNORECASE)
_AMT_RE = re.compile(
    r"amount|amt|gmv|sales|revenue|price|金额|销售额|营收|实付|总价|单价",
    re.IGNORECASE,
)
_DIM_RE = re.compile(
    r"category|type|region|city|province|channel|product|brand|name|shop"
    r"|品类|类别|类型|地区|区域|城市|渠道|商品|品牌|店铺",
    re.IGNORECASE,
)
_NUMERIC_TYPES = {"DOUBLE", "FLOAT", "REAL", "DECIMAL", "BIGINT", "INTEGER",
                  "HUGEINT", "SMALLINT", "TINYINT", "INT", "INT32", "INT64"}


def guess_roles(columns: list[dict]) -> dict:
    """按列名/类型猜看板列角色（时间/金额/维度），仅作为确认面板的预选值。"""
    by = {c["name"]: c for c in columns}
    names = [c["name"] for c in columns]
    types = {n: str(by[n]["type"]).upper() for n in names}

    time_col = next(
        (n for n in names if _TIME_RE.search(n) or types[n].startswith(("DATE", "TIMESTAMP"))),
        None,
    )
    numeric = [n for n in names if types[n] in _NUMERIC_TYPES]
    amount_col = next((n for n in numeric if _AMT_RE.search(n)), None)
    dims = [
        n for n in names
        if n not in (time_col, amount_col) and types[n].startswith(("VARCHAR", "TEXT", "STRING"))
    ]
    dims.sort(key=lambda n: 0 if _DIM_RE.search(n) else 1)
    return {"time": time_col, "amount": amount_col, "dims": dims[:3]}

_lock = threading.Lock()
_drafts: dict[str, dict] = {}         # draft_id -> 草稿（含原始 csv 文本，确认后释放）


# ---------- 注册表（已确认入库的上传表） ----------

def _load_registry() -> list[dict]:
    if not REGISTRY_PATH.exists():
        return []
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_registry(entries: list[dict]) -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def _parquet_path(name: str) -> Path:
    return UPLOADS_DIR / f"{name}.parquet"


def _slug(stem: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z_]+", "_", stem.lower()).strip("_")
    s = re.sub(r"_+", "_", s)[:36]
    # 纯中文文件名会剥空：兜底 "data"（确认面板里可改名）
    return s if len(s) >= 2 else "data"


def _unique_table_name(stem: str) -> str:
    base = f"up_{_slug(stem)}"
    taken = {u["name"] for u in _load_registry()}
    if base not in taken:
        return base
    i = 2
    while f"{base}_{i}" in taken:
        i += 1
    return f"{base}_{i}"


def register_views(conn) -> None:
    """把注册表里的上传表挂成 TEMP 视图 + 设置 search_path（幂等，可重复调用）。

    由 app.db 在连接建立时和每次确认/删除后调用；视图名 = 语义层登记名，
    LLM 生成 `FROM up_xxx` 直接命中，护栏白名单不需要任何特判。
    """
    for u in _load_registry():
        pq = _parquet_path(u["name"])
        if not pq.exists():
            continue
        conn.execute(
            f'CREATE OR REPLACE TEMP VIEW "{u["name"]}" AS '
            f"SELECT * FROM read_parquet('{pq.as_posix()}')")
    conn.execute("SET search_path = 'main,temp'")


# ---------- 数据路线：解析 / 草稿 / 确认 / 删除 ----------

def _decode(data: bytes) -> str:
    """中文 CSV 常见 GBK：utf-8-sig -> gbk -> utf-16 逐级尝试。"""
    for enc in ("utf-8-sig", "gbk", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")   # 兜底不抛错，乱码由用户在预览时发现


def _jsonable(v):
    try:
        return jsonify_value(v)
    except Exception:
        return str(v)


def _xlsx_to_csv(data: bytes) -> tuple[str, list[str]]:
    """openpyxl 取第一个工作表转 CSV 文本（日期转 ISO 字符串，行数封顶截断）。"""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    warnings = []
    buf = io.StringIO()
    writer = csv.writer(buf)
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= MAX_ROWS:
            warnings.append(f"超过 {MAX_ROWS} 行上限，已截断")
            break
        writer.writerow(["" if v is None else
                         (v.isoformat() if hasattr(v, "isoformat") else v)
                         for v in row])
    wb.close()
    return buf.getvalue(), warnings


def _cap_csv_rows(csv_text: str) -> tuple[str, str | None]:
    """CSV 行数封顶，与 xlsx 路线的 MAX_ROWS 对齐（防超大文件全量扫型拖垮解析）。

    用 csv.reader 切行：引号字段里嵌换行的行只算一行，直接 splitlines 会数错。
    未超限返回原文（不重写，避免引号风格被 csv.writer 改动）。
    先计数再截断两遍走：单遍边写边数的话，提前 break 时拿不到总行数，警告会数错。
    """
    total = sum(1 for _ in csv.reader(io.StringIO(csv_text)))   # 含表头
    if total <= MAX_ROWS:
        return csv_text, None
    reader = csv.reader(io.StringIO(csv_text))
    buf = io.StringIO()
    # lineterminator=\n：与原文件风格一致，避免截断产物行尾变成 \r\n
    writer = csv.writer(buf, lineterminator="\n")
    for i, row in enumerate(reader):
        if i >= MAX_ROWS:
            break
        writer.writerow(row)
    return buf.getvalue(), f"超过 {MAX_ROWS} 行上限，已截断（原 {total - 1} 数据行）"


def _inspect_csv(csv_text: str) -> tuple[list[dict], int]:
    """内存 DuckDB 推断列类型 + 取样值 + 行数（sample_size=-1 全量扫型更准）。"""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TMP_DIR / f"inspect_{uuid.uuid4().hex[:8]}.csv"
    # 写字节而非 write_text：Windows 文本模式会把 \n 再翻译成 \r\n，
    # 若 csv_text 已含 \r\n（如 xlsx 转换产物）会变成 \r\r\n 导致嗅探失败
    tmp.write_bytes(csv_text.encode("utf-8"))
    con = duckdb.connect()
    try:
        esc = tmp.as_posix().replace("'", "''")
        con.execute(f"CREATE TABLE draft AS SELECT * FROM read_csv_auto('{esc}', sample_size=-1)")
        rows = con.execute("SELECT COUNT(*) FROM draft").fetchone()[0]
        cols = []
        for cname, ctype, *_ in con.execute("DESCRIBE draft").fetchall():
            samples = con.execute(
                f'SELECT DISTINCT "{cname}" FROM draft '
                f'WHERE "{cname}" IS NOT NULL LIMIT 3').fetchall()
            cols.append({
                "name": cname,
                "type": ctype,
                "desc": "",
                "pii": bool(_PII_RE.search(cname)),
                "sample": [_jsonable(r[0]) for r in samples],
            })
        return cols, rows
    finally:
        con.close()
        tmp.unlink(missing_ok=True)


def ingest_data_upload(filename: str, data: bytes) -> dict:
    """数据文件 -> 语义层草稿（不入库，等确认）。"""
    if len(data) > MAX_BYTES:
        return {"ok": False, "reason": f"文件超过 {MAX_BYTES // 1024 // 1024}MB 上限"}
    ext = Path(filename).suffix.lower()
    warnings: list[str] = []
    if ext == ".xlsx":
        csv_text, warnings = _xlsx_to_csv(data)
    else:
        csv_text, cap = _cap_csv_rows(_decode(data))
        if cap:
            warnings.append(cap)
    if not csv_text.strip():
        return {"ok": False, "reason": "文件内容为空"}
    try:
        columns, rows = _inspect_csv(csv_text)
    except Exception as e:
        return {"ok": False, "reason": f"解析失败：{e}"}
    if not columns:
        return {"ok": False, "reason": "没有解析到数据列（需要带表头的 CSV）"}

    draft_id = uuid.uuid4().hex[:12]
    with _lock:
        _drafts[draft_id] = {
            "id": draft_id,
            "filename": filename,
            "csv_text": csv_text,
            "table": _unique_table_name(Path(filename).stem),
            "display": Path(filename).stem,
            "columns": columns,
            "rows": rows,
            "ts": datetime.now().timestamp(),
        }
        if len(_drafts) > MAX_DRAFTS:               # 挤掉最早的未确认草稿
            oldest = min(_drafts.values(), key=lambda d: d["ts"])
            _drafts.pop(oldest["id"], None)
    pii_cols = [c["name"] for c in columns if c["pii"]]
    return {
        "ok": True, "kind": "data", "draft_id": draft_id,
        "table": _drafts[draft_id]["table"], "filename": filename,
        "rows": rows, "columns": columns, "warnings": warnings,
        "pii_columns": pii_cols,
        "roles_guess": guess_roles(columns),
    }


def confirm_upload(draft_id: str, table: str | None = None,
                   description: str | None = None,
                   roles: dict | None = None) -> dict:
    """草稿 -> 确认入库：写 parquet + 注册 + 挂视图 + 刷语义层缓存。

    roles 是用户在确认面板标注的看板列角色（时间/金额/维度），只收白名单内的列名，
    非法值静默剔除；不标注则不存，该表不出现在看板数据源里。
    """
    with _lock:
        d = _drafts.pop(draft_id, None)
    if d is None:
        return {"ok": False, "reason": "草稿不存在或已过期，请重新上传"}
    name = (table or d["table"]).strip()
    if not re.fullmatch(r"up_[a-z0-9_]{1,39}", name):
        return {"ok": False, "reason": "表名必须以 up_ 开头，只含小写字母/数字/下划线"}

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    tmp_csv = TMP_DIR / f"{draft_id}.csv"
    tmp_csv.write_bytes(d["csv_text"].encode("utf-8"))
    pq = _parquet_path(name)
    w = duckdb.connect()
    try:
        w.execute(
            f"COPY (SELECT * FROM read_csv_auto('{tmp_csv.as_posix()}', sample_size=-1)) "
            f"TO '{pq.as_posix()}' (FORMAT PARQUET)")
    except Exception as e:
        tmp_csv.unlink(missing_ok=True)
        return {"ok": False, "reason": f"入库失败：{e}"}
    finally:
        w.close()
    tmp_csv.unlink(missing_ok=True)

    # 维度值样本注入：低基数文本列取高频真实值（search_schema 透传给 LLM，
    # 杜绝「华东」vs「华东大区」式字面量翻车）；失败不影响入库主流程
    sampled: dict[str, list] = {}
    try:
        from .semantic.samples import generate_upload_samples
        sampled = generate_upload_samples(name)
    except Exception:
        pass
    if sampled:
        for c in d["columns"]:
            if c["name"] in sampled:
                c["sample"] = sampled[c["name"]]

    roles_v: dict | None = None
    if isinstance(roles, dict):
        col_names = {c["name"] for c in d["columns"]}
        tcol = roles.get("time") if roles.get("time") in col_names else None
        acol = roles.get("amount") if roles.get("amount") in col_names else None
        dims = [x for x in (roles.get("dims") or [])
                if x in col_names and x not in (tcol, acol)][:3]
        if tcol:
            roles_v = {"time": tcol, "amount": acol, "dims": dims}

    entry = {
        "name": name,
        "display": d["display"],
        "description": description or f"用户上传：{d['filename']}",
        "synonyms": [],
        "filename": d["filename"],
        "rows": d["rows"],
        "columns": d["columns"],
        "roles": roles_v,
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
    }
    with _lock:
        entries = [u for u in _load_registry() if u["name"] != name]
        entries.append(entry)
        _save_registry(entries)

    _refresh()
    return {"ok": True, "kind": "data", "table": name, "rows": entry["rows"]}


def remove_upload(name: str) -> dict:
    """删除上传表：摘视图 + 删 parquet + 移出注册表 + 刷缓存。"""
    entries = _load_registry()
    hit = next((u for u in entries if u["name"] == name), None)
    doc_removed = False
    if hit is None:
        # 也允许按文件名删文档
        doc = USER_CORPUS_DIR / name
        if name.endswith(".md") and doc.exists():
            doc.unlink()
            doc_removed = True
        else:
            return {"ok": False, "reason": f"未找到上传对象：{name}"}
    else:
        with _lock:
            _save_registry([u for u in entries if u["name"] != name])
        _parquet_path(name).unlink(missing_ok=True)
        from .semantic.samples import remove_table_samples
        remove_table_samples(name)   # samples.json 同步清样，不留孤儿
    _refresh()
    return {"ok": True, "kind": "doc" if doc_removed else "data", "name": name}


def list_uploads() -> dict:
    docs = []
    if USER_CORPUS_DIR.exists():
        docs = sorted(p.name for p in USER_CORPUS_DIR.glob("*.md"))
    return {"tables": _load_registry(), "docs": docs}


def attachment_note(attachments: list[dict]) -> str:
    """把本次消息附带的文件编译成一段系统提示，注入主 Agent。

    只注入「有哪些文件 + 各走哪条工具路径」的指引，不注入文件内容——
    内容按需经 rag_retrieve / run_sql 读取（上下文经济性，与句柄协议同思路）。
    没有这段提示，Agent 对刚上传的文件一无所知，会答「未收到文件」。
    """
    lines: list[str] = []
    for a in attachments or []:
        a = a or {}
        kind = a.get("kind")
        if kind == "data":
            table = str(a.get("table") or "").strip()
            if not table:
                continue
            rows = a.get("rows", "?")
            lines.append(
                f"- 数据表 {table}（来自用户上传文件「{a.get('file') or table}」，约 {rows} 行）："
                f"已入库可直接查询。涉及它的问题先 search_schema 看列结构，再按语义层纪律写 SQL")
        elif kind == "doc":
            name = str(a.get("name") or "").strip()      # 语料文件名（入库后）
            orig = str(a.get("file") or "").strip() or name  # 用户上传的原始文件名
            if not name:
                continue
            stem = Path(orig).stem
            lines.append(
                f"- 文档《{orig}》（语料名 {name}）：已录入知识库。用户问及内容/摘要/要点时，"
                f"用 rag_retrieve 检索，检索词带上文件名关键词（如「{stem}」）或文档主题词，"
                f"基于检索到的原文回答")
    if not lines:
        return ""
    head = "【用户本次消息附带以下文件，均已就绪——直接按上述路径取用，不要回复“未收到文件”】"
    return head + "\n" + "\n".join(lines)


def _refresh() -> None:
    """确认/删除后：重建主连接上的 TEMP 视图 + 清语义层缓存（护栏白名单即时生效）。"""
    from .db import refresh_upload_views
    from .semantic.loader import reset_catalog

    try:
        refresh_upload_views()
    except Exception:
        pass    # 主库未播种时跳过挂载，注册表已持久化，下次连接自动挂
    reset_catalog()


# ---------- 文档路线：提取文本 -> 用户语料 -> RAG 重建 ----------

def _extract_text(filename: str, data: bytes, warnings: list[str]) -> str:
    ext = Path(filename).suffix.lower()
    if ext in (".md", ".txt", ".json"):
        return _decode(data)
    if ext == ".docx":
        import docx   # python-docx
        d = docx.Document(io.BytesIO(data))
        parts = [p.text.strip() for p in d.paragraphs if p.text.strip()]
        for t in d.tables:                       # 表格也是内容（口径表很常见）
            for row in t.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    parts.append(" | ".join(cells))
        return "\n\n".join(parts)
    if ext == ".pptx":
        from pptx import Presentation   # python-pptx
        prs = Presentation(io.BytesIO(data))
        parts = []
        for i, slide in enumerate(prs.slides, 1):
            texts = [s.text_frame.text.strip() for s in slide.shapes
                     if s.has_text_frame and s.text_frame.text.strip()]
            if texts:
                parts.append(f"## 第 {i} 页\n\n" + "\n\n".join(texts))
        return "\n\n".join(parts)
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if getattr(reader, "is_encrypted", False):
            raise ValueError("PDF 已加密，请先解除密码再上传")
        n_pages = len(reader.pages)
        # 教程类 PDF 常上百页，逐页提取很慢（前端会一直停在"上传中"）：
        # 封顶 MAX_PDF_PAGES，超出的部分丢弃并提示
        pages = reader.pages[:MAX_PDF_PAGES]
        if n_pages > MAX_PDF_PAGES:
            warnings.append(f"PDF 共 {n_pages} 页，仅提取前 {MAX_PDF_PAGES} 页")
        parts = []
        for i, page in enumerate(pages, 1):
            try:
                t = (page.extract_text() or "").strip()
            except Exception:
                continue    # 单页损坏不拖垮整体
            if t:
                parts.append(t)
            if sum(len(p) for p in parts) > MAX_DOC_CHARS:
                warnings.append(f"文本超过 {MAX_DOC_CHARS // 1000}k 字符，已截断")
                break
        if not parts and n_pages:
            warnings.append("没有提取到文本：可能是扫描版/图片型 PDF，请改用可复制文字的 PDF")
        return "\n\n".join(parts)
    raise ValueError(f"不支持的文档格式：{ext}")


def ingest_doc_upload(filename: str, data: bytes) -> dict:
    """文档 -> 提取文本 -> 用户语料目录 -> RAG 指纹重建（立即可检索）。"""
    if len(data) > MAX_BYTES:
        return {"ok": False, "reason": f"文件超过 {MAX_BYTES // 1024 // 1024}MB 上限"}
    warnings: list[str] = []
    try:
        text = _extract_text(filename, data, warnings)
    except ImportError:
        return {"ok": False, "reason": "缺少解析库，请先安装：pip install python-docx python-pptx pypdf"}
    except Exception as e:
        return {"ok": False, "reason": f"文本提取失败：{e}"}
    if len(text.strip()) < 20:
        return {"ok": False, "reason": "没有提取到有效文本（扫描版 PDF / 纯图片页暂不支持）"}

    USER_CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(Path(filename).stem)
    out = USER_CORPUS_DIR / f"{slug}_{datetime.now():%Y%m%d%H%M%S}.md"
    out.write_text(f"# {filename}\n\n{text}\n", encoding="utf-8")

    from .rag.ingest import ensure_index
    store = ensure_index()
    chunks = sum(1 for it in store.items if it["source"] == out.name)
    return {"ok": True, "kind": "doc", "file": out.name, "warnings": warnings,
            "chars": len(text), "chunks": chunks}
