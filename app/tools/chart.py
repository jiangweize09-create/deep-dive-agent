"""图表配置生成（纯函数，无 LLM）。"""

PALETTE = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444", "#06b6d4", "#84cc16", "#f97316"]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def build_option(chart_type: str, title: str, records: list[dict], x: str, ys: list[str]) -> dict:
    xs = [str(r.get(x, "")) for r in records]

    if chart_type == "pie":
        y = ys[0]
        data = [{"name": str(r.get(x, "")), "value": _num(r.get(y, 0))} for r in records]
        return {
            "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
            "tooltip": {"trigger": "item", "formatter": "{b}: {c}（{d}%）"},
            "legend": {"bottom": 0, "type": "scroll"},
            "series": [{
                "type": "pie", "radius": ["38%", "68%"], "center": ["50%", "52%"],
                "data": data, "label": {"formatter": "{b}\n{d}%"},
                "itemStyle": {"borderColor": "#fff", "borderWidth": 2},
            }],
            "color": PALETTE,
        }

    multi = len(ys) > 1
    series = []
    for yname in ys:
        s = {"name": yname, "data": [_num(r.get(yname, 0)) for r in records],
             "smooth": True, "symbolSize": 6}
        if chart_type == "bar":
            s["type"] = "bar"
            s["itemStyle"] = {"borderRadius": [4, 4, 0, 0]}
            s["barMaxWidth"] = 36
        else:
            s["type"] = "line"
            s["areaStyle"] = {"opacity": 0.08} if not multi else {"opacity": 0.10}
            s["lineStyle"] = {"width": 2.5}
        series.append(s)

    rotate = 30 if len(xs) > 8 else 0
    option = {
        "title": {"text": title, "left": "center", "textStyle": {"fontSize": 14}},
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 56, "right": 24, "top": 44, "bottom": 46 if multi else 30},
        "xAxis": {"type": "category", "data": xs, "axisLabel": {"rotate": rotate}},
        "yAxis": {"type": "value", "splitLine": {"lineStyle": {"type": "dashed"}}},
        "series": series,
        "color": PALETTE,
    }
    if multi:
        option["legend"] = {"data": ys, "bottom": 0}
    return option
