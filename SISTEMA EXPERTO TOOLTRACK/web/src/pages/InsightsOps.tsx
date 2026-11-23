import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { Donut, HBarChart, Panel, TrendChart, type Datum } from "../components/Charts";
import { MachineLink } from "../components/Links";
import { go } from "../router";

interface Kpi {
  key: string;
  label: string;
  unit: string;
  good: "up" | "down" | null;
  note: string | null;
  value: number | null;
  previous: number | null;
  delta_pct: number | null;
}
interface Operations {
  kpis: Kpi[];
  production: {
    pieces: number;
    by_shift: Datum[];
    daily: { date: string; pieces: number }[];
    top_parts: Datum[];
    weeks: { week: string; planned: number; produced: number; pct: number | null }[];
    attainment_pct: number | null;
    by_fg: { fg: string; customer: string | null; planned: number; produced: number; pct: number; sets_pct: number; limiting_part: string | null }[];
    outlook: { week: string; status: string; load_pct: number | null; capacity_h: number; load_h: number; late_pieces: number }[];
  };
  utilization: {
    fleet: { capacity_h: number; run_h: number; tool_wait_h: number; disabled_h: number; idle_h: number; util_pct: number | null };
    split: Datum[];
    machines: { code: string; type: string; capacity_h: number; run_h: number; tool_wait_h: number; disabled_h: number; idle_h: number; util_pct: number | null; spells: number }[];
    disabled_now: { code: string; kind: string | null; reason: string | null; since: string | null; expected_back: string | null }[];
    disabled_events: number;
  };
  strokes: {
    total: number;
    daily: { date: string; strokes: number }[];
    top_tn: Datum[];
    levels: Datum[];
    auto_opened: number;
    auto_closed: number;
    auto_open_now: number;
    auto_avg_response: number | null;
    auto_on_target: number | null;
    auto_by_shift: Datum[];
    tools_tracked: number;
    limits_set: number;
  };
  ctb: {
    rows: { fg: string; customer: string | null; weekly: number; verdict: "ready" | "risk" | "not_ready"; machines: Record<string, number>; programs: { ok: number; total: number }; max_load_pct: number; units_to_buy: number; distinct_tn: number; at_minimum: number }[];
    ready: number;
    risk: number;
    not_ready: number;
    units_to_buy: number;
    distinct_tn: number;
  };
  traceability: {
    total: number;
    daily: { date: string; movements: number }[];
    by_group: Datum[];
    capture: Datum[];
    scan_share: number | null;
    people: Datum[];
    tools_touched: number;
    tools_total: number;
    touched_pct: number | null;
    per_tool: number | null;
  };
}

const SPLIT_COLORS = ["#16a34a", "#f59e0b", "#64748b", "#cbd5e1"];
const LEVEL_COLORS = ["#16a34a", "#f59e0b", "#dc2626", "#94a3b8"];
const VERDICT = { ready: ["Clear to build", "ctb-ok"], risk: ["With risks", "ctb-risk"], not_ready: ["Not clear", "ctb-blocked"] } as const;
const OUTLOOK = { cumple: "Meets", justo: "Tight", no_cumple: "Short" } as Record<string, string>;
const fmt = (value: number) => value.toLocaleString();
const weekLabel = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const kindText = (value: string | null) => ({ maintenance: "Maintenance", unavailable: "Unavailable", other: "Other" } as Record<string, string>)[value ?? ""] ?? value ?? "—";

function OpsKpi({ kpi }: { kpi: Kpi }) {
  const delta = kpi.delta_pct;
  const better = delta !== null && kpi.good !== null && (kpi.good === "down" ? delta < 0 : delta > 0);
  const worse = delta !== null && kpi.good !== null && (kpi.good === "down" ? delta > 0 : delta < 0);
  return (
    <div className="kpi-card ops-kpi">
      <div className="kpi-label">{kpi.label}</div>
      <div className="kpi-value">
        {kpi.value === null ? "—" : typeof kpi.value === "number" && kpi.value >= 10000 ? fmt(kpi.value) : kpi.value}
        {kpi.value !== null && kpi.unit && <small>{kpi.unit === "%" ? "%" : ` ${kpi.unit}`}</small>}
      </div>
      <div className="kpi-foot">
        {delta !== null && delta !== 0 && (
          <span className={`kpi-delta${better ? " good" : worse ? " bad" : ""}`}>
            {delta > 0 ? "▲" : "▼"} {Math.abs(delta)}%
          </span>
        )}
        {kpi.note && <span className="muted small">{kpi.note}</span>}
      </div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string | number | null }) {
  return (
    <div>
      <div className="muted small">{label}</div>
      <strong>{value === null || value === "" ? "—" : value}</strong>
    </div>
  );
}

function Gauge({ value, label }: { value: number | null; label: string }) {
  const pct = Math.max(0, Math.min(100, value ?? 0));
  const color = pct >= 95 ? "#16a34a" : pct >= 85 ? "#f59e0b" : "#dc2626";
  return (
    <div className="ops-gauge" style={{ background: `conic-gradient(${color} ${pct * 3.6}deg, #e6eaf0 0deg)` }}>
      <div>
        <strong>{value === null ? "—" : `${value}%`}</strong>
        <span className="muted small">{label}</span>
      </div>
    </div>
  );
}

function WeekBars({ weeks }: { weeks: Operations["production"]["weeks"] }) {
  const max = Math.max(1, ...weeks.map((week) => Math.max(week.planned, week.produced)));
  if (weeks.length === 0) return <p className="muted chart-empty">No weeks with a forecast in this period.</p>;
  return (
    <div className="week-bars">
      {weeks.map((week) => (
        <div key={week.week} className="week-col">
          <div className="week-pct" style={{ color: (week.pct ?? 0) >= 95 ? "#16a34a" : (week.pct ?? 0) >= 85 ? "#b7791f" : "#dc2626" }}>{week.pct === null ? "—" : `${week.pct}%`}</div>
          <div className="week-pair">
            <div className="week-bar plan" style={{ height: `${(week.planned / max) * 100}%` }} title={`Planned ${fmt(week.planned)}`} />
            <div className="week-bar made" style={{ height: `${(week.produced / max) * 100}%` }} title={`Produced ${fmt(week.produced)}`} />
          </div>
          <div className="small">{weekLabel(week.week)}</div>
          <div className="muted small">{fmt(week.produced)} / {fmt(week.planned)}</div>
        </div>
      ))}
      <div className="week-legend muted small">
        <span className="dot plan" /> planned <span className="dot made" /> produced (pieces)
      </div>
    </div>
  );
}

function StackRow({ row }: { row: Operations["utilization"]["machines"][number] }) {
  const parts = [
    { key: "run", value: row.run_h, color: "#16a34a", label: "Running" },
    { key: "wait", value: row.tool_wait_h, color: "#f59e0b", label: "Waiting for a tool" },
    { key: "off", value: row.disabled_h, color: "#64748b", label: "Disabled" },
    { key: "idle", value: row.idle_h, color: "#e2e8f0", label: "Idle" },
  ];
  return (
    <div className="stack" title={parts.map((part) => `${part.label}: ${part.value} h`).join(" · ")}>
      {parts.map((part) => (
        <span key={part.key} style={{ width: `${(part.value / Math.max(row.capacity_h, 1)) * 100}%`, background: part.color }} />
      ))}
    </div>
  );
}

export function OperationsSection({ days }: { days: number }) {
  const [data, setData] = useState<Operations | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    api<Operations>("GET", `/insights/operations?days=${days}`)
      .then((result) => {
        setData(result);
        setError(null);
      })
      .catch((caught) => setError(caught instanceof ApiError ? caught.message : "Could not load the operations indicators."));
  }, [days]);

  if (error) return <p className="note note-warn">{error}</p>;
  if (!data) return <p className="muted">Calculating operations…</p>;
  const { production, utilization, strokes, ctb, traceability } = data;
  const wait = strokes.auto_by_shift;

  return (
    <>
      <h2 className="insight-section">MIRA at a glance</h2>
      <div className="kpi-grid">
        {data.kpis.map((kpi) => (
          <OpsKpi key={kpi.key} kpi={kpi} />
        ))}
      </div>

      <h2 className="insight-section">Production and plan attainment</h2>
      <div className="insight-grid">
        <Panel title="Plan attainment" subtitle="Pieces built against the weekly FG forecast (weeks with at least 4 days in the period)">
          <div className="ops-gauge-row">
            <Gauge value={production.attainment_pct} label="of the plan" />
            <div className="facts ops-facts">
              <Fact label="Pieces produced" value={fmt(production.pieces)} />
              <Fact label="Weeks measured" value={production.weeks.length} />
            </div>
          </div>
        </Panel>
        <Panel title="Planned vs produced, week by week" wide>
          <WeekBars weeks={production.weeks} />
        </Panel>
        <Panel title="Pieces per day" subtitle="Every production capture, by the shift that ran it" wide>
          <TrendChart data={production.daily} series={[{ key: "pieces", label: "Pieces", color: "#2563eb" }]} />
        </Panel>
        <Panel title="Production by shift">
          <HBarChart items={production.by_shift} color="multi" />
        </Panel>
        <Panel title="Make items with most volume">
          <HBarChart items={production.top_parts} color="#0d9488" />
        </Panel>
        <Panel title="Attainment by FG" subtitle="Pieces vs plan, and how many complete FG sets that means; the limiting make item is what held the FG back" wide>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>FG</th>
                  <th>Customer</th>
                  <th>Attainment</th>
                  <th>Complete sets</th>
                  <th>Limiting make item</th>
                </tr>
              </thead>
              <tbody>
                {production.by_fg.map((row) => (
                  <tr key={row.fg}>
                    <td>
                      <strong>{row.fg}</strong>
                    </td>
                    <td>{row.customer ?? "—"}</td>
                    <td>
                      <div className="ops-meter">
                        <div style={{ width: `${Math.min(100, row.pct)}%`, background: row.pct >= 95 ? "#16a34a" : row.pct >= 85 ? "#f59e0b" : "#dc2626" }} />
                      </div>
                      <strong>{row.pct}%</strong>
                    </td>
                    <td>
                      {fmt(row.produced)} of {fmt(row.planned)} <span className="muted small">({row.sets_pct}%)</span>
                    </td>
                    <td className="small">{row.limiting_part ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        {production.outlook.length > 0 && (
          <Panel title="Next 4 weeks: can we meet the forecast?" subtitle="From the capacity plan (machines, hours already on the Board and the forecast)" wide>
            <div className="outlook">
              {production.outlook.map((item) => (
                <div key={item.week} className={`outlook-card plan-${item.status}`}>
                  <div className="small">Week of {weekLabel(item.week)}</div>
                  <strong>{OUTLOOK[item.status] ?? item.status}</strong>
                  <div className="muted small">{item.load_pct}% of {fmt(Math.round(item.capacity_h))} h</div>
                  {item.late_pieces > 0 && <div className="warn-text small">{fmt(Math.round(item.late_pieces))} pieces late</div>}
                </div>
              ))}
              <button className="btn" onClick={() => go("/planning/capacity")}>
                Open Planning
              </button>
            </div>
          </Panel>
        )}
      </div>

      <h2 className="insight-section">Machine utilisation and stops</h2>
      <div className="insight-grid">
        <Panel title="Where the machine hours went" subtitle={`${fmt(Math.round(utilization.fleet.capacity_h))} hours of capacity in the period`}>
          <Donut items={utilization.split} colors={SPLIT_COLORS} center={utilization.fleet.util_pct === null ? "—" : `${utilization.fleet.util_pct}%`} />
        </Panel>
        <Panel title="Machines out of service now" subtitle={`${utilization.disabled_events} stop(s) in the period`}>
          {utilization.disabled_now.length === 0 ? (
            <p className="muted chart-empty">Every machine is enabled.</p>
          ) : (
            <div className="disabled-list">
              {utilization.disabled_now.map((row) => (
                <div key={row.code} className="disabled-card">
                  <strong>
                    <MachineLink code={row.code} />
                  </strong>
                  <span className="ctb-badge ctb-blocked">{kindText(row.kind)}</span>
                  <div className="small">{row.reason ?? "No reason recorded"}</div>
                  <div className="muted small">Back around {row.expected_back ?? "—"}</div>
                </div>
              ))}
            </div>
          )}
        </Panel>
        <Panel title="Utilisation by machine" subtitle="Green running · amber waiting for a tool · gray disabled · light idle" wide>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Machine</th>
                  <th style={{ width: "46%" }}>Hours</th>
                  <th>Utilisation</th>
                  <th>Running</th>
                  <th>Tool wait</th>
                  <th>Disabled</th>
                </tr>
              </thead>
              <tbody>
                {utilization.machines.map((row) => (
                  <tr key={row.code}>
                    <td>
                      <strong>
                        <MachineLink code={row.code} />
                      </strong>
                      <div className="muted small">{row.type}</div>
                    </td>
                    <td>
                      <StackRow row={row} />
                    </td>
                    <td>
                      <strong>{row.util_pct === null ? "—" : `${row.util_pct}%`}</strong>
                    </td>
                    <td>{row.run_h} h</td>
                    <td>{row.tool_wait_h ? <span className="warn-text">{row.tool_wait_h} h</span> : "—"}</td>
                    <td>{row.disabled_h ? `${row.disabled_h} h` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <h2 className="insight-section">Tool strokes and automatic tickets</h2>
      <div className="insight-grid">
        <Panel title="Tools by stroke level" subtitle={`${strokes.tools_tracked} tools counting strokes · ${strokes.limits_set} limits set`}>
          <Donut items={strokes.levels} colors={LEVEL_COLORS} center={String(strokes.tools_tracked)} />
        </Panel>
        <Panel title="Automatic tickets" subtitle="Opened by MIRA when a tool reaches its limit: Tool Crib brings the replacement before it fails">
          <div className="facts ops-facts">
            <Fact label="Opened" value={strokes.auto_opened} />
            <Fact label="Closed" value={strokes.auto_closed} />
            <Fact label="Open now" value={strokes.auto_open_now} />
            <Fact label="Avg response" value={strokes.auto_avg_response === null ? null : `${strokes.auto_avg_response} min`} />
            <Fact label="Within 15 min" value={strokes.auto_on_target === null ? null : `${strokes.auto_on_target}%`} />
          </div>
          {wait.length > 0 && <HBarChart items={wait} unit=" min" color="#2563eb" target={15} />}
        </Panel>
        <Panel title="Strokes per day" subtitle={`${fmt(strokes.total)} strokes counted on mounted tools`} wide>
          <TrendChart data={strokes.daily} series={[{ key: "strokes", label: "Strokes", color: "#7c3aed" }]} />
        </Panel>
        <Panel title="Tool numbers with most strokes">
          <HBarChart items={strokes.top_tn} color="#7c3aed" />
        </Panel>
      </div>

      <h2 className="insight-section">Clear to build</h2>
      <div className="insight-grid">
        <Panel title="Can we build this week's FG demand?" subtitle="Programs, machines and tools checked for every FG with demand, with the machines it really needs" wide>
          <div className="summary">
            <span className="chip sum lvl-VERDE">{ctb.ready} clear to build</span>
            <span className="chip sum lvl-AMARILLO">{ctb.risk} with risks</span>
            <span className="chip sum lvl-ROJO">{ctb.not_ready} not clear</span>
            <span className="chip sum lvl-PARO">{ctb.units_to_buy} tool units to buy</span>
            <button className="btn push-right" onClick={() => go("/planning/ctb")}>
              Open Clear to build
            </button>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>FG</th>
                  <th>FG / week</th>
                  <th>Machines needed</th>
                  <th>Programs</th>
                  <th>Tools to buy</th>
                  <th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {ctb.rows.map((row) => (
                  <tr key={row.fg}>
                    <td>
                      <strong>{row.fg}</strong>
                      <div className="muted small">{row.customer ?? "—"}</div>
                    </td>
                    <td>{fmt(row.weekly)}</td>
                    <td>{Object.entries(row.machines).map(([kind, n]) => `${n}×${kind}`).join(" + ")} <span className="muted small">· busiest {row.max_load_pct}%</span></td>
                    <td>
                      {row.programs.ok} / {row.programs.total}
                    </td>
                    <td>{row.units_to_buy ? <strong className="warn-text">{row.units_to_buy} units · {row.distinct_tn} TN</strong> : row.at_minimum ? <span className="muted small">{row.at_minimum} at minimum life</span> : "—"}</td>
                    <td>
                      <span className={`ctb-badge ${VERDICT[row.verdict][1]}`}>{VERDICT[row.verdict][0]}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <h2 className="insight-section">Traceability</h2>
      <div className="insight-grid">
        <Panel title="Every tool movement is recorded" subtitle="Who moved which tool, from where to where, when and how it was captured" wide>
          <div className="facts ops-facts">
            <Fact label="Movements recorded" value={fmt(traceability.total)} />
            <Fact label="Tools touched" value={`${traceability.tools_touched} of ${traceability.tools_total} (${traceability.touched_pct}%)`} />
            <Fact label="Movements per tool" value={traceability.per_tool} />
            <Fact label="Scanned (not typed)" value={traceability.scan_share === null ? null : `${traceability.scan_share}%`} />
          </div>
          <TrendChart data={traceability.daily} series={[{ key: "movements", label: "Movements", color: "#0d9488" }]} />
        </Panel>
        <Panel title="What moved">
          <Donut items={traceability.by_group} />
        </Panel>
        <Panel title="How it was captured">
          <Donut items={traceability.capture} colors={["#16a34a", "#cbd5e1", "#f59e0b", "#2563eb"]} />
        </Panel>
        <Panel title="Most active people">
          <HBarChart items={traceability.people} color="#2563eb" />
        </Panel>
      </div>
    </>
  );
}
