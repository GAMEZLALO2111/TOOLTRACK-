import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { Dialog, ErrorNote } from "../components/Dialog";
import { Chip } from "../components/MachineCard";
import { Qr } from "../components/Qr";
import { ReasonDialog } from "../components/ReasonDialog";
import { ACTION_LABEL, CATEGORY_LABEL, COMPONENT_STATE_LABEL, KIT_STATE_CLASS, KIT_STATE_LABEL } from "../labels";
import { go } from "../router";
import type { Candidate, DeliveryResult, Kit, KitComponent, KitQr } from "../types";
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

  return (
    <section className="page">
      <div className="board-bar">
        <h1>{kit ? `Kit for ${kit.machine}` : "Kit"}</h1>
        <div className="bar-right">
          <button className="btn" onClick={() => go("/kitting")}>
            ← Kitting
          </button>
          <button className="btn" onClick={() => void load()}>
            Refresh
          </button>
        </div>
      </div>
      <ErrorNote message={error} />
      {!kit && !error && <p className="muted">Loading…</p>}

      {kit && (
        <>
          <div className="panel kit-head">
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
                  <button className="btn btn-primary" onClick={() => go("/kitting/pending")}>
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
          <ComponentTable rows={needed} onChoose={open && canOperate ? setChoosing : undefined} />
          {kept.length > 0 && (
            <details className="kept">
              <summary>Tools that stay on the machine ({kept.length})</summary>
              <ComponentTable rows={kept} />
            </details>
          )}
        </>
      )}

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
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    input.current?.focus();
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const id = value.trim();
    if (!id) return;
    setBusy(true);
    try {
      const next = await api<Kit>("POST", `/workflow/kits/${encodeURIComponent(kitId)}/scan`, { herramienta_id: id, realizado_por: "web" });
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
        <input ref={input} value={value} onChange={(event) => setValue(event.target.value)} placeholder="Scan the tool, then Enter" autoComplete="off" autoCapitalize="characters" disabled={disabled || busy} aria-label="Tool ID" />
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
          <p className="muted small">At the machine: scan the machine QR first, then the kit QR.</p>
          <label>
            Machine QR (or code)
            <input value={machineScan} onChange={(event) => setMachineScan(event.target.value)} placeholder={`TOOLTRACK-MACHINE:${kit.machine}`} autoComplete="off" />
          </label>
          <label>
            Kit QR
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

function ComponentTable({ rows, onChoose }: { rows: KitComponent[]; onChoose?: (component: KitComponent) => void }) {
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
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Candidate[]>("GET", `/workflow/kits/${encodeURIComponent(kit.id)}/components/${component.component_id}/candidates`)
      .then(setCandidates)
      .catch((caught) => setError(caught instanceof ApiError ? caught.message : "Could not load the available tools."));
  }, [kit.id, component.component_id]);

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
      {candidates && candidates.length === 0 && <p className="note note-warn">No available tools in the racks match this TN{component.category === "matriz" ? " and clearance" : ""}.</p>}
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
