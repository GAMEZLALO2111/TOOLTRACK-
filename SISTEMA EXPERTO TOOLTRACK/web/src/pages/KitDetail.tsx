import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { Dialog, ErrorNote } from "../components/Dialog";
import { Chip } from "../components/MachineCard";
import { Qr } from "../components/Qr";
import { ReasonDialog } from "../components/ReasonDialog";
import { ScanInput } from "../components/ScanInput";
import { toolCode, type Capture } from "../scan";
import { ACTION_LABEL, CATEGORY_LABEL, COMPONENT_STATE_LABEL, KIT_STATE_CLASS, KIT_STATE_LABEL } from "../labels";
import { go } from "../router";
import type { Candidate, DeliveryResult, Kit, KitComponent, KitQr, KitRefreshResult } from "../types";
import { usePoll } from "../usePoll";

type Reasoned = "pause" | "cancel" | null;

export function KitDetailPage({ kitId }: { kitId: string }) {
  const { can } = useAuth();
  const canOperate = can("kitting.operate");
  const [kit, setKit] = useState<Kit | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reasoned, setReasoned] = useState<Reasoned>(null);
  const [choosing, setChoosing] = useState<KitComponent | null>(null);
  const [qr, setQr] = useState<KitQr | null>(null);
  const [delivered, setDelivered] = useState<DeliveryResult | null>(null);
  const [refreshed, setRefreshed] = useState<KitRefreshResult | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setKit(await api<Kit>("GET", `/workflow/kits/${encodeURIComponent(kitId)}`));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the kit.");
    }
  }, [kitId]);
  usePoll(load, 15000);

  // Al quedar listo, se pide (o se recupera) el QR de entrega.
  const state = kit?.state;
  useEffect(() => {
    if (state !== "ready") {
      setQr(null);
      return;
    }
    api<KitQr>("POST", `/workflow/kits/${encodeURIComponent(kitId)}/qr`)
      .then(setQr)
      .catch((caught) => setActionError(caught instanceof ApiError ? caught.message : "Could not get the delivery QR."));
  }, [state, kitId]);

  const needed = kit?.components.filter((c) => c.action !== "keep") ?? [];
  const kept = kit?.components.filter((c) => c.action === "keep") ?? [];
  const done = needed.filter((c) => c.state === "ready").length;
  const open = kit?.state === "preparing" || kit?.state === "paused";
  const complete = needed.length > 0 && done === needed.length;

  async function act(path: string, body: Record<string, unknown> = {}) {
    setBusy(true);
    setActionError(null);
    try {
      const next = await api<Kit>("POST", `/workflow/kits/${encodeURIComponent(kitId)}/${path}`, { realizado_por: "web", ...body });
      setKit(next);
      return true;
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.message : "The action could not be completed.");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function makeReady() {
    setBusy(true);
    setActionError(null);
    try {
      setQr(await api<KitQr>("POST", `/workflow/kits/${encodeURIComponent(kitId)}/qr`));
      await load();
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.message : "The kit is not complete yet.");
    } finally {
      setBusy(false);
    }
  }

  async function refresh() {
    setNotice(null);
    if (!(open || kit?.state === "ready") || !canOperate) return void load();
    setBusy(true);
    setActionError(null);
    try {
      const result = await api<KitRefreshResult>("POST", `/workflow/kits/${encodeURIComponent(kitId)}/refresh`, { realizado_por: "web" });
      setKit(result.kit);
      if (result.changed) setRefreshed(result);
      else setNotice("The program has not changed since this kit started.");
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.message : "The kit could not be refreshed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page">
      <div className="board-bar">
        <h1>{kit ? `Kit for ${kit.machine}${kit.part_number ? ` · ${kit.part_number}` : ""}` : "Kit"}</h1>
        <div className="bar-right">
          <button className="btn" onClick={() => go("/kitting")}>
            ← Kitting
          </button>
          <button className="btn" disabled={busy} onClick={() => void refresh()}>
            Refresh
          </button>
        </div>
      </div>
      <ErrorNote message={error} />
      {notice && <div className="note note-info">✔ {notice}</div>}
      {!kit && !error && <p className="muted">Loading…</p>}

      {kit && (
        <>
          <div className="panel kit-head">
            <Stat label="Part number" value={<strong>{kit.part_number ?? "—"}</strong>} />
            <Stat label="Thickness" value={kit.thickness ? `${kit.thickness} in` : "—"} />
            <Stat label="Die clearance" value={<strong>{kit.clearance_min && kit.clearance_max ? `${kit.clearance_min} – ${kit.clearance_max}` : "—"}</strong>} />
            <Stat label="Cart" value={<strong>{kit.cart}</strong>} />
            <Stat label="Status" value={<Chip text={KIT_STATE_LABEL[kit.state]} className={KIT_STATE_CLASS[kit.state]} />} />
            <Stat label="Progress" value={<strong>{done}/{needed.length}</strong>} />
            <Stat label="Created" value={new Date(kit.created_at + "Z").toLocaleString()} />
            {kit.reason && <Stat label="Reason" value={kit.reason} />}
          </div>

          {kit.state === "paused" && <div className="note note-warn">This kit is paused. Resume it to keep scanning tools.</div>}
          {kit.state === "cancelled" && <div className="note note-info">This kit was cancelled. Its tools went back to their racks.</div>}
          {kit.state === "delivered" && !delivered && <div className="note note-info">This kit was delivered{kit.delivered_at ? ` on ${new Date(kit.delivered_at + "Z").toLocaleString()}` : ""}.</div>}

          {delivered && (
            <div className="panel delivered">
              <h2>✔ Delivered to {delivered.machine}</h2>
              <p>
                {delivered.delivered_tools} tool(s) delivered. Active setup: <strong>{delivered.active_drawing ?? delivered.active_setup_id}</strong>.
              </p>
              {delivered.pending_pickups_created > 0 ? (
                <p>
                  <strong>{delivered.pending_pickups_created}</strong> outgoing tool(s) are waiting to be picked up from the machine.{" "}
                  <button className="btn btn-primary" onClick={() => go("/kitting/returns")}>
                    Go to pending pickups
                  </button>
                </p>
              ) : (
                <p className="muted">No tools to pick up from the machine.</p>
              )}
            </div>
          )}

          {open && canOperate && (
            <ScanBox
              kitId={kit.id}
              disabled={busy}
              onKit={setKit}
              hint={kit.state === "paused" ? "The kit is paused; scanning resumes it." : undefined}
            />
          )}

          {kit.state !== "delivered" && kit.state !== "cancelled" && canOperate && (
            <div className="kit-actions">
              {open && (
                <button className="btn btn-primary big" disabled={busy || !complete} onClick={() => void makeReady()}>
                  Kit complete: create delivery QR
                </button>
              )}
              {kit.state === "preparing" && (
                <button className="btn" disabled={busy} onClick={() => setReasoned("pause")}>
                  Pause
                </button>
              )}
              {kit.state === "paused" && (
                <button className="btn" disabled={busy} onClick={() => void act("resume")}>
                  Resume
                </button>
              )}
              <button className="btn btn-ghost-danger" disabled={busy} onClick={() => setReasoned("cancel")}>
                Cancel kit
              </button>
              {open && !complete && <span className="muted small">All {needed.length} tools must be on the cart first.</span>}
            </div>
          )}
          <ErrorNote message={actionError} />

          {kit.state === "ready" && qr && canOperate && (
            <DeliveryPanel
              kit={kit}
              qr={qr}
              onDelivered={(result) => {
                setDelivered(result);
                void load();
              }}
            />
          )}

          <h2 className="section-title">Tools to pick ({needed.length})</h2>
          <ComponentTable rows={needed} clearance={kit.clearance_min && kit.clearance_max ? `${kit.clearance_min} – ${kit.clearance_max}` : null} onChoose={open && canOperate ? setChoosing : undefined} />
          {kept.length > 0 && (
            <details className="kept">
              <summary>Tools that stay on the machine ({kept.length})</summary>
              <ComponentTable rows={kept} />
            </details>
          )}
        </>
      )}

      {refreshed && <RefreshDialog result={refreshed} onClose={() => setRefreshed(null)} />}
      {reasoned && kit && (
        <ReasonDialog
          title={reasoned === "pause" ? "Pause kit" : "Cancel kit"}
          message={
            reasoned === "pause"
              ? "Why is this kit being paused?"
              : "The tools already on the cart go back to their racks. Why is this kit being cancelled?"
          }
          confirmLabel={reasoned === "pause" ? "Pause" : "Cancel kit"}
          danger={reasoned === "cancel"}
          onClose={() => setReasoned(null)}
          onConfirm={(reason) => {
            const which = reasoned;
            setReasoned(null);
            void act(which === "pause" ? "pause" : "cancel", { motivo: reason });
          }}
        />
      )}
      {choosing && kit && (
        <ChooseDialog
          kit={kit}
          component={choosing}
          onClose={() => setChoosing(null)}
          onChosen={(next) => {
            setKit(next);
            setChoosing(null);
          }}
        />
      )}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="muted small">{label}</div>
      {value}
    </div>
  );
}

function ScanBox({ kitId, disabled, onKit, hint }: { kitId: string; disabled: boolean; onKit: (kit: Kit) => void; hint?: string }) {
  const [value, setValue] = useState("");
  const [capture, setCapture] = useState<Capture>("manual");
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    input.current?.focus();
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const id = toolCode(value);
    if (!id) return;
    setBusy(true);
    try {
      const next = await api<Kit>("POST", `/workflow/kits/${encodeURIComponent(kitId)}/scan`, { herramienta_id: id, realizado_por: "web", captura: capture });
      onKit(next);
      const placed = next.components.find((c) => c.tool_id?.toUpperCase() === id.toUpperCase());
      setFeedback({ ok: true, text: placed ? `✔ ${id} → ${placed.tn} ${CATEGORY_LABEL[placed.category] ?? placed.category} (station ${placed.station ?? "—"})` : `✔ ${id} added` });
      setValue("");
    } catch (caught) {
      setFeedback({ ok: false, text: caught instanceof ApiError ? caught.message : "The scan failed." });
      input.current?.select();
    } finally {
      setBusy(false);
      input.current?.focus();
    }
  }

  return (
    <form className="panel scan" onSubmit={submit}>
      <h2>Scan or type a tool ID</h2>
      <div className="start-row">
        <ScanInput ref={input} value={value} onValue={(next, mode) => { setValue(next); setCapture(mode); }} placeholder="Scan the tool, then Enter" disabled={disabled || busy} aria-label="Tool ID" />
        <button className="btn btn-primary" type="submit" disabled={disabled || busy || !value.trim()}>
          Add to cart
        </button>
      </div>
      {hint && <p className="muted small">{hint}</p>}
      {feedback && (
        <div className={`scan-result ${feedback.ok ? "ok" : "bad"}`} role="status">
          {feedback.text}
        </div>
      )}
    </form>
  );
}

function DeliveryPanel({ kit, qr, onDelivered }: { kit: Kit; qr: KitQr; onDelivered: (result: DeliveryResult) => void }) {
  const [machineScan, setMachineScan] = useState("");
  const [kitScan, setKitScan] = useState(qr.scan_value);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function confirm(event: FormEvent) {
    event.preventDefault();
    if (!machineScan.trim()) return setError(`First scan the QR on machine ${kit.machine} (or type ${kit.machine}).`);
    if (!kitScan.trim()) return setError("Scan or paste the kit QR value.");
    setBusy(true);
    setError(null);
    try {
      onDelivered(await api<DeliveryResult>("POST", "/workflow/delivery/scan", { scan_value: kitScan.trim(), machine_scan: machineScan.trim(), realizado_por: "web" }));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The delivery could not be confirmed.");
      setBusy(false);
    }
  }

  return (
    <div className="panel delivery">
      <h2>Ready to deliver to {kit.machine}</h2>
      <div className="delivery-grid">
        <div className="print-area label">
          <Qr value={qr.scan_value} size={200} />
          <div className="label-title">KIT → {kit.machine}</div>
          <div className="label-sub">Cart {kit.cart}</div>
          <div className="label-code">{qr.scan_value}</div>
        </div>
        <form onSubmit={confirm} className="form">
          <label>
            1 · Machine QR (or code)
            <input value={machineScan} onChange={(event) => setMachineScan(event.target.value)} placeholder={`TOOLTRACK-MACHINE:${kit.machine}`} autoComplete="off" />
          </label>
          <label>
            2 · Kit QR
            <input value={kitScan} onChange={(event) => setKitScan(event.target.value)} autoComplete="off" />
          </label>
          <ErrorNote message={error} />
          <div className="form-actions">
            <button type="button" className="btn" onClick={() => window.print()}>
              Print label
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              Confirm delivery
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function RefreshDialog({ result, onClose }: { result: KitRefreshResult; onClose: () => void }) {
  return (
    <Dialog title="Kit refreshed from the program" onClose={onClose} footer={<button className="btn btn-primary" onClick={onClose}>OK</button>}>
      <div className="form">
        {result.returned.length > 0 && (
          <div className="note note-warn">
            <strong>Put back in the rack</strong>
            <ul>
              {result.returned.map((row) => (
                <li key={row.tool_id}>
                  {row.tool_id} → {row.to ?? "its rack"} (TN {row.tn}, {CATEGORY_LABEL[row.category] ?? row.category}
                  {row.station ? `, station ${row.station}` : ""})
                </li>
              ))}
            </ul>
          </div>
        )}
        {result.added.length > 0 && (
          <div className="note note-info">
            <strong>New positions</strong>
            <ul>
              {result.added.map((row) => (
                <li key={`${row.tn}-${row.angle}`}>
                  TN {row.tn}
                  {row.angle ? ` · ${row.angle}°` : ""} · station {row.station ?? "—"}
                </li>
              ))}
            </ul>
          </div>
        )}
        {result.removed.length > 0 && (
          <div className="note note-info">
            <strong>Positions no longer needed</strong>
            <ul>
              {result.removed.map((row) => (
                <li key={`${row.tn}-${row.angle}`}>
                  TN {row.tn}
                  {row.angle ? ` · ${row.angle}°` : ""} · station {row.station ?? "—"}
                </li>
              ))}
            </ul>
          </div>
        )}
        <p>
          <strong>{result.kept}</strong> tool(s) already on the cart still apply and stay.
        </p>
      </div>
    </Dialog>
  );
}

function ComponentTable({ rows, onChoose, clearance }: { rows: KitComponent[]; onChoose?: (component: KitComponent) => void; clearance?: string | null }) {
  if (rows.length === 0) return <p className="muted">Nothing to pick for this change.</p>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Station</th>
            <th>TN</th>
            <th>Angle</th>
            <th>Part</th>
            {clearance !== undefined && <th>Clearance</th>}
            <th>Action</th>
            <th>Tool</th>
            <th className="hide-sm">From</th>
            <th>State</th>
            {onChoose && <th />}
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.component_id}>
              <td>{c.station ?? "—"}</td>
              <td>
                <strong>{c.tn}</strong>
              </td>
              <td>{c.angle ?? "—"}</td>
              <td>{CATEGORY_LABEL[c.category] ?? c.category}</td>
              {clearance !== undefined && <td>{c.category === "matriz" ? clearance ?? "—" : "—"}</td>}
              <td>{ACTION_LABEL[c.action] ?? c.action}</td>
              <td>{c.tool_id ?? "—"}</td>
              <td className="hide-sm muted">{c.source_location ?? "—"}</td>
              <td>
                <Chip text={COMPONENT_STATE_LABEL[c.state] ?? c.state} className={c.state === "ready" ? "st-onmachine" : c.state === "cancelled" ? "st-notstarted" : "st-kitting"} />
              </td>
              {onChoose && (
                <td className="row-actions">
                  <button className="btn" onClick={() => onChoose(c)}>
                    {c.state === "ready" ? "Change" : "Choose"}
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChooseDialog({ kit, component, onClose, onChosen }: { kit: Kit; component: KitComponent; onClose: () => void; onChosen: (kit: Kit) => void }) {
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [unplaced, setUnplaced] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Candidate[]>("GET", `/workflow/kits/${encodeURIComponent(kit.id)}/components/${component.component_id}/candidates`)
      .then((rows) => {
        setCandidates(rows);
        if (rows.length === 0) {
          // Tools of this TN that exist but have no rack recorded are the usual reason nothing shows up.
          api<{ items: { tn: string | null; estado: string }[] }>("GET", `/inventario/herramientas?q=${encodeURIComponent(component.tn)}&categoria=${component.category}&ubicacion=-&limit=200`)
            .then((page) => setUnplaced(page.items.filter((row) => row.tn === component.tn && row.estado === "disponible").length))
            .catch(() => setUnplaced(0));
        }
      })
      .catch((caught) => setError(caught instanceof ApiError ? caught.message : "Could not load the available tools."));
  }, [kit.id, component.component_id, component.tn, component.category]);

  async function pick(candidate: Candidate) {
    setError(null);
    try {
      onChosen(await api<Kit>("POST", `/workflow/kits/${encodeURIComponent(kit.id)}/components/${component.component_id}/select`, { herramienta_id: candidate.id, realizado_por: "web" }));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "That tool could not be selected.");
    }
  }

  return (
    <Dialog title={`${component.tn} · ${CATEGORY_LABEL[component.category] ?? component.category} (station ${component.station ?? "—"})`} onClose={onClose} wide>
      <ErrorNote message={error} />
      {!candidates && !error && <p className="muted">Loading…</p>}
      {candidates && candidates.length === 0 && (
        <div className="note note-warn">
          No tool of TN {component.tn} is in a rack{component.category === "matriz" ? " with the right clearance" : ""}.
          {unplaced > 0 && (
            <>
              {" "}
              {unplaced} in stock {unplaced === 1 ? "has" : "have"} no rack recorded.{" "}
              <button className="link-btn" onClick={() => go("/tools/store")}>
                Place them in a rack
              </button>
            </>
          )}
        </div>
      )}
      {candidates && candidates.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Tool ID</th>
                <th>Type</th>
                <th>Size</th>
                <th>Clearance</th>
                <th>Life</th>
                <th>Rack</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {candidates.map((c) => (
                <tr key={c.id}>
                  <td>
                    <strong>{c.id}</strong>
                  </td>
                  <td>{c.type ?? "—"}</td>
                  <td>{c.size ?? "—"}</td>
                  <td>{c.clearance ?? "—"}</td>
                  <td>{c.life ?? "—"}</td>
                  <td>{c.location}</td>
                  <td className="row-actions">
                    <button className="btn btn-primary" onClick={() => void pick(c)}>
                      Use this one
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Dialog>
  );
}
