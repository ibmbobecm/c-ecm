/**
 * Small, dependency-free chart primitives for the admin audit/reporting
 * page. No charting library is installed anywhere in this app, and these
 * two shapes (a bar-over-time chart, a breakdown donut) don't need one —
 * hand-rolled CSS/flexbox and a conic-gradient keep them consistent with
 * the rest of this codebase's "no extra dependency for something this
 * simple" approach (see the hand-built Icon system).
 */

export const CHART_COLORS = [
  "#5B8DEF", "#1E8E3E", "#E8710A", "#D93025", "#9334E6",
  "#00838F", "#D6409F", "#5F6368", "#0f62fe", "#f1c21b",
];

export function BarChart({ data }: { data: { label: string; value: number }[] }) {
  const max = Math.max(1, ...data.map((d) => d.value));
  if (data.length === 0) {
    return <p className="muted" style={{ fontSize: "var(--text-sm)" }}>No data for this range.</p>;
  }
  return (
    <div className="mini-bar-chart">
      {data.map((d, i) => (
        <div key={i} className="mini-bar-col" title={`${d.label}: ${d.value}`}>
          <div className="mini-bar" style={{ height: `${Math.max((d.value / max) * 100, d.value > 0 ? 3 : 0)}%` }} />
          <div className="mini-bar-label">{d.label}</div>
        </div>
      ))}
    </div>
  );
}

export function DonutChart({
  data,
  colors = CHART_COLORS,
}: {
  data: { label: string; value: number }[];
  colors?: string[];
}) {
  const total = data.reduce((sum, d) => sum + d.value, 0);
  if (total === 0) {
    return <p className="muted" style={{ fontSize: "var(--text-sm)" }}>No data for this range.</p>;
  }
  let acc = 0;
  const stops = data
    .map((d, i) => {
      const start = (acc / total) * 360;
      acc += d.value;
      const end = (acc / total) * 360;
      return `${colors[i % colors.length]} ${start}deg ${end}deg`;
    })
    .join(", ");

  return (
    <div className="mini-donut-wrap">
      <div className="mini-donut" style={{ background: `conic-gradient(${stops})` }} />
      <div className="mini-donut-legend">
        {data.map((d, i) => (
          <div key={i} className="mini-donut-legend-row">
            <span className="mini-donut-swatch" style={{ background: colors[i % colors.length] }} />
            <span className="mini-donut-legend-label">{d.label}</span>
            <span className="muted">{d.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
