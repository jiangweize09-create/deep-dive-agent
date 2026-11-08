"""生成截至上个月的最近 12 个月电商演示数据（确定性随机，seed=42）。

窗口随运行日期滚动：任何月份执行都保证「上个月/近 N 个月」类问题有数据，
不会出现演示过期。用法: python -m app.seed_data
"""

import datetime as dt
import random

import duckdb

from .config import settings


def _add_months(d: dt.date, n: int) -> dt.date:
    """月份加 n（可为负），返回目标月 1 号。"""
    total = d.year * 12 + (d.month - 1) + n
    y, m0 = divmod(total, 12)
    return dt.date(y, m0 + 1, 1)


def _last_months(n: int) -> list[tuple[int, int]]:
    """截至上个月的最近 n 个完整自然月（不含本月）。"""
    first = dt.date.today().replace(day=1)
    return [(d.year, d.month) for d in (_add_months(first, -i) for i in range(n, 0, -1))]


MONTHS = _last_months(12)
# 季节系数按自然月（双11/618 高峰），不绑具体年份——数据窗口滚动时口径不变
SEASON = {1: 0.90, 2: 0.84, 3: 0.97, 4: 1.00, 5: 1.06, 6: 1.16,
          7: 1.01, 8: 1.05, 9: 1.10, 10: 1.00, 11: 1.38, 12: 1.20}
REGIONS = [("华东", 0.30), ("华南", 0.20), ("华北", 0.18), ("华中", 0.12), ("西南", 0.12), ("西北", 0.08)]
CHANNELS = [("iOS App", 0.32), ("Android App", 0.28), ("小程序", 0.22), ("Web", 0.12), ("H5", 0.06)]
CATEGORY_PRODUCTS = {
    "数码电子": [("无线耳机", 299), ("智能手表", 899), ("蓝牙音箱", 459), ("快充充电器", 129), ("充电宝", 199), ("机械键盘", 599), ("平板支架", 99)],
    "家居厨房": [("空气炸锅", 599), ("电饭煲", 399), ("玻璃保鲜盒", 89), ("厨房纸巾", 39), ("保温杯", 79), ("香薰机", 159), ("置物架", 129)],
    "服饰鞋包": [("纯棉T恤", 89), ("牛仔裤", 259), ("运动鞋", 399), ("羽绒服", 699), ("连帽卫衣", 199), ("休闲裤", 239), ("连衣裙", 329)],
    "美妆个护": [("洁面乳", 79), ("精华液", 259), ("防晒霜", 129), ("护手霜", 49), ("面膜", 99), ("洗发水", 69), ("香水", 399)],
    "运动户外": [("瑜伽垫", 129), ("可调节哑铃", 199), ("跑步鞋", 459), ("跳绳", 49), ("运动水壶", 59), ("速干T恤", 119), ("露营椅", 189)],
    "食品生鲜": [("坚果礼盒", 129), ("咖啡豆", 89), ("橄榄油", 159), ("牛奶", 49), ("零食大礼包", 39), ("茶叶礼盒", 99), ("果干", 59)],
}
STATUS = [("completed", 0.78), ("pending", 0.09), ("refunded", 0.08), ("cancelled", 0.05)]
LEVELS = [("普通", 0.60), ("白银", 0.28), ("黄金", 0.12)]


def wchoice(rng: random.Random, pairs):
    r = rng.random()
    acc = 0.0
    for value, weight in pairs:
        acc += weight
        if r <= acc:
            return value
    return pairs[-1][0]


def main():
    rng = random.Random(42)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    if settings.db_path.exists():
        settings.db_path.unlink()

    conn = duckdb.connect(str(settings.db_path))
    conn.execute("CREATE TABLE regions(id INTEGER, name VARCHAR)")
    conn.execute("CREATE TABLE channels(id INTEGER, name VARCHAR)")
    conn.execute("CREATE TABLE categories(id INTEGER, name VARCHAR)")
    conn.execute("CREATE TABLE users(id INTEGER, name VARCHAR, region_id INTEGER, level VARCHAR, phone VARCHAR, email VARCHAR, register_date DATE)")
    conn.execute("CREATE TABLE products(id INTEGER, sku VARCHAR, name VARCHAR, category_id INTEGER, price DECIMAL(10,2))")
    conn.execute("CREATE TABLE orders(id INTEGER, order_no VARCHAR, user_id INTEGER, product_id INTEGER, channel_id INTEGER, region_id INTEGER, amount DECIMAL(10,2), status VARCHAR, created_at TIMESTAMP)")

    conn.executemany("INSERT INTO regions VALUES (?, ?)", [(i + 1, r[0]) for i, r in enumerate(REGIONS)])
    conn.executemany("INSERT INTO channels VALUES (?, ?)", [(i + 1, c[0]) for i, c in enumerate(CHANNELS)])

    region_ids = list(range(1, len(REGIONS) + 1))
    channel_ids = list(range(1, len(CHANNELS) + 1))

    # 类目与商品
    cat_rows, prod_rows = [], []
    for ci, (cat, items) in enumerate(CATEGORY_PRODUCTS.items(), start=1):
        cat_rows.append((ci, cat))
        for pi, (pname, price) in enumerate(items, start=1):
            prod_rows.append((len(prod_rows) + 1, f"SKU-{ci:02d}{pi:02d}", pname, ci, price))
    conn.executemany("INSERT INTO categories VALUES (?, ?)", cat_rows)
    conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?)", prod_rows)
    prod_ids = [p[0] for p in prod_rows]

    # 用户（含 PII 字段：phone / email —— 护栏演示用）
    # 注册日期锚点同样滚动：本月往前 18 个月起，跨度 545 天（约 1 年半）
    reg_anchor = _add_months(dt.date.today().replace(day=1), -18)
    user_rows = []
    for i in range(1, 1201):
        reg = region_ids[wchoice_index(rng, REGIONS)]
        reg_date = reg_anchor + dt.timedelta(days=rng.randint(0, 545))
        user_rows.append((
            i, f"用户{i:04d}", reg, wchoice(rng, LEVELS),
            f"138{rng.randint(10000000, 99999999)}", f"user{i}@demo.cn", reg_date,
        ))
    conn.executemany("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)", user_rows)

    # 订单：12 个月，带增长与季节性
    order_rows = []
    seq = 0
    for mi, (y, m) in enumerate(MONTHS):
        n = int(520 * (1.045 ** mi) * SEASON[m])
        days_in_month = (dt.date(y + (m == 12), (m % 12) + 1, 1) - dt.date(y, m, 1)).days
        for _ in range(n):
            seq += 1
            qty = wchoice(rng, [(1, 0.62), (2, 0.28), (3, 0.10)])
            pid = rng.choice(prod_ids)
            price = next(p[4] for p in prod_rows if p[0] == pid)
            order_rows.append((
                seq, f"SO{y}{m:02d}{seq:06d}", rng.randint(1, 1200), pid,
                rng.choice(channel_ids), region_ids[wchoice_index(rng, REGIONS)],
                round(float(price) * qty, 2), wchoice(rng, STATUS),
                dt.datetime(y, m, rng.randint(1, days_in_month), rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59)),
            ))
    conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", order_rows)
    conn.execute("CHECKPOINT")

    # 摘要
    total = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    gmv = conn.execute("SELECT SUM(amount) FROM orders WHERE status='completed'").fetchone()[0]
    first, last = conn.execute("SELECT MIN(created_at), MAX(created_at) FROM orders").fetchone()
    print(f"已生成演示数据 -> {settings.db_path}")
    print(f"订单 {total} 条 | 完成订单 GMV 合计 ¥{gmv:,.0f} | 时间范围 {first:%Y-%m-%d} ~ {last:%Y-%m-%d}")
    monthly = conn.execute(
        "SELECT strftime(created_at,'%Y-%m') m, COUNT(*), SUM(amount) FILTER (WHERE status='completed') "
        "FROM orders GROUP BY 1 ORDER BY 1"
    ).fetchall()
    for m, c, g in monthly:
        print(f"  {m}  订单 {c:5d}  GMV ¥{g:,.0f}")
    conn.close()

    # 维度值样本注入：播种后立即采样（python -m app.gen_samples 可随时重算）。
    # 必须在 conn.close() 之后：DuckDB 同进程不允许对同一文件同时开读写与只读连接
    try:
        from .semantic.samples import rebuild_all
        result = rebuild_all()
        print(f"维度值样本 -> data/samples.json（{len(result)} 表 / {sum(result.values())} 列）")
    except Exception as e:
        print(f"样本生成失败（不影响演示数据本身）：{e}")


def wchoice_index(rng: random.Random, pairs) -> int:
    """按权重选下标。"""
    r = rng.random()
    acc = 0.0
    for i, (_, w) in enumerate(pairs):
        acc += w
        if r <= acc:
            return i
    return len(pairs) - 1


if __name__ == "__main__":
    main()
