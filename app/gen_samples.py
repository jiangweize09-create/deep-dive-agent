"""维度值样本注入 CLI：python -m app.gen_samples

把内置表与上传表低基数文本列的真实高频取值写入 data/samples.json，
search_schema 返回给 LLM 的列即带真实取值（杜绝「华东」vs「华东大区」式翻车）。
seed_data 末尾会自动跑一次；上传表确认入库时自动采样，平时无需手动重跑。
"""

from .semantic.samples import SAMPLES_PATH, rebuild_all

if __name__ == "__main__":
    result = rebuild_all()
    if not result:
        print("没有可采样的表（业务库未播种且无上传表）")
    for table, n in sorted(result.items()):
        print(f"  {table}: {n} 列已采样")
    print(f"样本已写入 {SAMPLES_PATH}")
