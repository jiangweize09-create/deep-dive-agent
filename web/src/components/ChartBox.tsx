import { useEffect, useRef } from "react";
import * as echarts from "echarts";

/**
 * ECharts 容器。
 * - 默认（bare=false）：外层 .chart-panel + 内层 .chart-box，用于对话页图表卡片
 * - bare=true：只渲染一个 div，尺寸完全交给外部类名（看板用 .db-chart / .db-chart-lg）
 *   高度由 CSS 决定，这样和旧版 300 / 240 的尺寸严格一致
 */
export default function ChartBox({
  option,
  title,
  height = 260,
  className = "chart-panel",
  bare = false,
}: {
  option: any;
  title?: string;
  height?: number;
  className?: string;
  bare?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const c = echarts.init(ref.current);
    c.setOption(option);
    const onResize = () => c.resize();
    window.addEventListener("resize", onResize);
    // 页面从 hidden 切回可见时必须重算，否则宽高按 0 初始化，图会挤成一条
    const ro = new ResizeObserver(() => {
      if (ref.current && ref.current.clientWidth > 0) c.resize();
    });
    ro.observe(ref.current);
    return () => {
      window.removeEventListener("resize", onResize);
      ro.disconnect();
      c.dispose();
    };
  }, [option]);

  if (bare) {
    return <div className={className} ref={ref} />;
  }

  return (
    <div className={className}>
      {title ? <div className="chart-title">{title}</div> : null}
      <div className="chart-box" ref={ref} style={{ height }} />
    </div>
  );
}
