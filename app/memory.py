"""语义偏好记忆：跨会话记住用户的口径习惯 / 分析习惯 / 背景与关注（SQLite）。

三类记忆（对应设置页「记忆与进化」的三个分区）：
- caliber  口径偏好：销售额=净销售额 —— 写入后口径反问 HITL 永久跳过该术语
- workflow 分析习惯：出报告前先做数据质量体检 —— 主 Agent 每轮注入，改变默认行为
- profile  背景与关注：我负责华南区运营 / 最近关注退款率 —— 注入让回答贴近语境

写入路径（全部可溯源 source）：
- hitl：口径卡上明确选择的那一刻自动记（用户亲手确认，置信度最高）
- chat：用户说「以后都…/记住…」时主 Agent 调 save_memory 工具
- manual：设置页手动增改

读取路径：note_for_prompt() 生成一条紧凑 SystemMessage 随 make_input 注入；
pref_keys() 并入 clarify 的已确认集合。记忆是全局的（不分会话），
设置页可见、可编辑、可导入导出、可一键重置——不搞黑箱。
"""

import json
import sqlite3
import threading

from datetime import datetime

from .config import settings

DB_PATH = settings.root / "data" / "memory.db"

CATEGORIES = ("caliber", "workflow", "profile", "custom")
_CATEGORY_LABELS = {
    "caliber": "口径偏好", "workflow": "分析习惯",
    "profile": "背景与关注", "custom": "自定义指令",
}

_KEY_MAX = 40        # key 是简短名词，超长截断
_VALUE_MAX = 300
_NOTE_MAX_CHARS = 800   # 注入提示词的总量上限（上下文经济性）

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS prefs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'manual',
        updated_at TEXT NOT NULL,
        UNIQUE(category, key)
    )"""
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()


def is_enabled() -> bool:
    """个性化记忆总开关（设置页可关）。关闭后：不注入偏好、口径恢复每次反问、save_memory 停用。"""
    with _lock:
        conn = _connect()
        try:
            _ensure(conn)
            row = conn.execute("SELECT value FROM meta WHERE key = 'enabled'").fetchone()
            return row is None or row[0] == "1"   # 未设置 = 默认开启
        finally:
            conn.close()


def set_enabled(on: bool) -> None:
    with _lock:
        conn = _connect()
        try:
            _ensure(conn)
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('enabled', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("1" if on else "0",),
            )
            conn.commit()
        finally:
            conn.close()


def save_pref(category: str, key: str, value: str, source: str = "manual") -> dict:
    """按 (category, key) upsert：同一条偏好永远只有一行，新值覆盖旧值。"""
    if category not in CATEGORIES:
        raise ValueError(f"未知记忆类别：{category}")
    key = str(key or "").strip()[:_KEY_MAX]
    value = str(value or "").strip()[:_VALUE_MAX]
    if not key or not value:
        raise ValueError("偏好key与value均不能为空")
    now = datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _connect()
        try:
            _ensure(conn)
            conn.execute(
                """INSERT INTO prefs (category, key, value, source, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(category, key)
                   DO UPDATE SET value = excluded.value, source = excluded.source,
                                 updated_at = excluded.updated_at""",
                (category, key, value, source, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM prefs WHERE category = ? AND key = ?", (category, key)
            ).fetchone()
        finally:
            conn.close()
    return dict(row)


def list_prefs() -> list[dict]:
    with _lock:
        conn = _connect()
        try:
            _ensure(conn)
            rows = conn.execute(
                "SELECT * FROM prefs ORDER BY category, updated_at DESC").fetchall()
        finally:
            conn.close()
    return [dict(r) for r in rows]


def update_pref(pid: int, value: str) -> bool:
    value = str(value or "").strip()[:_VALUE_MAX]
    if not value:
        return False
    with _lock:
        conn = _connect()
        try:
            _ensure(conn)
            cur = conn.execute(
                "UPDATE prefs SET value = ?, source = 'manual', updated_at = ? WHERE id = ?",
                (value, datetime.now().isoformat(timespec="seconds"), int(pid)),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def delete_pref(pid: int) -> bool:
    with _lock:
        conn = _connect()
        try:
            _ensure(conn)
            cur = conn.execute("DELETE FROM prefs WHERE id = ?", (int(pid),))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def clear() -> int:
    """一键重置（设置页「重置」按钮 / 冒烟自清理）。"""
    with _lock:
        conn = _connect()
        try:
            _ensure(conn)
            n = conn.execute("SELECT COUNT(*) FROM prefs").fetchone()[0]
            conn.execute("DELETE FROM prefs")
            conn.commit()
            return int(n)
        finally:
            conn.close()


def pref_keys() -> set:
    """已记住口径的术语集合：clarify 的 already 集合并入它，问过一次就不再问。
    总开关关闭时返回空集（口径恢复每次反问）。"""
    if not is_enabled():
        return set()
    return {p["key"] for p in list_prefs() if p["category"] == "caliber"}


def note_for_prompt() -> str:
    """编译成注入主 Agent 的紧凑提示（空记忆或开关关闭返回空串，零 token 开销）。"""
    if not is_enabled():
        return ""
    prefs = list_prefs()
    if not prefs:
        return ""
    lines = []
    for cat in CATEGORIES:
        items = [p for p in prefs if p["category"] == cat]
        if not items:
            continue
        lines.append(
            f"· {_CATEGORY_LABELS[cat]}：" + "；".join(f"{p['key']}={p['value']}" for p in items)
        )
    if not lines:
        return ""
    note = "【用户偏好记忆】（跨会话生效，可在设置页「记忆」中管理；与本轮请求冲突时以用户本轮表述为准）\n" + "\n".join(lines)
    return note[:_NOTE_MAX_CHARS]


def make_save_memory_tool():
    """主 Agent 的记忆写入工具：用户表达长期偏好时调用（与 rag_retrieve 同级的直连工具）。"""
    from langchain_core.tools import tool

    @tool
    async def save_memory(category: str, key: str, value: str) -> str:
        """把用户表达的长期偏好写入记忆（跨会话生效，用户可在设置页管理）。
        category 四选一：caliber=指标口径（如「销售额指净销售额」）、
        workflow=分析习惯（如「出报告前先做数据质量体检」）、
        profile=背景与关注（如「我负责华南区运营」「最近关注退款率」）、
        custom=自定义指令（如「每份报告末尾附风险提示章节」）。
        仅当用户表达长期偏好（「以后都…」「记住…」「默认…」「我们说的X指…」）时调用；
        只影响本轮的一次性指令不要保存。key 传简短名词（20 字内），value 完整转述偏好内容。"""
        if not is_enabled():
            return json.dumps({"ok": False,
                               "reason": "个性化记忆已关闭（设置 → 个性化 中可开启）",
                               "suggestion": "请先在设置页开启记忆开关，或让用户手动在设置页添加偏好"},
                              ensure_ascii=False)
        try:
            row = save_pref(category, key, value, source="chat")
            return json.dumps({"ok": True, "pref": row,
                               "message": f"已记住：{row['key']}={row['value']}"},
                              ensure_ascii=False)
        except Exception as e:
            return json.dumps({"ok": False, "reason": str(e)[:200],
                               "suggestion": "category 须为 caliber/workflow/profile，key/value 不能为空"},
                              ensure_ascii=False)

    return save_memory
