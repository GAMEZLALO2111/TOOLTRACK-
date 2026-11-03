import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { MARK_LABEL } from "../labels";
import type { Demand, Machine, ProgramRow } from "../types";
import { Dialog, ErrorNote } from "./Dialog";

interface CreateProps {
  mode: "create";
  machines: Machine[];
  presetMachine?: string;
  onClose: () => void;
  onSaved: () => void;
}

interface EditProps {
  mode: "edit";
  machine: Machine;
  demand: Demand;
  marks: string[];
  onClose: () => void;
  onSaved: () => void;
}

export function DemandForm(props: CreateProps | EditProps) {
  return props.mode === "create" ? <CreateForm {...props} /> : <EditForm {...props} />;
}

function CreateForm({ machines, presetMachine, onClose, onSaved }: CreateProps) {
  const [machine, setMachine] = useState(presetMachine ?? "");
  const [part, setPart] = useState("");
  const [quantity, setQuantity] = useState("");
  const [parts, setParts] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const type = machines.find((m) => m.maquina === machine)?.tipo;
    setPart("");
    if (!type) {
      setParts([]);
      return;
    }
    api<ProgramRow[]>("GET", `/workflow/programs?tipo_maquina=${encodeURIComponent(type)}`)
      .then((rows) => setParts(rows.map((row) => row.numero_parte).sort()))
      .catch((caught) => setError(caught instanceof ApiError ? caught.message : "Could not load part numbers."));
  }, [machine, machines]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const amount = Number(quantity);
    if (!machine || !part.trim()) return setError("Choose the machine and the part number.");
    if (!Number.isInteger(amount) || amount <= 0) return setError("Quantity must be a whole number of pieces.");
    setBusy(true);
    try {
      await api("POST", "/workflow/demandas", {
        maquina_codigo: machine,
        numero_parte: part.trim(),
        cantidad: amount,
        creado_por: "web",
      });
      onSaved();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The demand could not be saved.");
      setBusy(false);
    }
  }

  return (
    <Dialog title="New demand" onClose={onClose}>
      <form onSubmit={submit} className="form">
        <label>
          Machine
          <select value={machine} onChange={(event) => setMachine(event.target.value)} autoFocus>
            <option value="">Select…</option>
            {[...machines]
              .sort((a, b) => a.maquina.localeCompare(b.maquina))
              .map((m) => (
                <option key={m.maquina} value={m.maquina}>
                  {m.maquina} · {m.tipo}
                </option>
              ))}
          </select>
        </label>
        <label>
          Part number
          <input list="part-options" value={part} onChange={(event) => setPart(event.target.value)} placeholder={machine ? "Type to search" : "Choose a machine first"} disabled={!machine} />
          <datalist id="part-options">
            {parts.map((p) => (
              <option key={p} value={p} />
            ))}
          </datalist>
        </label>
        <label>
          Quantity (pieces)
          <input type="number" inputMode="numeric" min={1} max={1000000} value={quantity} onChange={(event) => setQuantity(event.target.value)} />
        </label>
        <ErrorNote message={error} />
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            Save demand
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function EditForm({ machine, demand, marks, onClose, onSaved }: EditProps) {
  const { can } = useAuth();
  const canQty = can("demand.manage");
  const canProgress = can("demand.progress");
  const canMark = can("demand.mark");
  const [quantity, setQuantity] = useState(String(demand.cantidad));
  const [produced, setProduced] = useState(String(demand.piezas_producidas));
  const [mark, setMark] = useState(demand.marca ?? "");
  const [comment, setComment] = useState(demand.comentario ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const payload: Record<string, unknown> = {};
    const qty = Number(quantity);
    const done = Number(produced || 0);
    if (!Number.isInteger(qty) || !Number.isInteger(done)) return setError("Quantity and produced must be whole numbers.");
    if (canQty && qty !== demand.cantidad) payload.cantidad = qty;
    if (canProgress && done !== demand.piezas_producidas) payload.piezas_producidas = done;
    if (canMark) {
      const newMark = mark || null;
      const newComment = comment.trim() || null;
      if (newMark !== demand.marca) payload.marca = newMark;
      if (newComment !== demand.comentario || "marca" in payload) payload.comentario = newComment;
      if (newMark && !newComment) return setError("A mark needs a comment explaining why.");
    }
    if (Object.keys(payload).length === 0) return onClose();
    setBusy(true);
    try {
      await api("PATCH", `/workflow/demandas/${demand.id}`, payload);
      onSaved();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The change could not be saved.");
      setBusy(false);
    }
  }

  return (
    <Dialog title={`Edit ${demand.numero_parte}`} onClose={onClose}>
      <form onSubmit={submit} className="form">
        <div className="muted">
          {machine.maquina} · {machine.tipo}
        </div>
        <label>
          Quantity (pieces)
          <input type="number" inputMode="numeric" value={quantity} onChange={(event) => setQuantity(event.target.value)} disabled={!canQty} />
        </label>
        <label>
          Produced (pieces)
          <input type="number" inputMode="numeric" value={produced} onChange={(event) => setProduced(event.target.value)} disabled={!canProgress} />
        </label>
        <label>
          Mark
          <select value={mark} onChange={(event) => setMark(event.target.value)} disabled={!canMark}>
            <option value="">None</option>
            {marks.map((m) => (
              <option key={m} value={m}>
                {MARK_LABEL[m] ?? m}
              </option>
            ))}
          </select>
        </label>
        <label>
          Comment
          <textarea rows={3} value={comment} onChange={(event) => setComment(event.target.value)} disabled={!canMark} />
        </label>
        <ErrorNote message={error} />
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            Save changes
          </button>
        </div>
      </form>
    </Dialog>
  );
}
