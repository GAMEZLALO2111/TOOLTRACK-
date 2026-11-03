import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { CartsPanel, useKitOverview } from "../components/Carts";
import { Dialog, ErrorNote } from "../components/Dialog";
import { Chip } from "../components/MachineCard";
import { CATEGORY_LABEL, PICK_ACTION_LABEL } from "../labels";
import { go, type Route } from "../router";
import type { Kit, KitPlan, ProgramRow } from "../types";

export function KittingPage({ route }: { route: Route }) {
  const { can } = useAuth();
  const { overview, error: overviewError, reload } = useKitOverview();
  const [machine, setMachine] = useState(route.query.get("machine") ?? "");
  const [newPart, setNewPart] = useState(route.query.get("part") ?? "");
  const [currentOverride, setCurrentOverride] = useState("");
  const [cart, setCart] = useState("");
  const [parts, setParts] = useState<string[]>([]);
  const [plan, setPlan] = useState<KitPlan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [planning, setPlanning] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [mismatch, setMismatch] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<{ kit: Kit; items: string[] } | null>(null);
  const requestId = useRef(0);

  const machines = overview?.machines ?? [];
  const selected = machines.find((m) => m.machine === machine) ?? null;
  const freeCarts = useMemo(() => (plan?.carts ?? overview?.carts ?? []).filter((c) => c.state === "free"), [plan, overview]);
  const machineKit = selected?.kit_state ? (overview?.carts.find((c) => c.machine === selected.machine) ?? null) : null;

  // Piezas guardadas para el tipo de la máquina elegida.
  useEffect(() => {
    setParts([]);
    if (!selected?.tipo) return;
    api<ProgramRow[]>("GET", `/workflow/programs?tipo_maquina=${encodeURIComponent(selected.tipo)}`)
      .then((rows) => setParts(rows.map((row) => row.numero_parte).sort()))
      .catch(() => setParts([]));
  }, [selected?.tipo]);

  // Comparación automática cuando hay máquina y una pieza nueva válida.
  const newMatch = parts.find((p) => p.toUpperCase() === newPart.trim().toUpperCase()) ?? null;
  useEffect(() => {
    setPlan(null);
    setPlanError(null);
    setStartError(null);
    if (!machine || !newMatch) return;
    const id = ++requestId.current;
    setPlanning(true);
    api<KitPlan>("POST", "/workflow/kitting/plan", {
      maquina_codigo: machine,
      setup_nuevo_parte: newMatch,
      setup_anterior_parte: currentOverride || null,
    })
      .then((result) => {
        if (id !== requestId.current) return;
        setPlan(result);
        setCart((previous) => (result.carts.some((c) => c.codigo === previous && c.state === "free") ? previous : (result.suggested_cart ?? "")));
      })
      .catch((caught) => {
        if (id === requestId.current) setPlanError(caught instanceof ApiError ? caught.message : "Could not compare the setups.");
      })
      .finally(() => {
        if (id === requestId.current) setPlanning(false);
      });
  }, [machine, newMatch, currentOverride]);

  function chooseMachine(code: string) {
    setMachine(code);
    setNewPart("");
    setCurrentOverride("");
    setCart("");
  }

  async function start(correction?: string) {
    if (!plan || !machine || !newMatch) return;
    setStarting(true);
    setStartError(null);
    try {
      const kit = await api<Kit>("POST", "/workflow/kitting/start", {
        maquina_codigo: machine,
        setup_nuevo_parte: newMatch,
        setup_anterior_parte: plan.current_part,
        carro_codigo: cart || null,
        corregir_actual: correction !== undefined,
        motivo_correccion: correction ?? null,
        creado_por: "web",
      });
      setMismatch(null);
      void reload();
      if (kit.warnings?.length) setWarnings({ kit, items: kit.warnings });
      else go(`/kitting/kit/${kit.id}`);
    } catch (caught) {
      const data = caught instanceof ApiError ? (caught.data as { code?: string; message?: string } | null) : null;
      if (data && typeof data === "object" && data.code === "SETUP_MISMATCH") {
        setMismatch(data.message ?? "The registered setup does not match what you chose.");
      } else {
        setStartError(caught instanceof ApiError ? caught.message : "The kit could not be started.");
        setMismatch(null);
      }
    } finally {
      setStarting(false);
    }
  }

  const comparison = plan?.comparison ?? null;
  const canStart = can("kitting.operate") && !!comparison && !!cart && !machineKit && !starting;

  return (
    <section className="page kitting">
      <div className="board-bar">
        <h1>Kitting</h1>
        <div className="bar-right">
          <button className="btn" onClick={() => go("/kitting/pending")}>
            Pending pickups
          </button>
          <button className="btn" onClick={() => go("/kitting/labels")}>
            QR labels
          </button>
          <button className="btn" onClick={() => void reload()}>
            Refresh
          </button>
        </div>
      </div>
      {overviewError && <div className="note note-warn">⚠ {overviewError}</div>}

      <div className="steps">
        <div className="step">
          <h2>
            <span className="step-n">1</span> Which machine?
          </h2>
          <select value={machine} onChange={(event) => chooseMachine(event.target.value)} aria-label="Machine">
            <option value="">Select a machine…</option>
            {machines.map((m) => (
              <option key={m.machine} value={m.machine}>
                {m.machine} · {m.tipo || "no type"} · {m.numero_parte ?? "empty"}
                {m.kit_state ? " · kit open" : ""}
              </option>
            ))}
          </select>
          {selected && (
            <p className="muted small">
              {selected.numero_parte ? `Running now: ${selected.numero_parte}` : "No part registered on this machine"}
              {selected.thickness ? ` · thickness ${selected.thickness} in` : ""}
            </p>
          )}
          {machineKit && (
            <div className="note note-warn">
              ⚠ {machineKit.machine} already has a kit ({machineKit.state === "paused" ? "paused" : machineKit.state === "ready" ? "ready to deliver" : "in progress"}) on cart{" "}
              {machineKit.codigo}. Open it or cancel it before starting another.{" "}
              <button className="btn" onClick={() => go(`/kitting/kit/${machineKit.kit_id}`)}>
                Open kit
              </button>
            </div>
          )}
        </div>

        <div className="step">
          <h2>
            <span className="step-n">2</span> Which part number is next?
          </h2>
          <input
            list="kit-parts"
            value={newPart}
            onChange={(event) => setNewPart(event.target.value)}
            placeholder={machine ? "Type or pick a part number" : "Choose a machine first"}
            disabled={!machine}
            aria-label="New part number"
          />
          <datalist id="kit-parts">
            {parts.map((p) => (
              <option key={p} value={p} />
            ))}
          </datalist>
          {machine && newPart.trim() && !newMatch && parts.length > 0 && <p className="muted small">Not found for {selected?.tipo}. Pick one from the list.</p>}
          {machine && parts.length === 0 && selected?.tipo && <p className="muted small">No saved programs for {selected.tipo} yet.</p>}
          {plan && !plan.needs_current && (
            <p className="muted small">
              Current setup: <strong>{plan.current_part}</strong>
              {plan.mounted_part && plan.mounted_part !== plan.current_part ? ` (the system has ${plan.mounted_part} registered)` : ""}
              {" · "}
              <select className="inline-select" value={currentOverride} onChange={(event) => setCurrentOverride(event.target.value)} aria-label="Change the current part">
                <option value="">Use the one on the machine</option>
                {parts.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </p>
          )}
        </div>
      </div>

      {planning && <p className="muted">Comparing…</p>}
      <ErrorNote message={planError} />

      {plan?.needs_current && (
        <div className="note note-info">
          The system does not know what is mounted on {plan.machine}. Choose the current part:{" "}
          <select value={currentOverride} onChange={(event) => setCurrentOverride(event.target.value)} aria-label="Current part">
            <option value="">Select…</option>
            {parts.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
      )}

      {plan && comparison && (
        <div className="panel">
          <h2>
            <span className="step-n">3</span> What to put on the cart
          </h2>
          <div className="cmp-head">
            <strong>
              {plan.current_part} → {plan.new_part}
            </strong>
            <span className="muted">
              Thickness {comparison.thickness_current} → {comparison.thickness_new} in{comparison.thickness_changed ? " (changes)" : ""}
            </span>
            <span className="muted">
              Die clearance target:{" "}
              {comparison.clearance_min && comparison.clearance_max ? `${comparison.clearance_min}–${comparison.clearance_max} in` : "out of the approved range; review manually"}
            </span>
          </div>
          <div className="chips">
            <Chip text={`${comparison.supply} to supply`} className="st-kitting" />
            <Chip text={`${comparison.verify} to verify`} className="st-onmachine" />
          </div>
          {plan.pick_list.length === 0 ? (
            <p className="note note-info">No new tools are needed for this change.</p>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Station</th>
                    <th>TN</th>
                    <th>Angle</th>
                    <th>Part</th>
                    <th>Clearance</th>
                    <th>What to do</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.pick_list.map((row, index) => (
                    <tr key={index} className={row.kind === "verify" ? "row-verify" : ""}>
                      <td>{row.station}</td>
                      <td>
                        <strong>{row.tn}</strong>
                      </td>
                      <td>{row.angle}</td>
                      <td>{CATEGORY_LABEL[row.category]}</td>
                      <td>{row.category === "die" ? (row.clearance ?? "Manual review") : "—"}</td>
                      <td>{PICK_ACTION_LABEL[row.action]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="start-bar">
            <h2 className="start-title">
              <span className="step-n">4</span> Which cart?
            </h2>
            <div className="start-row">
              <select value={cart} onChange={(event) => setCart(event.target.value)} aria-label="Cart" disabled={!!machineKit}>
                {freeCarts.length === 0 && <option value="">No free carts</option>}
                {freeCarts.map((c) => (
                  <option key={c.codigo} value={c.codigo}>
                    {c.codigo}
                  </option>
                ))}
              </select>
              {can("kitting.operate") ? (
                <button className="btn btn-primary big" disabled={!canStart} onClick={() => void start()}>
                  {starting ? "Starting…" : "Start kitting"}
                </button>
              ) : (
                <span className="muted">Your role can look at this but cannot start a kit.</span>
              )}
            </div>
            {freeCarts.length === 0 && !machineKit && <p className="muted small">All carts are busy. Finish or cancel an open kit below.</p>}
            <ErrorNote message={startError} />
          </div>
        </div>
      )}

      <h2 className="section-title">Carts</h2>
      <CartsPanel carts={overview?.carts ?? []} />

      {mismatch && <MismatchDialog message={mismatch} busy={starting} onClose={() => setMismatch(null)} onConfirm={(reason) => void start(reason)} />}
      {warnings && <WarningsDialog kit={warnings.kit} items={warnings.items} onClose={() => { const id = warnings.kit.id; setWarnings(null); go(`/kitting/kit/${id}`); }} />}
    </section>
  );
}

function WarningsDialog({ kit, items, onClose }: { kit: Kit; items: string[]; onClose: () => void }) {
  return (
    <Dialog
      title="Machine alerts"
      onClose={onClose}
      footer={
        <button className="btn btn-primary" onClick={onClose}>
          Understood, open the kit
        </button>
      }
    >
      <p>
        The kit was created, but check this on <strong>{kit.machine}</strong>:
      </p>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </Dialog>
  );
}

function MismatchDialog({ message, busy, onClose, onConfirm }: { message: string; busy: boolean; onClose: () => void; onConfirm: (reason: string) => void }) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  function submit() {
    if (!reason.trim()) return setError("Write why the registered setup differs.");
    onConfirm(reason.trim());
  }
  return (
    <Dialog
      title="The current setup does not match"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            No, go back
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={busy}>
            Yes, what I chose is mounted
          </button>
        </>
      }
    >
      <p>{message}</p>
      <p>Is what you chose really what is mounted? If so, the machine record will be corrected and the reason saved.</p>
      <label className="form">
        <span className="muted small">Reason (required)</span>
        <input value={reason} onChange={(event) => setReason(event.target.value)} autoFocus onKeyDown={(event) => event.key === "Enter" && submit()} />
      </label>
      <ErrorNote message={error} />
    </Dialog>
  );
}
