"""报告模块：洞察计算（纯代码）、产物仓库、Excel 导出、单文件 HTML 报告。

viz/报告子 Agent 的「洞察」不靠 LLM 自由发挥：insights.py 用确定性统计
（趋势/环比/异常/集中度/缺失值）从真实查询结果算出洞察条目，LLM 只负责
选择图表与组织语言 —— 数字全部来自代码，答辩时经得起追问。
"""

from .assemble import build_report_html  # noqa: F401
from .excel import export_excel_core  # noqa: F401
from .insights import compute_insights  # noqa: F401
from .store import current_report, register_report  # noqa: F401
