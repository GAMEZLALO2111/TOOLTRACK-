import type { ReactNode } from "react";

export interface Datum {
  key: string;
  label: string;
  value: number | null;
  count?: number;
  cumulative?: number;
}

export const COLORS = ["#2563eb", "#f59e0b", "#16a34a", "#dc2626", "#7c3aed", "#0d9488", "#64748b", "#db2777", "#ea580c", "#0891b2"];

function Empty() {
  return <p className="muted chart-empty">No data in this period.</p>;
}

export function Panel({ title, subtitle, children, wide = false }: { title: string; subtitle?: string; children: ReactNode; wide?: boolean }) {
  return (
    <section className={`panel insight-panel${wide ? " wide" : ""}`}>
      <h3>{title}</h3>
      {subtitle && <p className="muted small insight-sub">{subtitle}</p>}
      {children}
    </section>
  );
}

function format(value: number | null, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(digits);
}

/** Horizontal bars: the easiest way to compare machines, shifts or causes. */
export function HBarChart({ items, unit = "", color = COLORS[0], target }: { items: Datum[]; unit?: string; color?: string; target?: number }) {
  const rows = items.filter((item) => item.value !== null);
  if (rows.length === 0) return <Empty />;
  const max = Math.max(...rows.map((item) => item.value ?? 0), target ?? 0, 0.0001);
  return (
    <div className="hbars">
      {rows.map((item, index) => (
        <div className="hbar-row" key={item.key}>
          <span className="hbar-label" title={item.label}>
            {item.label}
          </span>
          <span className="hbar-track">
            <span className="hbar-fill" style={{ width: `${((item.value ?? 0) / max) * 100}%`, background: color === "multi" ? COLORS[index % COLORS.length] : color }} />
            {target !== undefined && <span className="hbar-target" style={{ left: `${(target / max) * 100}%` }} title={`Target ${target}${unit}`} />}
          </span>
          <span className="hbar-value">
            {format(item.value)}
            {unit}
            {item.count !== undefined && <small className="muted"> · {item.count}</small>}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Donut with its own legend. */
export function Donut({ items, center, colors = COLORS }: { items: Datum[]; center?: string; colors?: string[] }) {
  const rows = items.filter((item) => (item.value ?? 0) > 0);
  const total = rows.reduce((sum, item) => sum + (item.value ?? 0), 0);
  if (!total) return <Empty />;
  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  return (
    <div className="donut-wrap">
      <svg viewBox="0 0 140 140" className="donut" role="img" aria-label="Distribution">
        <circle cx="70" cy="70" r={radius} fill="none" stroke="#eef2f7" strokeWidth="22" />
        {rows.map((item, index) => {
          const length = ((item.value ?? 0) / total) * circumference;
          const circle = (
            <circle
              key={item.key}
              cx="70"
              cy="70"
              r={radius}
              fill="none"
              stroke={colors[index % colors.length]}
              strokeWidth="22"
              strokeDasharray={`${length} ${circumference - length}`}
              strokeDashoffset={-offset}
              transform="rotate(-90 70 70)"
            />
          );
          offset += length;
          return circle;
        })}
        <text x="70" y="66" textAnchor="middle" className="donut-total">
          {center ?? total}
        </text>
        <text x="70" y="84" textAnchor="middle" className="donut-caption">
          total
        </text>
      </svg>
      <ul className="legend-list">
        {rows.map((item, index) => (
          <li key={item.key}>
            <span className="swatch" style={{ background: colors[index % colors.length] }} />
            <span className="legend-name">{item.label}</span>
            <strong>{item.value}</strong>
            <small className="muted">{Math.round(((item.value ?? 0) / total) * 100)}%</small>
          </li>
        ))}
      </ul>
    </div>
  );
}

export interface Series {
  key: string;
  label: string;
  color: string;
}

/** Lines over time (one point per day). */
export function TrendChart({ data, series }: { data: Record<string, number | string>[]; series: Series[] }) {
  if (data.length === 0 || series.every((s) => data.every((row) => !Number(row[s.key])))) return <Empty />;
  const width = 640;
  const height = 220;
  const pad = { left: 34, right: 12, top: 12, bottom: 26 };
  const max = Math.max(1, ...data.flatMap((row) => series.map((s) => Number(row[s.key]) || 0)));
  const top = Math.ceil(max / 4) * 4 || 4;
  const x = (index: number) => pad.left + (data.length === 1 ? 0 : (index / (data.length - 1)) * (width - pad.left - pad.right));
  const y = (value: number) => pad.top + (1 - value / top) * (height - pad.top - pad.bottom);
  const step = Math.max(1, Math.ceil(data.length / 8));
  // A 7-day smoothing keeps long periods readable.
  const smooth = (key: string) =>
    data.map((_, index) => {
      const from = Math.max(0, index - (data.length > 30 ? 6 : 0));
      const slice = data.slice(from, index + 1);
      return slice.reduce((sum, row) => sum + (Number(row[key]) || 0), 0) / slice.length;
    });
  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} className="trend" role="img" aria-label="Trend">
        {[0, 0.25, 0.5, 0.75, 1].map((fraction) => (
          <g key={fraction}>
            <line x1={pad.left} x2={width - pad.right} y1={y(top * fraction)} y2={y(top * fraction)} stroke="#e5e9f0" />
            <text x={pad.left - 6} y={y(top * fraction) + 4} textAnchor="end" className="axis">
              {format(top * fraction, 0)}
            </text>
          </g>
        ))}
        {data.map((row, index) =>
          index % step === 0 ? (
            <text key={String(row.date)} x={x(index)} y={height - 8} textAnchor="middle" className="axis">
              {String(row.date).slice(5)}
            </text>
          ) : null,
        )}
        {series.map((s) => {
          const values = smooth(s.key);
          return (
            <polyline
              key={s.key}
              fill="none"
              stroke={s.color}
              strokeWidth="2.5"
              strokeLinejoin="round"
              points={values.map((value, index) => `${x(index)},${y(value)}`).join(" ")}
            />
          );
        })}
      </svg>
      <div className="legend-inline">
        {series.map((s) => (
          <span key={s.key}>
            <span className="swatch" style={{ background: s.color }} /> {s.label}
          </span>
        ))}
        {data.length > 30 && <span className="muted small">(7-day average)</span>}
      </div>
    </div>
  );
}

/** Pareto: bars sorted by weight plus the cumulative percentage line. */
export function ParetoChart({ items }: { items: Datum[] }) {
  if (items.length === 0) return <Empty />;
  const width = 640;
  const height = 240;
  const pad = { left: 34, right: 34, top: 14, bottom: 58 };
  const max = Math.max(...items.map((item) => item.value ?? 0), 1);
  const slot = (width - pad.left - pad.right) / items.length;
  const barWidth = Math.min(46, slot * 0.62);
  const cx = (index: number) => pad.left + slot * index + slot / 2;
  const yBar = (value: number) => pad.top + (1 - value / max) * (height - pad.top - pad.bottom);
  const yPct = (value: number) => pad.top + (1 - value / 100) * (height - pad.top - pad.bottom);
  const words = (label: string) => (label.length > 13 ? [label.slice(0, 12) + "…"] : [label]);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="trend" role="img" aria-label="Pareto">
      {[0, 25, 50, 75, 100].map((pct) => (
        <g key={pct}>
          <line x1={pad.left} x2={width - pad.right} y1={yPct(pct)} y2={yPct(pct)} stroke="#e5e9f0" />
          <text x={width - pad.right + 6} y={yPct(pct) + 4} className="axis">
            {pct}%
          </text>
        </g>
      ))}
      {items.map((item, index) => (
        <g key={item.key}>
          <rect x={cx(index) - barWidth / 2} y={yBar(item.value ?? 0)} width={barWidth} height={height - pad.bottom - yBar(item.value ?? 0)} fill={index === 0 ? "#dc2626" : "#2563eb"} rx="3" />
          <text x={cx(index)} y={yBar(item.value ?? 0) - 4} textAnchor="middle" className="axis strong">
            {item.value}
          </text>
          <text x={cx(index)} y={height - pad.bottom + 14} textAnchor="middle" className="axis">
            {words(item.label).map((line, row) => (
              <tspan key={row} x={cx(index)} dy={row === 0 ? 0 : 11}>
                {line}
              </tspan>
            ))}
          </text>
        </g>
      ))}
      <polyline fill="none" stroke="#f59e0b" strokeWidth="2.5" points={items.map((item, index) => `${cx(index)},${yPct(item.cumulative ?? 0)}`).join(" ")} />
      {items.map((item, index) => (
        <circle key={item.key} cx={cx(index)} cy={yPct(item.cumulative ?? 0)} r="3.5" fill="#f59e0b" />
      ))}
    </svg>
  );
}

export function Progress({ value, label }: { value: number | null; label: string }) {
  if (value === null) return <Empty />;
  return (
    <div>
      <div className="meter" aria-label={`${value}%`}>
        <div style={{ width: `${Math.min(100, value)}%` }} />
      </div>
      <p className="muted small">{label}</p>
    </div>
  );
}
