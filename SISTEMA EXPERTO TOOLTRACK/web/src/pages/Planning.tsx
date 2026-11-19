import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { Dialog, ErrorNote } from "../components/Dialog";
import { go } from "../router";

type Tab = "capacity" | "proposal" | "fg" | "forecast" | "settings";

interface WeekSummary {
  capacity_h: number;
  load_h: number;
  prebuilt_h: number;
  utilization: number | null;
  late_h: number;
  late_pieces: number;
  unplanned_pieces: number;
  status: string;
  machines_used: number;
}
interface PartWeek {
  need: number;
  assigned: number;
  late: number;
}
interface CapacityResult {
  weeks: string[];
  params: Record<string, number>;
  summary: WeekSummary[];
  fgs: { id: number; numero_fg: string; cliente: string | null; parts: string[]; weeks: { forecast: number; status: string }[] }[];
  parts: { numero_parte: string; forecast_total: number; produced: number; board_pending: number; has_program: boolean; types: string[]; weeks: PartWeek[] }[];
  machines: { code: string; type: string; hours_week: number; configured: boolean; committed_h: number; loads: number[]; disabled?: { type: string; reason: string; expected_return: string | null } | null }[];
  warnings: string[];
}
interface FgRow {
  id: number;
  numero_fg: string;
  descripcion: string | null;
  cliente: string | null;
  bom_from: string | null;
  parts: { numero_parte: string; piezas: number; types: string[]; has_program: boolean }[];
}
interface CatalogPart {
  numero_parte: string;
  types: string[];
  family: string | null;
}
interface ForecastGrid {
  weeks: string[];
  rows: { fg_id: number; numero_fg: string; cliente: string | null; values: number[] }[];
}
interface MachineRow {
  code: string;
  type: string | null;
  configured: boolean;
  included: boolean;
  shifts: number;
  hours_per_shift: number;
  days_per_week: number;
  efficiency_pct: number;
  hours_week: number;
  thickness_min: number | null;
  thickness_max: number | null;
}
interface MachinesResult {
  machines: MachineRow[];
  params: Record<string, number>;
  defaults: Record<string, number>;
}

const STATUS_LABEL: Record<string, string> = {
  cumple: "Meets",
  justo: "Tight",
  no_cumple: "Short",
  sin_programa: "No program",
  sin_demanda: "—",
};
const WEEK_CHOICES = [4, 8, 12, 26];
const TABS: { value: Tab; label: string }[] = [
  { value: "capacity", label: "Capacity" },
  { value: "proposal", label: "Proposal" },
  { value: "fg", label: "FG and BOM" },
  { value: "forecast", label: "Forecast" },
  { value: "settings", label: "Capacity settings" },
];

function weekLabel(iso: string) {
  const date = new Date(`${iso}T00:00:00`);
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
const fmt = (value: number) => value.toLocaleString();
const message = (caught: unknown, fallback: string) => (caught instanceof ApiError ? caught.message : fallback);

function StatusChip({ status }: { status: string }) {
  return <span className={`chip plan-${status}`}>{STATUS_LABEL[status] ?? status}</span>;
}

export function PlanningPage({ tab }: { tab: string }) {
  const current: Tab = (TABS.find((item) => item.value === tab)?.value ?? "capacity") as Tab;
  return (
    <section className="page">
      <div className="board-bar">
        <h1>Planning</h1>
      </div>
      <div className="tabs-inline wrap">
        {TABS.map((item) => (
          <button key={item.value} className={`btn${current === item.value ? " btn-primary" : ""}`} onClick={() => go(`/planning/${item.value}`)}>
            {item.label}
          </button>
        ))}
      </div>
      {current === "capacity" && <CapacityTab />}
      {current === "proposal" && <ProposalTab />}
      {current === "fg" && <FgTab />}
      {current === "forecast" && <ForecastTab />}
      {current === "settings" && <SettingsTab />}
    </section>
  );
}

// ---------------------------------------------------------------- Capacity

function CapacityTab() {
  const [weeks, setWeeks] = useState(12);
  const [data, setData] = useState<CapacityResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<CapacityResult["fgs"][number] | null>(null);

  const load = useCallback(() => {
    setError(null);
    api<CapacityResult>("GET", `/planeacion/capacidad?semanas=${weeks}`)
      .then(setData)
      .catch((caught) => setError(message(caught, "Could not calculate the capacity")));
  }, [weeks]);
  useEffect(load, [load]);

  const shortWeeks = data?.summary.filter((week) => week.status === "no_cumple").length ?? 0;
  const firstShort = data ? data.summary.findIndex((week) => week.status === "no_cumple") : -1;

  return (
    <>
      <div className="plan-toolbar">
        <label>
          Weeks{" "}
          <select value={weeks} onChange={(event) => setWeeks(Number(event.target.value))}>
            {WEEK_CHOICES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <button className="btn" onClick={load}>
          Refresh
        </button>
      </div>
      <ErrorNote message={error} />
      {!data && !error && <p className="muted">Calculating…</p>}
      {data && (
        <>
          <div className={`note ${shortWeeks ? "note-error" : "note-info"}`}>
            {data.fgs.length === 0
              ? "No FG yet. Create them in «FG and BOM» and enter their weekly forecast."
              : shortWeeks
                ? `Capacity falls short in ${shortWeeks} of ${data.weeks.length} weeks, starting the week of ${weekLabel(data.weeks[firstShort])}.`
                : "The machines cover the forecast in every week shown."}
          </div>
          {data.warnings.length > 0 && (
            <details className="note note-warn">
              <summary>{data.warnings.length} point(s) to review before trusting this result</summary>
              <ul>
                {data.warnings.map((text) => (
                  <li key={text}>{text}</li>
                ))}
              </ul>
            </details>
          )}

          <h2 className="insight-section">All machines, week by week</h2>
          <div className="table-wrap">
            <table className="table plan-table">
              <thead>
                <tr>
                  <th />
                  {data.weeks.map((week) => (
                    <th key={week}>{weekLabel(week)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th>Result</th>
                  {data.summary.map((week, index) => (
                    <td key={index}>
                      <StatusChip status={week.status} />
                    </td>
                  ))}
                </tr>
                <tr>
                  <th>Capacity (h)</th>
                  {data.summary.map((week, index) => (
                    <td key={index}>{fmt(week.capacity_h)}</td>
                  ))}
                </tr>
                <tr>
                  <th>Load (h)</th>
                  {data.summary.map((week, index) => (
                    <td key={index}>
                      {fmt(week.load_h)}
                      {week.prebuilt_h > 0 && <div className="muted small">+{fmt(week.prebuilt_h)} ahead</div>}
                    </td>
                  ))}
                </tr>
                <tr>
                  <th>Use</th>
                  {data.summary.map((week, index) => (
                    <td key={index}>{week.utilization === null ? "—" : `${Math.round(week.utilization * 100)}%`}</td>
                  ))}
                </tr>
                <tr>
                  <th>Late (h)</th>
                  {data.summary.map((week, index) => (
                    <td key={index} className={week.late_h > 0 ? "plan-late" : ""}>
                      {week.late_h > 0 ? `${fmt(week.late_h)} h · ${fmt(week.late_pieces)} pcs` : "—"}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>

          <h2 className="insight-section">Each FG</h2>
          <p className="muted small">Pieces of the weekly forecast, colored by whether its make items fit. Click a FG for its parts.</p>
          <div className="table-wrap">
            <table className="table plan-table">
              <thead>
                <tr>
                  <th>FG</th>
                  {data.weeks.map((week) => (
                    <th key={week}>{weekLabel(week)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.fgs.map((fg) => (
                  <tr key={fg.id} className="clickable" onClick={() => setDetail(fg)}>
                    <th>
                      {fg.numero_fg}
                      {fg.cliente && <div className="muted small">{fg.cliente}</div>}
                    </th>
                    {fg.weeks.map((week, index) => (
                      <td key={index} className={`plan-cell plan-${week.status}`} title={STATUS_LABEL[week.status]}>
                        {week.forecast ? fmt(week.forecast) : ""}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h2 className="insight-section">Make items</h2>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Part number</th>
                  <th>Machine types</th>
                  <th>Forecast</th>
                  <th>Produced</th>
                  <th>On the Board</th>
                  <th>Late at the end</th>
                </tr>
              </thead>
              <tbody>
                {data.parts.map((part) => {
                  const late = part.weeks.length ? part.weeks[part.weeks.length - 1].late : 0;
                  return (
                    <tr key={part.numero_parte}>
                      <td>
                        <strong>{part.numero_parte}</strong>
                      </td>
                      <td>{part.has_program ? part.types.join(", ") : <span className="chip plan-sin_programa">No program</span>}</td>
                      <td>{fmt(part.forecast_total)}</td>
                      <td>{fmt(part.produced)}</td>
                      <td>{fmt(part.board_pending)}</td>
                      <td className={late > 0 && part.has_program ? "plan-late" : ""}>{!part.has_program ? "—" : late > 0 ? `${fmt(late)} pcs` : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <h2 className="insight-section">Machines</h2>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Machine</th>
                  <th>Type</th>
                  <th>Hours / week</th>
                  <th>Already on the Board (h)</th>
                  {data.weeks.map((week) => (
                    <th key={week}>{weekLabel(week)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.machines.map((machine) => (
                  <tr key={machine.code}>
                    <td>
                      <strong>{machine.code}</strong> {!machine.configured && <span className="muted small">default</span>}
                      {machine.disabled && <span className="chip st-missing" title={machine.disabled.reason}>Disabled</span>}
                    </td>
                    <td>{machine.type}</td>
                    <td>{fmt(machine.hours_week)}</td>
                    <td>{fmt(machine.committed_h)}</td>
                    {machine.loads.map((load, index) => (
                      <td key={index}>{load ? fmt(load) : "—"}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {detail && data && <FgDetail fg={detail} data={data} onClose={() => setDetail(null)} />}
    </>
  );
}

function FgDetail({ fg, data, onClose }: { fg: CapacityResult["fgs"][number]; data: CapacityResult; onClose: () => void }) {
  const parts = data.parts.filter((part) => fg.parts.includes(part.numero_parte));
  return (
    <Dialog title={`${fg.numero_fg} · make items`} onClose={onClose} wide>
      {parts.length === 0 && <p className="muted">This FG has no BOM yet.</p>}
      {parts.map((part) => (
        <div key={part.numero_parte} className="plan-detail">
          <strong>{part.numero_parte}</strong>{" "}
          <span className="muted small">
            {part.has_program ? part.types.join(", ") : "no program: its hours are not counted"} · produced {fmt(part.produced)} · on the Board {fmt(part.board_pending)}
          </span>
          <div className="table-wrap">
            <table className="table plan-table">
              <thead>
                <tr>
                  <th />
                  {data.weeks.map((week) => (
                    <th key={week}>{weekLabel(week)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(["need", "assigned", "late"] as const).map((key) => (
                  <tr key={key}>
                    <th>{key === "need" ? "To make" : key === "assigned" ? "Placed" : "Late"}</th>
                    {part.weeks.map((week, index) => (
                      <td key={index} className={key === "late" && week.late > 0 ? "plan-late" : ""}>
                        {week[key] ? fmt(week[key]) : "—"}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </Dialog>
  );
}

// ---------------------------------------------------------------- Proposal

interface ProposalItem {
  numero_parte: string;
  piezas: number;
  hours: number;
  setup_h: number;
  prebuilt: boolean;
  thickness: number | null;
  eligible: string[];
}
interface ProposalResult {
  weeks: string[];
  summary: WeekSummary[];
  warnings: string[];
  proposal: {
    weeks: { week: string; machines: { code: string; type: string; capacity_h: number; load_h: number; items: ProposalItem[] }[] }[];
    not_placed: { numero_parte: string; piezas: number; reason: string }[];
  };
}
interface Line {
  key: string;
  machine: string;
  part: string;
  pieces: string;
  originalPieces: number;
  hours: number;
  prebuilt: boolean;
  thickness: number | null;
  eligible: string[];
  checked: boolean;
}

const NOT_PLACED_REASON: Record<string, string> = {
  no_program: "no program with UPH",
  no_machine: "no compatible machine",
  capacity: "not enough capacity in the weeks shown",
};

function ProposalTab() {
  const { can } = useAuth();
  const canApply = can("planning.manage");
  const [weeks, setWeeks] = useState(8);
  const [data, setData] = useState<ProposalResult | null>(null);
  const [index, setIndex] = useState(0);
  const [lines, setLines] = useState<Line[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setError(null);
    api<ProposalResult>("GET", `/planeacion/propuesta?semanas=${weeks}`)
      .then((result) => {
        setData(result);
        setIndex((value) => Math.min(value, result.weeks.length - 1));
      })
      .catch((caught) => setError(message(caught, "Could not build the proposal")));
  }, [weeks]);
  useEffect(load, [load]);

  useEffect(() => {
    const week = data?.proposal.weeks[index];
    if (!week) return setLines([]);
    setLines(
      week.machines.flatMap((machine) =>
        machine.items.map((item) => ({
          key: `${machine.code}|${item.numero_parte}|${item.prebuilt}`,
          machine: machine.code,
          part: item.numero_parte,
          pieces: String(item.piezas),
          originalPieces: item.piezas,
          hours: item.hours + item.setup_h,
          prebuilt: item.prebuilt,
          thickness: item.thickness,
          eligible: item.eligible,
          checked: !item.prebuilt,
        })),
      ),
    );
  }, [data, index]);

  const grouped = useMemo(() => {
    const result = new Map<string, Line[]>();
    for (const line of [...lines].sort((a, b) => a.machine.localeCompare(b.machine))) {
      result.set(line.machine, [...(result.get(line.machine) ?? []), line]);
    }
    return [...result.entries()];
  }, [lines]);

  const selected = lines.filter((line) => line.checked);

  function patch(key: string, values: Partial<Line>) {
    setLines((list) => list.map((line) => (line.key === key ? { ...line, ...values } : line)));
  }

  async function send() {
    setError(null);
    setInfo(null);
    for (const line of selected) {
      const pieces = Number(line.pieces);
      if (!Number.isInteger(pieces) || pieces < 1) return setError(`Pieces of ${line.part} must be a whole number of 1 or more.`);
    }
    setBusy(true);
    try {
      const result = await api<{ created: unknown[] }>("POST", "/planeacion/propuesta/aplicar", {
        items: selected.map((line) => ({ maquina: line.machine, numero_parte: line.part, piezas: Number(line.pieces) })),
      });
      setInfo(`${result.created.length} demand(s) were added to the Board. The proposal now counts them as already scheduled.`);
      load();
    } catch (caught) {
      setError(message(caught, "The lines could not be sent to the Board"));
    } finally {
      setBusy(false);
    }
  }

  const week = data?.proposal.weeks[index];
  const summary = data?.summary[index];

  return (
    <>
      <p className="muted">What each machine would run to meet the forecast. Review it, change a machine or the pieces, and send the lines you accept to the Board.</p>
      <div className="plan-toolbar">
        <label>
          Week{" "}
          <select value={index} onChange={(event) => setIndex(Number(event.target.value))}>
            {(data?.weeks ?? []).map((value, position) => (
              <option key={value} value={position}>
                {weekLabel(value)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Look ahead{" "}
          <select value={weeks} onChange={(event) => setWeeks(Number(event.target.value))}>
            {WEEK_CHOICES.map((value) => (
              <option key={value} value={value}>
                {value} weeks
              </option>
            ))}
          </select>
        </label>
        {summary && <StatusChip status={summary.status} />}
        {canApply && (
          <button className="btn btn-primary" disabled={busy || selected.length === 0} onClick={() => void send()}>
            {busy ? "Sending…" : `Send ${selected.length} line(s) to the Board`}
          </button>
        )}
      </div>
      <ErrorNote message={error} />
      {info && (
        <div className="note note-info">
          {info}{" "}
          <button className="link-btn" onClick={() => go("/board")}>
            Open the Board
          </button>
        </div>
      )}
      {data && data.warnings.length > 0 && <div className="note note-warn">{data.warnings.length} point(s) to review: see the Capacity tab.</div>}
      {data && data.proposal.not_placed.length > 0 && (
        <div className="note note-error">
          Not placed in these weeks:
          <ul>
            {data.proposal.not_placed.map((row) => (
              <li key={row.numero_parte}>
                <strong>{row.numero_parte}</strong>: {fmt(row.piezas)} pcs, {NOT_PLACED_REASON[row.reason] ?? row.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
      {!data && !error && <p className="muted">Building the proposal…</p>}
      {data && grouped.length === 0 && <p className="note note-info">Nothing needs to run in the week of {week ? weekLabel(week.week) : "—"}.</p>}
      {grouped.map(([code, items]) => {
        const machine = week?.machines.find((row) => row.code === code);
        return (
          <div key={code} className="plan-machine">
            <h3>
              {code} <span className="muted small">{machine?.type ?? ""}{machine ? ` · ${fmt(machine.load_h)} of ${fmt(machine.capacity_h)} h` : ""}</span>
            </h3>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    {canApply && <th />}
                    <th>Part number</th>
                    <th>Thickness</th>
                    <th>Pieces</th>
                    <th>Hours</th>
                    <th>Machine</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((line) => (
                    <tr key={line.key}>
                      {canApply && (
                        <td>
                          <input type="checkbox" checked={line.checked} onChange={(event) => patch(line.key, { checked: event.target.checked })} aria-label={`Send ${line.part}`} />
                        </td>
                      )}
                      <td>
                        <strong>{line.part}</strong> {line.prebuilt && <span className="chip st-kitting">Built ahead</span>}
                      </td>
                      <td>{line.thickness ?? "—"}</td>
                      <td>
                        <input className="plan-input" inputMode="numeric" value={line.pieces} disabled={!canApply} onChange={(event) => patch(line.key, { pieces: event.target.value })} aria-label={`Pieces of ${line.part}`} />
                      </td>
                      <td>{fmt(Math.round(line.hours * (Number(line.pieces) / line.originalPieces || 0) * 10) / 10)}</td>
                      <td>
                        <select value={line.machine} disabled={!canApply} onChange={(event) => patch(line.key, { machine: event.target.value })} aria-label={`Machine for ${line.part}`}>
                          {line.eligible.map((value) => (
                            <option key={value} value={value}>
                              {value}
                            </option>
                          ))}
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
    </>
  );
}

// ---------------------------------------------------------------- FG and BOM

function FgTab() {
  const { can } = useAuth();
  const canEdit = can("planning.manage");
  const [rows, setRows] = useState<FgRow[] | null>(null);
  const [catalog, setCatalog] = useState<CatalogPart[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [editing, setEditing] = useState<FgRow | "new" | null>(null);

  const load = useCallback(() => {
    api<FgRow[]>("GET", "/planeacion/fg")
      .then(setRows)
      .catch((caught) => setError(message(caught, "Could not load the FG list")));
    api<CatalogPart[]>("GET", "/planeacion/partes").then(setCatalog).catch(() => setCatalog([]));
  }, []);
  useEffect(load, [load]);

  function chooseFile(file: File | undefined) {
    if (!file) return;
    setError(null);
    setInfo(null);
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result ?? "");
      const content = text.includes(",") ? text.split(",")[1] : text;
      api<{ created: number; updated: number; problems: string[] }>("POST", "/planeacion/fg/importar", { contenido_base64: content })
        .then((result) => {
          setInfo(`Imported: ${result.created} new, ${result.updated} updated.${result.problems.length ? ` ${result.problems.join(" ")}` : ""}`);
          load();
        })
        .catch((caught) => setError(message(caught, "The file could not be imported")));
    };
    reader.readAsDataURL(file);
  }

  async function remove(row: FgRow) {
    setError(null);
    try {
      await api("DELETE", `/planeacion/fg/${row.id}`);
      load();
    } catch (caught) {
      setError(message(caught, "The FG could not be removed"));
    }
  }

  return (
    <>
      <p className="muted">A FG (a rack, a shelf…) is made of make items and how many pieces of each one every FG unit needs. Two FG numbers with the same BOM can share it.</p>
      {canEdit && (
        <div className="plan-toolbar">
          <button className="btn btn-primary" onClick={() => setEditing("new")}>
            + New FG
          </button>
          <label className="btn">
            Import from Excel
            <input type="file" accept=".xlsx" hidden onChange={(event) => { chooseFile(event.target.files?.[0]); event.target.value = ""; }} />
          </label>
          <span className="muted small">Columns: FG, MAKE and PIEZAS (optional: CLIENTE).</span>
        </div>
      )}
      <ErrorNote message={error} />
      {info && <div className="note note-info">{info}</div>}
      {!rows && !error && <p className="muted">Loading…</p>}
      {rows && rows.length === 0 && <p className="note note-info">No FG yet.</p>}
      {rows && rows.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>FG</th>
                <th>Description</th>
                <th>Customer</th>
                <th>Make items (pieces per FG)</th>
                {canEdit && <th />}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <strong>{row.numero_fg}</strong>
                  </td>
                  <td>{row.descripcion ?? "—"}</td>
                  <td>{row.cliente ?? "—"}</td>
                  <td>
                    {row.bom_from && <div className="muted small">Same BOM as {row.bom_from}</div>}
                    {row.parts.length === 0 && !row.bom_from && <span className="muted">No BOM yet</span>}
                    <div className="chips">
                      {row.parts.map((part) => (
                        <span key={part.numero_parte} className={`chip ${part.has_program ? "st-onmachine" : "plan-sin_programa"}`} title={part.has_program ? part.types.join(", ") : "No program in the system"}>
                          {part.piezas} × {part.numero_parte}
                        </span>
                      ))}
                    </div>
                  </td>
                  {canEdit && (
                    <td className="row-actions">
                      <button className="btn" onClick={() => setEditing(row)}>
                        Edit
                      </button>
                      <button className="btn btn-ghost-danger" onClick={() => void remove(row)}>
                        Remove
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editing && (
        <FgDialog
          row={editing === "new" ? null : editing}
          others={(rows ?? []).filter((row) => !row.bom_from && (editing === "new" || row.id !== editing.id))}
          catalog={catalog}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            load();
          }}
        />
      )}
    </>
  );
}

function FgDialog({ row, others, catalog, onClose, onSaved }: { row: FgRow | null; others: FgRow[]; catalog: CatalogPart[]; onClose: () => void; onSaved: () => void }) {
  const [number, setNumber] = useState(row?.numero_fg ?? "");
  const [description, setDescription] = useState(row?.descripcion ?? "");
  const [customer, setCustomer] = useState(row?.cliente ?? "");
  const [shared, setShared] = useState(row?.bom_from ?? "");
  const [parts, setParts] = useState<{ part: string; pieces: string }[]>(
    row && !row.bom_from ? row.parts.map((p) => ({ part: p.numero_parte, pieces: String(p.piezas) })) : [{ part: "", pieces: "1" }],
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function update(index: number, patch: Partial<{ part: string; pieces: string }>) {
    setParts((list) => list.map((item, i) => (i === index ? { ...item, ...patch } : item)));
  }

  async function submit() {
    setError(null);
    if (!number.trim()) return setError("Write the FG number.");
    const filled = parts.filter((item) => item.part.trim());
    for (const item of filled) {
      const pieces = Number(item.pieces);
      if (!Number.isInteger(pieces) || pieces < 1) return setError(`Pieces per FG of ${item.part} must be a whole number of 1 or more.`);
    }
    setBusy(true);
    try {
      const body = {
        numero_fg: number,
        descripcion: description,
        cliente: customer,
        bom_de: shared || null,
        partes: shared ? [] : filled.map((item) => ({ numero_parte: item.part, piezas: Number(item.pieces) })),
      };
      await (row ? api("PUT", `/planeacion/fg/${row.id}`, body) : api("POST", "/planeacion/fg", body));
      onSaved();
    } catch (caught) {
      setError(message(caught, "The FG could not be saved"));
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={row ? `Edit ${row.numero_fg}` : "New FG"}
      onClose={onClose}
      wide
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={() => void submit()} disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </button>
        </>
      }
    >
      <div className="form">
        <label>
          FG number
          <input value={number} onChange={(event) => setNumber(event.target.value)} autoFocus maxLength={60} placeholder="e.g. TYEH-1171174-01_01-FG" />
        </label>
        <label>
          Description
          <input value={description} onChange={(event) => setDescription(event.target.value)} maxLength={120} />
        </label>
        <label>
          Customer
          <input value={customer} onChange={(event) => setCustomer(event.target.value)} maxLength={60} />
        </label>
        <label>
          Make items
          <select value={shared} onChange={(event) => setShared(event.target.value)}>
            <option value="">Its own list</option>
            {others.map((other) => (
              <option key={other.id} value={other.numero_fg}>
                Same BOM as {other.numero_fg}
              </option>
            ))}
          </select>
        </label>
        {!shared && (
          <>
            <datalist id="plan-parts">
              {catalog.map((part) => (
                <option key={part.numero_parte} value={part.numero_parte}>
                  {part.types.join(", ")}
                </option>
              ))}
            </datalist>
            {parts.map((item, index) => (
              <div key={index} className="plan-bom-row">
                <input list="plan-parts" value={item.part} onChange={(event) => update(index, { part: event.target.value })} placeholder="Make part number (pick or type)" aria-label="Make part number" />
                <input type="number" min={1} value={item.pieces} onChange={(event) => update(index, { pieces: event.target.value })} aria-label="Pieces per FG" />
                <span className="muted small">per FG</span>
                <button className="btn btn-ghost-danger" onClick={() => setParts((list) => list.filter((_, i) => i !== index))} aria-label="Remove make item">
                  ✕
                </button>
              </div>
            ))}
            <button className="btn" onClick={() => setParts((list) => [...list, { part: "", pieces: "1" }])}>
              + Add make item
            </button>
            <p className="muted small">A make item that is not in the system can be typed too; its hours are not counted until it has a program.</p>
          </>
        )}
        <ErrorNote message={error} />
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------- Forecast

function ForecastTab() {
  const { can } = useAuth();
  const canEdit = can("planning.manage");
  const [weeks, setWeeks] = useState(12);
  const [grid, setGrid] = useState<ForecastGrid | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(() => {
    setError(null);
    api<ForecastGrid>("GET", `/planeacion/forecast?semanas=${weeks}`)
      .then((result) => {
        setGrid(result);
        setEdits({});
      })
      .catch((caught) => setError(message(caught, "Could not load the forecast")));
  }, [weeks]);
  useEffect(load, [load]);

  const changed = useMemo(() => Object.keys(edits).length, [edits]);

  async function save() {
    if (!grid) return;
    setError(null);
    const rows: Record<string, Record<string, number>> = {};
    for (const [key, text] of Object.entries(edits)) {
      const [fg, week] = key.split("|");
      const value = text.trim() === "" ? 0 : Number(text);
      if (!Number.isInteger(value) || value < 0) return setError("Quantities are whole numbers of 0 or more.");
      (rows[fg] ??= {})[week] = value;
    }
    try {
      await api("PUT", "/planeacion/forecast", { filas: Object.entries(rows).map(([numero_fg, semanas]) => ({ numero_fg, semanas })) });
      setSaved(true);
      load();
    } catch (caught) {
      setError(message(caught, "The forecast could not be saved"));
    }
  }

  return (
    <>
      <p className="muted">Pieces of each FG needed per week (the week starts on Monday). Weeks before this one are not planned.</p>
      <div className="plan-toolbar">
        <label>
          Weeks{" "}
          <select value={weeks} onChange={(event) => setWeeks(Number(event.target.value))}>
            {WEEK_CHOICES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        {canEdit && (
          <button className="btn btn-primary" onClick={() => void save()} disabled={changed === 0}>
            Save{changed ? ` (${changed})` : ""}
          </button>
        )}
        {saved && changed === 0 && <span className="muted small">Saved.</span>}
      </div>
      <ErrorNote message={error} />
      {grid && grid.rows.length === 0 && <p className="note note-info">Create a FG first in «FG and BOM».</p>}
      {grid && grid.rows.length > 0 && (
        <div className="table-wrap">
          <table className="table plan-table">
            <thead>
              <tr>
                <th>FG</th>
                {grid.weeks.map((week) => (
                  <th key={week}>{weekLabel(week)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {grid.rows.map((row) => (
                <tr key={row.fg_id}>
                  <th>{row.numero_fg}</th>
                  {grid.weeks.map((week, index) => {
                    const key = `${row.numero_fg}|${week}`;
                    return (
                      <td key={week}>
                        <input
                          className={`plan-input${key in edits ? " edited" : ""}`}
                          inputMode="numeric"
                          disabled={!canEdit}
                          value={key in edits ? edits[key] : row.values[index] ? String(row.values[index]) : ""}
                          onChange={(event) => { setSaved(false); setEdits((list) => ({ ...list, [key]: event.target.value })); }}
                          aria-label={`${row.numero_fg} ${weekLabel(week)}`}
                        />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------- Capacity settings

const PARAM_LABELS: Record<string, string> = {
  setup_minutos: "Setup time per part (min)",
  adelanto_semanas: "Weeks that can be built ahead",
  horizonte_semanas: "Weeks planned by default",
  turnos: "Default shifts",
  horas_turno: "Default hours per shift",
  dias_semana: "Default days per week",
  eficiencia_pct: "Default efficiency (%)",
};

function SettingsTab() {
  const { can } = useAuth();
  const canEdit = can("planning.manage");
  const [data, setData] = useState<MachinesResult | null>(null);
  const [params, setParams] = useState<Record<string, string>>({});
  const [rows, setRows] = useState<Record<string, MachineRow>>({});
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const load = useCallback(() => {
    api<MachinesResult>("GET", "/planeacion/maquinas")
      .then((result) => {
        setData(result);
        setParams(Object.fromEntries(Object.entries(result.params).map(([key, value]) => [key, String(value)])));
        setRows(Object.fromEntries(result.machines.map((machine) => [machine.code, machine])));
      })
      .catch((caught) => setError(message(caught, "Could not load the settings")));
  }, []);
  useEffect(load, [load]);

  async function saveParams() {
    setError(null);
    setInfo(null);
    try {
      await api("PUT", "/planeacion/parametros", { valores: Object.fromEntries(Object.entries(params).map(([key, value]) => [key, Number(value)])) });
      setInfo("Parameters saved. Machines without their own values use the new defaults.");
      load();
    } catch (caught) {
      setError(message(caught, "The parameters could not be saved"));
    }
  }

  async function saveMachine(code: string) {
    const row = rows[code];
    setError(null);
    setInfo(null);
    try {
      await api("PUT", `/planeacion/maquinas/${encodeURIComponent(code)}`, {
        turnos: Number(row.shifts), horas_turno: Number(row.hours_per_shift), dias_semana: Number(row.days_per_week),
        eficiencia_pct: Number(row.efficiency_pct), incluida: row.included,
      });
      setInfo(`${code} saved.`);
      load();
    } catch (caught) {
      setError(message(caught, `${code} could not be saved`));
    }
  }

  function patch(code: string, values: Partial<MachineRow>) {
    setRows((list) => ({ ...list, [code]: { ...list[code], ...values } }));
  }

  return (
    <>
      <p className="muted">Hours a machine can run per week = shifts × hours per shift × days × efficiency. The values below start as defaults to review.</p>
      <ErrorNote message={error} />
      {info && <div className="note note-info">{info}</div>}
      {data && (
        <>
          <div className="plan-params">
            {Object.keys(PARAM_LABELS).map((key) => (
              <label key={key}>
                {PARAM_LABELS[key]}
                <input type="number" value={params[key] ?? ""} onChange={(event) => setParams((list) => ({ ...list, [key]: event.target.value }))} disabled={!canEdit} />
              </label>
            ))}
            {canEdit && (
              <button className="btn btn-primary" onClick={() => void saveParams()}>
                Save parameters
              </button>
            )}
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Machine</th>
                  <th>Type</th>
                  <th>Shifts</th>
                  <th>Hours / shift</th>
                  <th>Days / week</th>
                  <th>Efficiency %</th>
                  <th>Hours / week</th>
                  <th>Thickness range</th>
                  <th>Planned</th>
                  {canEdit && <th />}
                </tr>
              </thead>
              <tbody>
                {data.machines.map((machine) => {
                  const row = rows[machine.code] ?? machine;
                  return (
                    <tr key={machine.code}>
                      <td>
                        <strong>{machine.code}</strong> {!machine.configured && <span className="muted small">default</span>}
                      </td>
                      <td>{machine.type ?? <span className="muted">no type: left out</span>}</td>
                      {(["shifts", "hours_per_shift", "days_per_week", "efficiency_pct"] as const).map((field) => (
                        <td key={field}>
                          <input className="plan-input" type="number" value={row[field]} disabled={!canEdit} onChange={(event) => patch(machine.code, { [field]: Number(event.target.value) } as Partial<MachineRow>)} />
                        </td>
                      ))}
                      <td>{machine.hours_week}</td>
                      <td>{machine.thickness_min !== null && machine.thickness_max !== null ? `${machine.thickness_min}–${machine.thickness_max} in` : <span className="muted">not set</span>}</td>
                      <td>
                        <input type="checkbox" checked={row.included} disabled={!canEdit} onChange={(event) => patch(machine.code, { included: event.target.checked })} aria-label={`${machine.code} planned`} />
                      </td>
                      {canEdit && (
                        <td className="row-actions">
                          <button className="btn" onClick={() => void saveMachine(machine.code)}>
                            Save
                          </button>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
