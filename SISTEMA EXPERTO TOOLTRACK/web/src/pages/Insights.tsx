import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { Donut, HBarChart, Panel, ParetoChart, Progress, TrendChart, type Datum } from "../components/Charts";
import { ErrorNote } from "../components/Dialog";
import { ToolLink } from "../components/Links";
import { usePoll } from "../usePoll";

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

interface Overview {
  period: { days: number; from: string; to: string };
  kpis: Kpi[];
  insights: string[];
  daily: { date: string; requests: number; incidents: number; kits: number }[];
  downtime_by_machine: Datum[];
  response_by_shift: Datum[];
  request_mix: Datum[];
  damage_by_cause: Datum[];
  damage_by_machine: Datum[];
  damage_by_shift: Datum[];
  damage_by_moment: Datum[];
  damage_by_part: Datum[];
  damage_by_tn: Datum[];
  inventory: { total: number; status: Datum[]; in_service: Datum[] };
  tools_attention: { tool_id: string; tn: string | null; category: string | null; status: string | null; life: string | null; services: number; ground_off: number; abnormal: number; incidents: number }[];
  maintenance: { opened: number; closed: number; open_now: number; oldest_open_days: number; avg_days: number | null; results: Datum[] };
  kits: { delivered: number; avg_minutes: number | null; cancelled: number; open_now: number; by_machine: Datum[] };
  plan: { levels: Datum[]; machines_total: number; queue_hours: number; completion_pct: number | null; planned_pieces: number; produced_pieces: number; demands: number; queue_by_machine: Datum[] };
}

const PERIODS = [
  { value: 7, label: "Last 7 days" },
  { value: 30, label: "Last 30 days" },
  { value: 90, label: "Last 90 days" },
  { value: 180, label: "Last 6 months" },
  { value: 365, label: "Last year" },
];
const COST_KEY = "mira.hourCost";
const LEVEL_COLORS = ["#dc2626", "#f59e0b", "#16a34a", "#64748b", "#cbd5e1"];
const STATUS_COLORS = ["#16a34a", "#f59e0b", "#dc2626", "#64748b"];

function KpiCard({ kpi }: { kpi: Kpi }) {
  const empty = kpi.value === null;
  const delta = kpi.delta_pct;
  const better = delta !== null && kpi.good !== null && (kpi.good === "down" ? delta < 0 : delta > 0);
  const worse = delta !== null && kpi.good !== null && (kpi.good === "down" ? delta > 0 : delta < 0);
  return (
    <div className="kpi-card">
      <div className="kpi-label">{kpi.label}</div>
      <div className="kpi-value">
        {empty ? "—" : kpi.value}
        {!empty && kpi.unit && <small>{kpi.unit === "%" ? "%" : ` ${kpi.unit}`}</small>}
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

export function InsightsPage() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cost, setCost] = useState(localStorage.getItem(COST_KEY) ?? "");

  const load = useCallback(async () => {
    try {
      setData(await api<Overview>("GET", `/insights/overview?days=${days}`));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the dashboard.");
    }
  }, [days]);
  usePoll(load, 120000);

  useEffect(() => {
    setData(null);
    void load();
  }, [load]);

  function changeCost(value: string) {
    setCost(value);
    localStorage.setItem(COST_KEY, value);
  }

  const rate = Number(cost);
  const downtime = data?.kpis.find((kpi) => kpi.key === "downtime");
  const downtimeCost = downtime?.value != null && rate > 0 ? Math.round(downtime.value * rate) : null;
  const savedCost = downtime?.value != null && downtime.previous != null && downtime.delta_pct !== null && rate > 0 ? Math.round((downtime.previous - downtime.value) * rate) : null;

  return (
    <section className="page insights">
      <div className="board-bar">
        <div>
          <h1>Management dashboard</h1>
          {data && (
            <p className="muted small">
              {data.period.from} to {data.period.to} · compared with the previous {data.period.days} days
            </p>
          )}
        </div>
        <div className="bar-right no-print">
          <label className="cost-input">
            Cost per machine-hour (USD)
            <input inputMode="decimal" value={cost} onChange={(event) => changeCost(event.target.value)} placeholder="optional" />
          </label>
          <select value={days} onChange={(event) => setDays(Number(event.target.value))} aria-label="Period">
            {PERIODS.map((period) => (
              <option key={period.value} value={period.value}>
                {period.label}
              </option>
            ))}
          </select>
          <button className="btn" onClick={() => void load()}>
            Refresh
          </button>
          <button className="btn btn-primary" onClick={() => window.print()}>
            Print / save as PDF
          </button>
        </div>
      </div>
      <ErrorNote message={error} />
      {!data && !error && <p className="muted">Calculating indicators…</p>}

      {data && (
        <>
          {downtimeCost !== null && (
            <div className="note note-info cost-banner">
              Tool-related downtime in the period: <strong>${downtimeCost.toLocaleString()}</strong> at ${rate}/machine-hour
              {savedCost !== null && savedCost !== 0 && (
                <>
                  {" · "}
                  <strong>{savedCost > 0 ? `$${savedCost.toLocaleString()} less` : `$${Math.abs(savedCost).toLocaleString()} more`}</strong> than the previous period
                </>
              )}
              .
            </div>
          )}

          <div className="kpi-grid">
            {data.kpis.map((kpi) => (
              <KpiCard key={kpi.key} kpi={kpi} />
            ))}
          </div>

          {data.insights.length > 0 && (
            <Panel title="Key findings" subtitle="Written automatically from the data of the period" wide>
              <ul className="findings">
                {data.insights.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </Panel>
          )}

          <h2 className="insight-section">Tool availability and Tool Crib response</h2>
          <div className="insight-grid">
            <Panel title="Activity" subtitle="Tool requests, damage reports and kits delivered per day" wide>
              <TrendChart
                data={data.daily}
                series={[
                  { key: "requests", label: "Tool requests", color: "#2563eb" },
                  { key: "incidents", label: "Damage reports", color: "#dc2626" },
                  { key: "kits", label: "Kits delivered", color: "#16a34a" },
                ]}
              />
            </Panel>
            <Panel title="Machine hours lost waiting for a tool" subtitle="Per machine; the number after the dot is how many positions waited">
              <HBarChart items={data.downtime_by_machine} unit=" h" color="#dc2626" />
            </Panel>
            <Panel title="Tool Crib response by shift" subtitle="Average minutes from request to someone on it (dashed mark: 15 min target)">
              <HBarChart items={data.response_by_shift} unit=" min" color="#2563eb" target={15} />
            </Panel>
            <Panel title="What Manufacturing asks for">
              <Donut items={data.request_mix} />
            </Panel>
          </div>

          <h2 className="insight-section">Damage analysis</h2>
          <div className="insight-grid">
            <Panel title="Damage causes (Pareto)" subtitle="Bars: reports · line: cumulative share. The red bar is the first thing to attack." wide>
              <ParetoChart items={data.damage_by_cause} />
            </Panel>
            <Panel title="By machine">
              <HBarChart items={data.damage_by_machine} color="#dc2626" />
            </Panel>
            <Panel title="By shift">
              <HBarChart items={data.damage_by_shift} color="#7c3aed" />
            </Panel>
            <Panel title="When it happens">
              <Donut items={data.damage_by_moment} />
            </Panel>
            <Panel title="Part numbers with most damage">
              <HBarChart items={data.damage_by_part} color="#f59e0b" />
            </Panel>
            <Panel title="Tool numbers (TN) with most damage">
              <HBarChart items={data.damage_by_tn} color="#0d9488" />
            </Panel>
          </div>

          <h2 className="insight-section">Tool health</h2>
          <div className="insight-grid">
            <Panel title="Inventory status" subtitle={`${data.inventory.total} physical tools`}>
              <Donut items={data.inventory.status} colors={STATUS_COLORS} center={String(data.inventory.total)} />
            </Panel>
            <Panel title="Tools away from the racks">
              <HBarChart items={data.inventory.in_service} color="multi" />
            </Panel>
            <Panel title="Tools that need attention" subtitle="Most sharpenings and damage reports in the period: candidates to review or retire" wide>
              {data.tools_attention.length === 0 ? (
                <p className="muted chart-empty">No data in this period.</p>
              ) : (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Tool</th>
                        <th>TN</th>
                        <th>Type</th>
                        <th>Status</th>
                        <th>Sharpenings</th>
                        <th>Ground off (in)</th>
                        <th>Abnormal</th>
                        <th>Damage reports</th>
                        <th>Life left</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.tools_attention.map((tool) => (
                        <tr key={tool.tool_id}>
                          <td>
                            <strong>
                              <ToolLink id={tool.tool_id} />
                            </strong>
                          </td>
                          <td>{tool.tn ?? "—"}</td>
                          <td>{tool.category === "punzon" ? "Punch" : tool.category === "matriz" ? "Die" : "—"}</td>
                          <td>{tool.status ?? "—"}</td>
                          <td>{tool.services}</td>
                          <td>{tool.ground_off.toFixed(3)}</td>
                          <td>{tool.abnormal || "—"}</td>
                          <td>{tool.incidents || "—"}</td>
                          <td>{tool.life ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
          </div>

          <h2 className="insight-section">Maintenance and kitting</h2>
          <div className="insight-grid">
            <Panel title="Maintenance">
              <div className="facts">
                <Fact label="Tickets opened" value={data.maintenance.opened} />
                <Fact label="Closed" value={data.maintenance.closed} />
                <Fact label="Open now" value={data.maintenance.open_now} />
                <Fact label="Average turnaround" value={data.maintenance.avg_days === null ? null : `${data.maintenance.avg_days} days`} />
                <Fact label="Oldest open" value={data.maintenance.open_now ? `${data.maintenance.oldest_open_days} days` : null} />
              </div>
              <Donut items={data.maintenance.results} colors={["#16a34a", "#dc2626", "#64748b"]} />
            </Panel>
            <Panel title="Kitting">
              <div className="facts">
                <Fact label="Kits delivered" value={data.kits.delivered} />
                <Fact label="Average time to deliver" value={data.kits.avg_minutes === null ? null : `${data.kits.avg_minutes} min`} />
                <Fact label="Cancelled" value={data.kits.cancelled} />
                <Fact label="In progress now" value={data.kits.open_now} />
              </div>
              <HBarChart items={data.kits.by_machine} unit="" color="#16a34a" />
            </Panel>
          </div>

          <h2 className="insight-section">Production plan</h2>
          <div className="insight-grid">
            <Panel title="Machines by time left on the running part" subtitle={`${data.plan.machines_total} machines`}>
              <Donut items={data.plan.levels} colors={LEVEL_COLORS} center={String(data.plan.machines_total)} />
            </Panel>
            <Panel title="Hours queued per machine" subtitle={`${data.plan.queue_hours} machine-hours in the plan`}>
              <HBarChart items={data.plan.queue_by_machine} unit=" h" color="#2563eb" />
            </Panel>
            <Panel title="Plan progress" subtitle={`${data.plan.produced_pieces.toLocaleString()} of ${data.plan.planned_pieces.toLocaleString()} planned pieces · ${data.plan.demands} demands`}>
              <Progress value={data.plan.completion_pct} label={data.plan.completion_pct === null ? "" : `${data.plan.completion_pct}% of the planned pieces are produced`} />
            </Panel>
          </div>
        </>
      )}
    </section>
  );
}
