import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { Dialog, ErrorNote } from "../components/Dialog";
import type { Family, Planning, Program, ProgramPreview } from "../types";

export const PART_FORMAT = /^[A-Z]{2,6}-\d{5,9}(?:-\d{2})?_\d{2}-[A-Z]{1,4}$/;
const PART_IN_FILE = /^([A-Z]{2,6}-\d{5,9}(?:-\d{2})?_\d{2}-[A-Z]{1,4})/i;

export function fmt(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : String(value);
}

export function PlanningBox({ planning }: { planning: Planning }) {
  return (
    <div className="facts">
      <div>
        <div className="muted small">Pieces per blank</div>
        {fmt(planning.piezas_blank)}
      </div>
      <div>
        <div className="muted small">Cycle (s per blank)</div>
        {fmt(planning.cycle_time_s)}
      </div>
      <div>
        <div className="muted small">Cycle + lead time (s)</div>
        {fmt(planning.cycle_con_lead_s)}
      </div>
      <div>
        <div className="muted small">Theoretical UPH</div>
        {fmt(planning.uph_teorico)}
      </div>
    </div>
  );
}

interface Props {
  type: string;
  existing?: Program | null;
  onClose: () => void;
  onSaved: (part: string, revision: number) => void;
}

/** Cargar un .stp: lee el archivo en el navegador, muestra qué contiene y qué cambia, y guarda la revisión. */
export function UploadDialog({ type, existing, onClose, onSaved }: Props) {
  const [part, setPart] = useState(existing?.numero_parte ?? "");
  const [fileName, setFileName] = useState("");
  const [content, setContent] = useState("");
  const [family, setFamily] = useState(existing?.familia ?? "");
  const [kind, setKind] = useState(existing?.tipo_programa ?? "SUSTAINING");
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<ProgramPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [families, setFamilies] = useState<Family[]>([]);
  const [clients, setClients] = useState<string[]>([]);
  const [adding, setAdding] = useState<"client" | "family" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadLists = useCallback(async () => {
    try {
      const [f, c] = await Promise.all([api<Family[]>("GET", "/workflow/programs/families"), api<string[]>("GET", "/workflow/programs/clients")]);
      setFamilies(f);
      setClients(c);
    } catch {
      /* el formulario avisa al guardar */
    }
  }, []);
  useEffect(() => {
    void loadLists();
  }, [loadLists]);

  const partOk = PART_FORMAT.test(part.trim().toUpperCase());
  useEffect(() => {
    setPreview(null);
    setPreviewError(null);
    if (!content || !partOk) return;
    const timer = setTimeout(() => {
      api<ProgramPreview>("POST", `/workflow/programs/${type}/${encodeURIComponent(part.trim().toUpperCase())}/preview`, { nombre_archivo: fileName || null, contenido: content })
        .then(setPreview)
        .catch((caught) => setPreviewError(caught instanceof ApiError ? caught.message : "The file could not be read."));
    }, 250);
    return () => clearTimeout(timer);
  }, [content, part, partOk, type, fileName]);

  async function chooseFile(file: File | undefined) {
    if (!file) return;
    setFileName(file.name);
    setContent(await file.text());
    const match = PART_IN_FILE.exec(file.name);
    if (match && !existing && !part) setPart(match[1].toUpperCase());
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const np = part.trim().toUpperCase();
    if (!partOk) return setError("The part number must look like TYEH-1193217_01-SW.");
    if (!content) return setError("Choose the .stp file.");
    if (!family) return setError("Choose the family/FG.");
    if (preview?.exists && !reason.trim()) return setError("This part number already exists: write the reason for the change.");
    setBusy(true);
    setError(null);
    try {
      const saved = await api<Program>("PUT", `/workflow/programs/${type}/${encodeURIComponent(np)}`, {
        familia: family,
        tipo_programa: kind,
        nombre_archivo: fileName || `${np}.stp`,
        contenido: content,
        usuario: "web",
        motivo: reason.trim() || null,
      });
      onSaved(np, saved.revision);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The program could not be saved.");
      setBusy(false);
    }
  }

  const grouped = clients.map((client) => ({ client, items: families.filter((f) => f.cliente === client) }));
  return (
    <Dialog title={existing ? `New revision of ${existing.numero_parte} (${type})` : `Load a program (${type})`} onClose={onClose} wide>
      <form className="form" onSubmit={submit}>
        <div className="two-col">
          <label>
            .stp file
            <input type="file" accept=".stp,.txt,text/plain" onChange={(event) => void chooseFile(event.target.files?.[0])} />
            {fileName && <small className="muted">{fileName}</small>}
          </label>
          <label>
            Part number
            <input value={part} onChange={(event) => setPart(event.target.value)} disabled={!!existing} placeholder="TYEH-1193217_01-SW" autoCapitalize="characters" />
            {part && !partOk && <small className="warn-text">Format: TYEH-1193217_01-SW</small>}
          </label>
          <label>
            Family / FG
            <select value={family} onChange={(event) => setFamily(event.target.value)}>
              <option value="">Select…</option>
              {grouped.map(({ client, items }) => (
                <optgroup key={client} label={client}>
                  {items.map((f) => (
                    <option key={f.nombre} value={f.nombre}>
                      {f.nombre}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
            <span className="pair">
              <button type="button" className="link-btn" onClick={() => setAdding("client")}>
                + client
              </button>
              <button type="button" className="link-btn" onClick={() => setAdding("family")}>
                + family
              </button>
            </span>
          </label>
          <label>
            Program type
            <select value={kind} onChange={(event) => setKind(event.target.value)}>
              <option value="NPI">NPI</option>
              <option value="SUSTAINING">Sustaining</option>
            </select>
          </label>
        </div>

        {previewError && <ErrorNote message={previewError} />}
        {preview && (
          <div className="panel">
            <h2>What the file contains</h2>
            <div className="facts">
              <div>
                <div className="muted small">Drawing</div>
                {preview.drawing_name ?? "—"}
              </div>
              <div>
                <div className="muted small">Material</div>
                {preview.material ?? "—"}
              </div>
              <div>
                <div className="muted small">Thickness</div>
                {preview.thickness} in
              </div>
              <div>
                <div className="muted small">Stations</div>
                {preview.requisitos.length}
              </div>
            </div>
            <PlanningBox planning={preview.planeacion} />
            {preview.exists ? (
              <>
                <p>
                  <strong>
                    This part number already exists (revision {preview.revision}). Saving creates revision {preview.next_revision}.
                  </strong>
                </p>
                <pre className="diff">{preview.diff}</pre>
                <label>
                  Reason for the change (required)
                  <input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Sequence change, tool change, notes…" />
                </label>
              </>
            ) : (
              <p className="muted">New part number for {type}: it will be created as revision 1.</p>
            )}
          </div>
        )}

        <ErrorNote message={error} />
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy || !preview}>
            {preview?.exists ? `Save revision ${preview.next_revision}` : "Save program"}
          </button>
        </div>
      </form>
      {adding && (
        <AddDialog
          kind={adding}
          clients={clients}
          onClose={() => setAdding(null)}
          onSaved={async (name) => {
            setAdding(null);
            await loadLists();
            if (adding === "family") setFamily(name);
          }}
        />
      )}
    </Dialog>
  );
}

function AddDialog({ kind, clients, onClose, onSaved }: { kind: "client" | "family"; clients: string[]; onClose: () => void; onSaved: (name: string) => void }) {
  const [name, setName] = useState("");
  const [client, setClient] = useState(clients[0] ?? "");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return setError("Enter a name.");
    try {
      if (kind === "client") await api("POST", "/workflow/programs/clients", { nombre: name.trim() });
      else await api("POST", "/workflow/programs/families", { nombre: name.trim(), cliente: client });
      onSaved(name.trim().toUpperCase());
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "It could not be saved.");
    }
  }

  return (
    <Dialog title={kind === "client" ? "New client" : "New family / FG"} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        {kind === "family" && (
          <label>
            Client
            <select value={client} onChange={(event) => setClient(event.target.value)}>
              {clients.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          {kind === "client" ? "Client name (e.g. CIENA)" : "Family / FG name"}
          <input value={name} onChange={(event) => setName(event.target.value)} autoFocus />
        </label>
        <ErrorNote message={error} />
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary">
            Save
          </button>
        </div>
      </form>
    </Dialog>
  );
}
