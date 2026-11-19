import { useState } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { fmtDuration, LEVEL_LABEL } from "../labels";
import type { Demand, Machine } from "../types";
import { Dialog, ErrorNote } from "./Dialog";
import { MachineOperatorBar, requestNextPart, useOnMachine } from "./MachineOperatorBar";
import { MarkChip, Progress, StatusChip } from "./MachineCard";

interface Props {
  machine: Machine;
  marks: string[];
  onClose: () => void;
  onChanged: () => void;
  onKit: (code: string, part: string) => void;
  onAdd: (code: string) => void;
  onEdit: (demand: Demand) => void;
}

export function QueueDialog({ machine, onClose, onChanged, onKit, onAdd, onEdit }: Props) {
  const { can } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Demand | null>(null);
  const [producing, setProducing] = useState<Demand | null>(null);
  const mine = useOnMachine(machine.operadores);
  const pending = machine.demandas.filter((d) => d.pendiente > 0);
  const done = machine.demandas.filter((d) => d.pendiente === 0);
  const canEdit = can("demand.manage") || can("demand.mark");
  // Tool Crib and Planning record produced pieces on any machine; an operator only on the one he is logged into.
  const canProduce = can("demand.progress") && (mine || can("kitting.operate") || can("demand.manage"));

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      onChanged();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The change could not be saved.");
    }
  }

  function move(demand: Demand, direction: -1 | 1) {
    const index = pending.findIndex((d) => d.id === demand.id);
    const other = pending[index + direction];
    if (!other) return;
    void run(async () => {
      await api("PATCH", `/workflow/demandas/${demand.id}`, { orden: other.orden });
      await api("PATCH", `/workflow/demandas/${other.id}`, { orden: demand.orden });
    });
  }

  function row(demand: Demand, position: number | null) {
    const finished = position === null;
    return (
      <li key={demand.id} className={`q-row${finished ? " finished" : ""}`}>
        {position !== null && <div className="q-pos">{position}</div>}
        <div className="q-main">
          <div className="q-title">
            <strong>{demand.numero_parte}</strong>
            {demand.orden_trabajo && <span className="plan-row-wo">WO {demand.orden_trabajo}</span>}
            <StatusChip status={demand.estado} />
            {demand.es_actual && <span className="chip tag-dark">CURRENT</span>}
            {demand.es_siguiente && <span className="chip tag-slate">NEXT</span>}
            {demand.solicitud_parte && <span className="chip st-kitting">Kit requested</span>}
            <MarkChip mark={demand.marca} />
          </div>
          <div className="muted">
            {demand.piezas_producidas}/{demand.cantidad} pcs · remaining {fmtDuration(demand.hr_pendiente)} · total {fmtDuration(demand.hr)}
            {demand.sin_programa ? " · ⚠ no program" : ""}
          </div>
          <Progress demand={demand} />
          {demand.comentario && <div className="q-comment">💬 {demand.comentario}</div>}
        </div>
        <div className="q-actions">
          {!finished && demand.estado !== "CORRIENDO" && can("kitting.operate") && (
            <button className="btn btn-primary" onClick={() => onKit(machine.maquina, demand.numero_parte)}>
              Kit
            </button>
          )}
          {!finished && mine && demand.estado === "NO INICIADO" && !demand.solicitud_parte && (
            <button className="btn btn-primary" onClick={() => void run(() => requestNextPart(machine.maquina, demand.id))}>
              Request kit
            </button>
          )}
          {canProduce && (
            <button className="btn" onClick={() => setProducing(demand)}>
              Produced
            </button>
          )}
          {canEdit && (
            <button className="btn" onClick={() => onEdit(demand)}>
              Edit
            </button>
          )}
          {!finished && can("demand.manage") && (
            <span className="pair">
              <button className="btn" onClick={() => move(demand, -1)} aria-label="Move up">
                ▲
              </button>
              <button className="btn" onClick={() => move(demand, 1)} aria-label="Move down">
                ▼
              </button>
            </span>
          )}
          {can("demand.manage") && (
            <button className="btn btn-ghost-danger" onClick={() => setRemoving(demand)}>
              Remove
            </button>
          )}
        </div>
      </li>
    );
  }

  return (
    <Dialog
      title={`${machine.maquina} · ${machine.tipo}`}
      onClose={onClose}
      wide
      footer={
        <>
          {can("demand.manage") && (
            <button className="btn btn-primary" onClick={() => onAdd(machine.maquina)}>
              + Add demand
            </button>
          )}
          <button className="btn" onClick={onClose}>
            Close
          </button>
        </>
      }
    >
      <div className={`q-head lvl-${machine.nivel}`}>
        <strong>{machine.hr_restante !== null ? fmtDuration(machine.hr_restante) : LEVEL_LABEL[machine.nivel]}</strong>
        <span>{machine.motivo}</span>
        <span className="q-tie">
          TIE {machine.tie_total.toFixed(1)} HR · covered {machine.tie_cubierto.toFixed(1)} HR
        </span>
      </div>
      <ErrorNote message={error} />
      <MachineOperatorBar code={machine.maquina} operators={machine.operadores ?? []} onChanged={onChanged} showOpen />
      {machine.demandas.length === 0 && <p className="muted">This machine has no demands yet.</p>}
      <ol className="q-list">{pending.map((d, i) => row(d, i + 1))}</ol>
      {done.length > 0 && (
        <>
          <h3 className="q-done-title">Done ({done.length})</h3>
          <ol className="q-list">{done.map((d) => row(d, null))}</ol>
        </>
      )}

      {removing && (
        <RemoveDialog
          demand={removing}
          machine={machine.maquina}
          onClose={() => setRemoving(null)}
          onConfirm={(state, reason) => {
            const target = removing;
            setRemoving(null);
            void run(() => api("DELETE", `/workflow/demandas/${target.id}?estado=${state}&motivo=${encodeURIComponent(reason)}`));
          }}
        />
      )}
      {producing && (
        <ProducedDialog
          demand={producing}
          onClose={() => setProducing(null)}
          onSave={(value) => {
            const target = producing;
            setProducing(null);
            void run(() => api("PATCH", `/workflow/demandas/${target.id}`, { piezas_producidas: value }));
          }}
        />
      )}
    </Dialog>
  );
}

const REMOVE_STATES = [
  { value: "terminada", label: "Finished" },
  { value: "cancelada", label: "Cancelled" },
  { value: "reemplazada", label: "Replaced by another demand" },
];

function RemoveDialog({ demand, machine, onClose, onConfirm }: { demand: Demand; machine: string; onClose: () => void; onConfirm: (state: string, reason: string) => void }) {
  const [state, setState] = useState(demand.pendiente === 0 ? "terminada" : "cancelada");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  function submit() {
    if (reason.trim().length < 3) return setError("Write why the demand leaves the board.");
    onConfirm(state, reason.trim());
  }

  return (
    <Dialog
      title="Remove demand"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Back
          </button>
          <button className="btn btn-danger" onClick={submit}>
            Remove
          </button>
        </>
      }
    >
      <p>
        Remove <strong>{demand.numero_parte}</strong> from {machine}? The {demand.piezas_producidas} pcs already produced stay in the production history.
      </p>
      <div className="form">
        <label>
          Why is it leaving?
          <select value={state} onChange={(event) => setState(event.target.value)}>
            {REMOVE_STATES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Reason (required)
          <input value={reason} onChange={(event) => setReason(event.target.value)} autoFocus maxLength={200} onKeyDown={(event) => event.key === "Enter" && submit()} />
        </label>
      </div>
      <ErrorNote message={error} />
    </Dialog>
  );
}

function ProducedDialog({ demand, onClose, onSave }: { demand: Demand; onClose: () => void; onSave: (value: number) => void }) {
  const [value, setValue] = useState(String(demand.piezas_producidas));
  const [error, setError] = useState<string | null>(null);

  function submit() {
    const amount = Number(value);
    if (!Number.isInteger(amount) || amount < 0) return setError("Enter a whole number of pieces.");
    onSave(amount);
  }

  return (
    <Dialog
      title="Produced pieces"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={submit}>
            Save
          </button>
        </>
      }
    >
      <p>
        Total produced of <strong>{demand.numero_parte}</strong> (demand: {demand.cantidad} pcs)
      </p>
      <input
        className="big-input"
        type="number"
        inputMode="numeric"
        min={0}
        autoFocus
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => event.key === "Enter" && submit()}
      />
      <ErrorNote message={error} />
    </Dialog>
  );
}
