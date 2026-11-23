import { useEffect, useMemo, useState } from "react";
import { api, ApiError, download } from "../api";
import { ErrorNote } from "../components/Dialog";
import { MachineLink } from "../components/Links";

type Status = "ok" | "risk" | "blocked";
interface FgOption {
  id: number;
  numero_fg: string;
  description?: string | null;
  descripcion?: string | null;
  parts: { numero_parte: string; piezas: number }[];
}
interface BuyRow {
  tn: string;
  category: "punzon" | "matriz";
  description: string;
  need: number;
  free: number;
  returning: number;
  damaged: number;
  elsewhere: number;
  at_minimum: number;
  missing: number;
  to_buy: number;
  parts: string[];
}
interface CtbResult {
  fg: { numero_fg: string; description: string | null; customer: string | null };
  weekly: number;
  verdict: "ready" | "risk" | "not_ready";
  headroom_fg: number | null;
  alternative: { counts: Record<string, number>; units_to_buy: number; distinct_tn: number; machines_status: Status; max_load_pct: number } | null;
  params: Record<string, number>;
  pillars: {
    programs: { status: Status; ok: number; total: number };
    machines: { status: Status; chosen: Record<string, number>; found: number; max_load_pct: number; avg_load_pct: number; minimum: Record<string, number>; extra: Record<string, number>; short_pieces: number; hours_needed: Record<string, number> };
    tools: { status: Status; distinct_tn: number; units_to_buy: number; units_missing: number; at_minimum: number; units_needed: number };
  };
  parts: { part: string; per_fg: number; pieces_week: number; placed: number; types: string[]; uph: Record<string, number>; thickness: string | null; machines: Record<string, number>; status: string; issues: string[]; tools_missing: number }[];
  machines: { code: string; type: string; capacity_h: number; committed_h: number; used_h: number; load_pct: number; items: { part: string; pieces: number; hours: number; setup_h: number }[] }[];
  buy: BuyRow[];
  at_minimum: BuyRow[];
  blockers: { kind: "program" | "machine" | "tool"; text: string }[];
  warnings: string[];
}

const VERDICT = {
  ready: { icon: "✔", title: "CLEAR TO BUILD", note: "Programs, machines and tools cover the demand." },
  risk: { icon: "⚠", title: "CLEAR TO BUILD WITH RISKS", note: "It can be built, but review the points below." },
  not_ready: { icon: "✖", title: "NOT CLEAR TO BUILD", note: "Something is missing. Here is exactly what." },
} as const;
const PART_STATUS: Record<string, { label: string; cls: string }> = {
  ready: { label: "Ready", cls: "ctb-ok" },
  no_tools: { label: "Missing tools", cls: "ctb-blocked" },
  no_program: { label: "No program", cls: "ctb-blocked" },
  no_machine: { label: "No machine", cls: "ctb-blocked" },
  no_capacity: { label: "No capacity", cls: "ctb-blocked" },
};
const STATUS_TEXT: Record<Status, string> = { ok: "OK", risk: "Watch", blocked: "Missing" };
const message = (caught: unknown, fallback: string) => (caught instanceof ApiError ? caught.message : fallback);
const fmt = (value: number) => value.toLocaleString();

function Stepper({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return (
    <div className="ctb-stepper">
      <span className="muted small">{label}</span>
      <div>
        <button className="btn" onClick={() => onChange(Math.max(0, value - 1))} aria-label={`Fewer ${label}`}>
          −
        </button>
        <strong>{value}</strong>
        <button className="btn" onClick={() => onChange(Math.min(40, value + 1))} aria-label={`More ${label}`}>
          +
        </button>
      </div>
    </div>
  );
}

function Pillar({ title, status, big, small, children }: { title: string; status: Status; big: string; small: string; children?: React.ReactNode }) {
  return (
    <div className={`ctb-pillar ctb-${status}`}>
      <div className="ctb-pillar-head">
        <span>{title}</span>
        <span className={`ctb-badge ctb-${status}`}>{STATUS_TEXT[status]}</span>
      </div>
      <div className="ctb-big">{big}</div>
      <div className="muted small">{small}</div>
      {children}
    </div>
  );
}

export function CtbTab() {
  const [fgs, setFgs] = useState<FgOption[]>([]);
  const [fg, setFg] = useState("");
  const [weekly, setWeekly] = useState("100");
  const [shifts, setShifts] = useState("3");
  const [hours, setHours] = useState("8");
  const [days, setDays] = useState("5");
  const [efficiency, setEfficiency] = useState("85");
  const [count4, setCount4] = useState(4);
  const [count2, setCount2] = useState(1);
  const [count45, setCount45] = useState(0);
  const [board, setBoard] = useState(true);
  const [result, setResult] = useState<CtbResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [onlyTools, setOnlyTools] = useState(false);

  useEffect(() => {
    api<FgOption[]>("GET", "/planeacion/fg")
      .then((rows) => {
        setFgs(rows);
        setFg((current) => current || rows[0]?.numero_fg || "");
      })
      .catch((caught) => setError(message(caught, "Could not load the FGs")));
    api<{ params: Record<string, number> }>("GET", "/planeacion/maquinas")
      .then((data) => {
        setHours(String(data.params.horas_turno ?? 8));
        setDays(String(data.params.dias_semana ?? 5));
        setEfficiency(String(data.params.eficiencia_pct ?? 85));
      })
      .catch(() => undefined);
  }, []);

  const query = useMemo(
    () =>
      new URLSearchParams({
        fg,
        cantidad: weekly,
        c4i: String(count4),
        c2i: String(count2),
        c45: String(count45),
        turnos: shifts,
        horas: hours,
        dias: days,
        eficiencia: efficiency,
        tablero: board ? "1" : "0",
      }).toString(),
    [fg, weekly, count4, count2, count45, shifts, hours, days, efficiency, board],
  );

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api<CtbResult>("GET", `/planeacion/ctb?${query}`));
    } catch (caught) {
      setResult(null);
      setError(message(caught, "Could not check clear to build"));
    } finally {
      setBusy(false);
    }
  }

  async function excel() {
    try {
      await download(`/planeacion/ctb/excel?${query}`, `ctb-${fg}.xlsx`);
    } catch (caught) {
      setError(message(caught, "The Excel file could not be created"));
    }
  }

  const verdict = result ? VERDICT[result.verdict] : null;
  const grouped = useMemo(() => {
    const groups: Record<string, string[]> = { program: [], machine: [], tool: [] };
    result?.blockers.forEach((item) => groups[item.kind].push(item.text));
    return groups;
  }, [result]);
  const buy = result ? (onlyTools ? result.buy.filter((row) => row.to_buy > 0) : result.buy) : [];

  return (
    <div>
      <p className="muted small">
        Can we build a FG at a weekly rate with the programs, machines and tools we have? Pick the FG, the weekly quantity and how many machines of each type, and MIRA tells you what is ready and exactly what is missing.
      </p>
      <div className="ctb-form">
        <label>
          FG
          <select value={fg} onChange={(event) => setFg(event.target.value)} aria-label="FG">
            {fgs.map((row) => (
              <option key={row.id} value={row.numero_fg}>
                {row.numero_fg} ({row.parts.length} make items)
              </option>
            ))}
          </select>
        </label>
        <label>
          FG per week
          <input inputMode="numeric" value={weekly} onChange={(event) => setWeekly(event.target.value)} aria-label="FG per week" />
        </label>
        <label>
          Shifts
          <select value={shifts} onChange={(event) => setShifts(event.target.value)} aria-label="Shifts">
            <option value="1">1 shift</option>
            <option value="2">2 shifts</option>
            <option value="3">3 shifts</option>
          </select>
        </label>
        <label>
          Hours / shift
          <input inputMode="decimal" value={hours} onChange={(event) => setHours(event.target.value)} />
        </label>
        <label>
          Days / week
          <input inputMode="decimal" value={days} onChange={(event) => setDays(event.target.value)} />
        </label>
        <label>
          Efficiency %
          <input inputMode="decimal" value={efficiency} onChange={(event) => setEfficiency(event.target.value)} />
        </label>
        <Stepper label="4 Index" value={count4} onChange={setCount4} />
        <Stepper label="2 Index" value={count2} onChange={setCount2} />
        <Stepper label="45 Sta" value={count45} onChange={setCount45} />
        <label className="ctb-check">
          <input type="checkbox" checked={board} onChange={(event) => setBoard(event.target.checked)} /> Count hours already on the Board
        </label>
        <button className="btn btn-primary ctb-run" disabled={busy || !fg} onClick={() => void run()}>
          {busy ? "Checking…" : "Check clear to build"}
        </button>
      </div>
      <ErrorNote message={error} />

      {result && verdict && (
        <>
          <div className={`ctb-hero ctb-${result.verdict}`}>
            <div className="ctb-hero-icon">{verdict.icon}</div>
            <div className="ctb-hero-text">
              <div className="ctb-hero-title">{verdict.title}</div>
              <div>
                {fmt(result.weekly)} × {result.fg.numero_fg} per week · {result.params.turnos} shift{result.params.turnos === 1 ? "" : "s"} · {Object.entries(result.pillars.machines.chosen).filter(([, n]) => n).map(([kind, n]) => `${n}×${kind}`).join(" + ")}
              </div>
              <div className="small">{verdict.note}</div>
            </div>
            <div className="ctb-hero-actions">
              <button className="btn" onClick={() => void excel()}>
                Download .xlsx
              </button>
            </div>
          </div>

          <div className="ctb-pillars">
            <Pillar title="Programs" status={result.pillars.programs.status} big={`${result.pillars.programs.ok} / ${result.pillars.programs.total}`} small="make items with a program and UPH" />
            <Pillar
              title="Machines"
              status={result.pillars.machines.status}
              big={`${result.pillars.machines.max_load_pct}%`}
              small={`busiest machine · average ${result.pillars.machines.avg_load_pct}%`}
            >
              <div className="small ctb-lines">
                <div>
                  Minimum needed: <strong>{Object.entries(result.pillars.machines.minimum).map(([kind, n]) => `${n}×${kind}`).join(" + ") || "—"}</strong>
                </div>
                {Object.entries(result.pillars.machines.extra).map(([kind, n]) => (
                  <div key={kind} className="warn-text">
                    {n} more {kind} needed
                  </div>
                ))}
                {result.headroom_fg !== null && (
                  <div>
                    These machines hold up to <strong>~{fmt(result.headroom_fg)}</strong> FG/week
                  </div>
                )}
              </div>
            </Pillar>
            <Pillar title="Tools" status={result.pillars.tools.status} big={`${result.pillars.tools.units_to_buy}`} small={`tool units to buy · ${result.pillars.tools.distinct_tn} different TN`}>
              <div className="small ctb-lines">
                <div>{result.pillars.tools.units_needed} units needed at the same time</div>
                {result.pillars.tools.at_minimum > 0 && <div className="warn-text">{result.pillars.tools.at_minimum} TN with tools at minimum life</div>}
              </div>
            </Pillar>
          </div>

          {result.alternative && (
            <div className="note note-info">
              <strong>Tip:</strong> every machine needs its own set of tools. With the minimum of machines ({Object.entries(result.alternative.counts).map(([kind, n]) => `${n}×${kind}`).join(" + ")}, busiest at {result.alternative.max_load_pct}%) you would need to buy{" "}
              <strong>{result.alternative.units_to_buy}</strong> tool units ({result.alternative.distinct_tn} TN) instead of {result.pillars.tools.units_to_buy}.
            </div>
          )}

          {result.blockers.length > 0 && (
            <div className="ctb-missing">
              <h3 className="q-done-title">What is missing</h3>
              <div className="ctb-missing-grid">
                {(["program", "machine", "tool"] as const).map(
                  (kind) =>
                    grouped[kind].length > 0 && (
                      <div key={kind} className="ctb-missing-card">
                        <div className="ctb-missing-title">
                          {kind === "program" ? "Programs" : kind === "machine" ? "Machines" : `Tools (${grouped.tool.length})`}
                        </div>
                        <ul>
                          {grouped[kind].slice(0, kind === "tool" ? 8 : 20).map((text) => (
                            <li key={text}>{text}</li>
                          ))}
                        </ul>
                        {kind === "tool" && grouped.tool.length > 8 && <div className="muted small">…and {grouped.tool.length - 8} more in the table below.</div>}
                      </div>
                    ),
                )}
              </div>
            </div>
          )}
          {result.warnings.map((text) => (
            <div key={text} className="note note-warn">
              {text}
            </div>
          ))}

          <h3 className="q-done-title">Machines</h3>
          <div className="ctb-machines">
            {result.machines.map((machine) => (
              <div key={machine.code} className="ctb-machine">
                <div className="ctb-machine-head">
                  <strong>
                    <MachineLink code={machine.code} />
                  </strong>
                  <span className="muted small">{machine.type}</span>
                  <span className="push-right small">
                    {machine.used_h} / {machine.capacity_h} h
                  </span>
                </div>
                <div className="ctb-bar" title={`${machine.load_pct}% of the week`}>
                  <div className={machine.load_pct >= 100 ? "ctb-bar-red" : machine.load_pct >= 90 ? "ctb-bar-amber" : "ctb-bar-green"} style={{ width: `${Math.min(machine.load_pct, 100)}%` }} />
                </div>
                <div className="small muted">
                  {machine.committed_h > 0 && <span>{machine.committed_h} h already on the Board · </span>}
                  {machine.items.length === 0 ? "Nothing assigned" : machine.items.map((item) => `${item.part.replace(/^TYEH?-/, "")} ×${fmt(item.pieces)}`).join(" · ")}
                </div>
              </div>
            ))}
          </div>

          <h3 className="q-done-title">Make items of {result.fg.numero_fg}</h3>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Make item</th>
                  <th>Per FG</th>
                  <th>Pieces / week</th>
                  <th>Programs · UPH</th>
                  <th>Runs on</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {result.parts.map((part) => {
                  const status = PART_STATUS[part.status] ?? { label: part.status, cls: "ctb-risk" };
                  return (
                    <tr key={part.part}>
                      <td>
                        <strong>{part.part}</strong>
                        <div className="muted small">thickness {part.thickness ?? "—"}</div>
                      </td>
                      <td>{part.per_fg}</td>
                      <td>{fmt(part.pieces_week)}</td>
                      <td>{Object.entries(part.uph).map(([kind, value]) => `${kind}: ${value}`).join(" · ") || "—"}</td>
                      <td className="small">{Object.keys(part.machines).join(", ") || "—"}</td>
                      <td>
                        <span className={`ctb-badge ${status.cls}`}>{status.label}</span>
                        {part.tools_missing > 0 && <div className="muted small">{part.tools_missing} tool(s) missing</div>}
                        {part.issues.map((text) => (
                          <div key={text} className="warn-text small">
                            {text}
                          </div>
                        ))}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {(result.buy.length > 0 || result.at_minimum.length > 0) && (
            <>
              <div className="ctb-buy-head">
                <h3 className="q-done-title">Tools to buy</h3>
                <label className="small">
                  <input type="checkbox" checked={onlyTools} onChange={(event) => setOnlyTools(event.target.checked)} /> only what must be bought
                </label>
              </div>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>TN</th>
                      <th>Type</th>
                      <th>Needed</th>
                      <th>Usable now</th>
                      <th>In sharpening</th>
                      <th>On other machines</th>
                      <th>To buy</th>
                      <th>Make items</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...buy, ...result.at_minimum.map((row) => ({ ...row, atMin: true }))].map((row) => (
                      <tr key={`${row.tn}-${row.category}-${"atMin" in row ? "m" : "b"}`}>
                        <td>
                          <strong>{row.tn}</strong>
                          <div className="muted small">{row.description}</div>
                        </td>
                        <td>{row.category === "punzon" ? "Punch" : "Die"}</td>
                        <td>{row.need}</td>
                        <td>{row.free}</td>
                        <td>{row.returning || "—"}</td>
                        <td>{row.elsewhere || "—"}</td>
                        <td>{"atMin" in row ? <span className="ctb-badge ctb-risk">{row.at_minimum} at minimum life</span> : row.to_buy > 0 ? <span className="ctb-badge ctb-blocked">Buy {row.to_buy}</span> : <span className="ctb-badge ctb-risk">Returns from service</span>}</td>
                        <td className="small">{row.parts.join(", ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
