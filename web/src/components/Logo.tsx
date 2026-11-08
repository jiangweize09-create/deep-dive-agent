/**
 * 品牌标识「Deep Dive」· 霓虹版：深紫底 + 发光边框 + 中央数据光柱（双白珠）+ 渐变波澜。
 * 造型参照用户提供的霓虹音频图标（squircle + 光束 + 双波）。
 *
 * LogoMark：纯图形（霓虹光束 + 双波），放在深紫底上用（消息头像 / hero 徽标 / 徽标容器）。
 * Logo：自带深紫底 + 发光内框的圆角方块（squircle 32%），独立使用（侧栏品牌位）。
 */

export function LogoMark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <defs>
        <linearGradient id="lgBeam" x1="16" y1="5" x2="16" y2="23" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#FBCFE8" />
          <stop offset="0.45" stopColor="#E879F9" />
          <stop offset="1" stopColor="#A855F7" />
        </linearGradient>
        <linearGradient id="lgWave1" x1="3" y1="20" x2="29" y2="22" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#FF9AD5" />
          <stop offset="0.55" stopColor="#C084FC" />
          <stop offset="1" stopColor="#818CF8" />
        </linearGradient>
        <linearGradient id="lgWave2" x1="3" y1="24" x2="29" y2="26" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#7EF29A" />
          <stop offset="0.6" stopColor="#A78BFA" />
          <stop offset="1" stopColor="#C084FC" />
        </linearGradient>
      </defs>

      {/* 背景淡光柱 */}
      <rect x="7" y="10" width="0.9" height="12" rx="0.45" fill="#C084FC" opacity="0.22" />
      <rect x="10.5" y="7" width="0.9" height="15" rx="0.45" fill="#C084FC" opacity="0.3" />
      <rect x="21" y="8" width="0.9" height="14" rx="0.45" fill="#C084FC" opacity="0.26" />
      <rect x="24.5" y="11" width="0.9" height="11" rx="0.45" fill="#C084FC" opacity="0.18" />

      {/* 中央数据光束：光晕 + 主柱 */}
      <rect x="13.6" y="5" width="4.8" height="18" rx="2.4" fill="#C084FC" opacity="0.28" />
      <rect x="15" y="5.4" width="2" height="17.2" rx="1" fill="url(#lgBeam)" />
      {/* 双白珠：Deep Dive 之"目" */}
      <circle cx="16" cy="9.6" r="1.15" fill="#FFFFFF" />
      <circle cx="16" cy="12.4" r="1.15" fill="#FFFFFF" />
      {/* 光束底座 */}
      <ellipse cx="16" cy="22.6" rx="3.1" ry="0.8" fill="#C084FC" opacity="0.55" />

      {/* 前波（粉 → 紫 → 蓝紫） */}
      <path
        d="M3 21.6c2.6-2.9 4.8-4.3 6.7-4.3 2.4 0 3.9 2.5 6 2.5 2.3 0 4.3-2.2 6.4-4.4 1.9-2 3.9-3.3 5.9-3.3"
        stroke="url(#lgWave1)"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
      {/* 后波（绿 → 紫，更靠下） */}
      <path
        d="M3.5 24.4c2.4-2.2 4.4-3.2 6.1-3.2 2.2 0 3.6 1.9 5.6 1.9 2.1 0 3.9-1.7 5.8-3.3 1.7-1.4 3.4-2.2 5-2.2"
        stroke="url(#lgWave2)"
        strokeWidth="1.2"
        strokeLinecap="round"
        opacity="0.9"
      />
    </svg>
  );
}

export default function Logo({
  size = 38,
  radius,
  shadow = true,
}: {
  size?: number;
  radius?: number;
  shadow?: boolean;
}) {
  // 圆角方形（squircle 比例 32%）：方中带圆
  const r = radius ?? Math.round(size * 0.32);
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: r,
        background: "linear-gradient(150deg,#2E1860 0%,#160B2E 60%,#0E0620 100%)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
        boxShadow: shadow ? "0 8px 24px rgba(88,28,135,.5)" : undefined,
      }}
    >
      {/* 霓虹发光内框（参照图中的发光描边方框） */}
      <div
        style={{
          width: size * 0.82,
          height: size * 0.82,
          borderRadius: size * 0.26,
          border: `${Math.max(1.2, size * 0.045).toFixed(1)}px solid #D8B4FE`,
          boxShadow:
            "0 0 8px rgba(192,132,252,.55), inset 0 0 8px rgba(192,132,252,.28)",
          background:
            "radial-gradient(120% 90% at 50% 0%, rgba(168,85,247,.30) 0%, rgba(18,10,38,0) 70%)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          overflow: "hidden",
        }}
      >
        <LogoMark size={Math.round(size * 0.94)} />
      </div>
    </div>
  );
}
