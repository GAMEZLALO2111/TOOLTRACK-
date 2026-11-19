import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { ConfirmDialog, Dialog, ErrorNote } from "../components/Dialog";
import { DisabledBanner } from "../components/MachineCard";
import { go } from "../router";
import { KIT_STATE_LABEL } from "../labels";
import type { MachineProfile, MachineStation, RunningMachine } from "../types";
import { usePoll } from "../usePoll";

type ProfileForm = Pick<
  MachineProfile,
  | "tipo_maquina"
  | "edificio"
  | "linea"
  | "capacidad"
  | "espesor_min"
  | "espesor_max"
  | "blank_max"
  | "area_pallet"
  | "notas"
>;

interface LocationRow {
  codigo: string;
  nombre: string;
  activa: boolean;
  estandar: boolean;
  tipo: string;
}

interface KitOverview {
  machines: RunningMachine[];
}

interface AvailabilityEvent {
  id: number;
  action: "disabled" | "enabled";
  type: string | null;
  reason: string | null;
  expected_return: string | null;
  user: string;
  at: string;
}

const DISABLE_LABEL: Record<string, string> = { maintenance: "Maintenance", unavailable: "Unavailable", other: "Other" };

function DisableDialog({ code, onClose, onDone }: { code: string; onClose: () => void; onDone: () => void }) {
  const [kind, setKind] = useState("maintenance");
  const [reason, setReason] = useState("");
  const [returns, setReturns] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    if (reason.trim().length < (kind === "other" ? 5 : 3)) return setError("Write why the machine is being disabled.");
    setBusy(true);
    try {
      await api("POST", `/workflow/machine-profiles/${encodeURIComponent(code)}/disable`, {
        tipo: kind, motivo: reason.trim(), regreso_estimado: returns || null,
      });
      onDone();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The machine could not be disabled.");
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={`Disable ${code}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Back
          </button>
          <button className="btn btn-danger" onClick={() => void submit()} disabled={busy}>
            {busy ? "Saving…" : "Disable machine"}
          </button>
        </>
      }
    >
      <p className="muted">While it is disabled, Planning does not count it and it accepts no new kits, demands or tickets. What it is running now stays as it is.</p>
      <div className="form">
        <label>
          Reason
          <select value={kind} onChange={(event) => setKind(event.target.value)}>
            {Object.entries(DISABLE_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          {kind === "other" ? "Explain (required)" : "Details"}
          <input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={200} autoFocus placeholder={kind === "maintenance" ? "e.g. Hydraulic repair" : ""} />
        </label>
        <label>
          Expected back (optional)
          <input type="date" value={returns} onChange={(event) => setReturns(event.target.value)} />
        </label>
        <ErrorNote message={error} />
      </div>
    </Dialog>
  );
}

const PROFILE_FIELDS: { key: keyof ProfileForm; label: string }[] = [
  { key: "tipo_maquina", label: "Machine type / model" },
  { key: "edificio", label: "Building" },
  { key: "linea", label: "Line / area" },
  { key: "capacidad", label: "Capacity" },
  { key: "espesor_min", label: "Minimum thickness (in)" },
  { key: "espesor_max", label: "Maximum thickness (in)" },
  { key: "blank_max", label: "Maximum blank" },
  { key: "area_pallet", label: "Pallet area" },
];

export function MachinesPage({ code }: { code: string | null }) {
  const { can, session } = useAuth();
  const [machines, setMachines] = useState<MachineProfile[] | null>(null);
  const [running, setRunning] = useState<RunningMachine[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [transferring, setTransferring] = useState(false);
  const [editingStation, setEditingStation] = useState<MachineStation | null>(null);
  const [deletingStation, setDeletingStation] = useState<MachineStation | null>(null);
  const [disabling, setDisabling] = useState(false);
  const [history, setHistory] = useState<AvailabilityEvent[]>([]);

  const load = useCallback(async () => {
    try {
      const [profiles, overview] = await Promise.all([
        api<MachineProfile[]>("GET", "/workflow/machine-profiles"),
        // Roles without kitting access (Punzonado) still see the profiles, without the live kit status.
        api<KitOverview>("GET", "/workflow/kitting/overview").catch((caught) => {
          if (caught instanceof ApiError && caught.status === 403) return null;
          throw caught;
        }),
      ]);
      setMachines(profiles);
      setRunning(overview?.machines ?? []);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the machines.");
    }
  }, []);
  usePoll(load, 30000);

  const runningByCode = useMemo(() => new Map(running.map((row) => [row.machine, row])), [running]);
  const machine = machines?.find((row) => row.codigo === code) ?? null;
  const disabledSince = machine?.disabled?.since ?? null;
  useEffect(() => {
    if (!code) return setHistory([]);
    api<AvailabilityEvent[]>("GET", `/workflow/machine-profiles/${encodeURIComponent(code)}/availability-history`)
      .then(setHistory)
      .catch(() => setHistory([]));
  }, [code, disabledSince, machines]);
  const filteredMachines = useMemo(() => {
    const text = search.trim().toLowerCase();
    if (!text) return machines ?? [];
    return (machines ?? []).filter((row) =>
      [row.codigo, row.nombre, row.tipo_maquina, row.edificio, row.linea]
        .some((value) => value?.toLowerCase().includes(text)),
    );
  }, [machines, search]);

  async function createMachine(payload: { codigo: string; nombre: string }) {
    setActionError(null);
    try {
      const result = await api<LocationRow>("POST", "/workflow/locations", {
        ...payload,
        tipo: "machine",
      });
      setCreating(false);
      setMessage(`Machine ${result.codigo} was created.`);
      await load();
      go(`/machines/${encodeURIComponent(result.codigo)}`);
    } catch (caught) {
      throw caught instanceof ApiError ? caught : new Error("The machine could not be created.");
    }
  }

  async function enableMachine() {
    if (!machine) return;
    setActionError(null);
    try {
      await api("POST", `/workflow/machine-profiles/${encodeURIComponent(machine.codigo)}/enable`, {});
      setMessage(`${machine.codigo} is enabled again.`);
      await load();
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.message : "The machine could not be enabled.");
    }
  }

  async function updateStandard(next: boolean) {
    if (!machine) return;
    setActionError(null);
    try {
      await api("PATCH", `/workflow/locations/${encodeURIComponent(machine.codigo)}/standard`, { estandar: next });
      setMessage(`Standard machine setting updated for ${machine.codigo}.`);
      await load();
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.message : "The machine setting could not be updated.");
    }
  }

  async function setStationState(station: MachineStation, state: MachineStation["estado"]) {
    if (!machine) return;
    setActionError(null);
    try {
      await api(
        "PATCH",
        `/workflow/machine-profiles/${encodeURIComponent(machine.codigo)}/stations/${encodeURIComponent(station.numero)}`,
        { estado: state, ...(state === "disponible" ? { nota: "" } : {}) },
      );
      setMessage(`Station ${station.numero} marked ${state === "danada" ? "damaged" : "available"}.`);
      await load();
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.message : "The station state could not be updated.");
    }
  }

  async function deleteStation() {
    if (!machine || !deletingStation) return;
    const station = deletingStation;
    setDeletingStation(null);
    setActionError(null);
    try {
      await api(
        "DELETE",
        `/workflow/machine-profiles/${encodeURIComponent(machine.codigo)}/stations/${encodeURIComponent(station.numero)}`,
      );
      setMessage(`Station ${station.numero} was deleted.`);
      await load();
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.message : "The station could not be deleted.");
    }
  }

  async function updateStation(station: MachineStation) {
    if (!machine) return;
    setEditingStation(null);
    setActionError(null);
    try {
      await api(
        "PATCH",
        `/workflow/machine-profiles/${encodeURIComponent(machine.codigo)}/stations/${encodeURIComponent(station.numero)}`,
        {
          tamano: station.tamano,
          posicion: station.posicion,
          autoindex: station.autoindex,
          estado: station.estado,
          nota: station.nota,
        },
      );
      setMessage(`Station ${station.numero} was updated.`);
      await load();
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.message : "The station could not be updated.");
      setEditingStation(station);
    }
  }

  async function addStation(station: Omit<MachineStation, "estado">) {
    if (!machine) return;
    setActionError(null);
    try {
      await api(
        "POST",
        `/workflow/machine-profiles/${encodeURIComponent(machine.codigo)}/stations`,
        { ...station, estado: "disponible" },
      );
      setMessage(`Station ${station.numero} was added.`);
      await load();
    } catch (caught) {
      throw caught instanceof ApiError ? caught : new Error("The station could not be added.");
    }
  }

  async function transferPart(result: { numero_parte: string; origen: string; destino: string; herramientas_movidas: number }) {
    setTransferring(false);
    setMessage(
      `${result.numero_parte} transferred from ${result.origen} to ${result.destino}. ` +
      `${result.herramientas_movidas} tool(s) moved.`,
    );
    setActionError(null);
    await load();
  }

  if (!machines) {
    return (
      <section className="page">
        <div className="board-bar"><h1>Machines</h1></div>
        <ErrorNote message={error} />
        {!error ? <p className="muted">Loading machines…</p> : <button className="btn" onClick={() => void load()}>Try again</button>}
      </section>
    );
  }

  if (code) {
    if (!machine) {
      return (
        <section className="page">
          <div className="board-bar">
            <h1>Machine {code}</h1>
            <button className="btn" onClick={() => go("/machines")}>← Machines</button>
          </div>
          <ErrorNote message={error ?? `Machine ${code} was not found.`} />
        </section>
      );
    }
    const live = runningByCode.get(machine.codigo);
    return (
      <section className="page">
        <div className="board-bar">
          <div>
            <button className="link-btn" onClick={() => go("/machines")}>← All machines</button>
            <h1>{machine.codigo}</h1>
          </div>
          <div className="bar-right">
            {can("machine.availability") && machine.activa && (
              machine.disabled ? (
                <button className="btn btn-primary" onClick={() => void enableMachine()}>Enable machine</button>
              ) : (
                <button className="btn btn-ghost-danger" onClick={() => setDisabling(true)}>Disable…</button>
              )
            )}
            {can("kitting.operate") && machine.activa && live?.numero_parte && (
              <button className="btn btn-primary" onClick={() => setTransferring(true)}>Transfer part</button>
            )}
            <button className="btn" onClick={() => void load()}>Refresh</button>
          </div>
        </div>
        <ErrorNote message={error} />
        <ErrorNote message={actionError} />
        {message && <div className="note note-info">{message}</div>}
        {machine.disabled && <DisabledBanner disabled={machine.disabled} />}
        <section className="machine-facts panel" aria-label="Machine status">
          <Fact label="Operational state" value={!machine.activa ? "Inactive" : machine.disabled ? `Disabled · ${DISABLE_LABEL[machine.disabled.type] ?? machine.disabled.type}` : "Active"} />
          <Fact label="Running part number" value={live?.numero_parte ?? "No part registered"} />
          <Fact label="Machine type" value={machine.tipo_maquina ?? "Not configured"} />
          <Fact label="Current thickness" value={live?.thickness ? `${live.thickness} in` : "—"} />
          <Fact
            label="Kitting"
            value={live?.kit_state ? `${KIT_STATE_LABEL[live.kit_state]} · cart ${live.kit_cart ?? "—"}${live.kit_count && live.kit_count > 1 ? ` (+${live.kit_count - 1} in line)` : ""}` : "No open kit"}
          />
          <Fact label="Stations" value={`${machine.estaciones_disponibles} available · ${machine.estaciones_danadas} damaged`} />
        </section>

        <div className="machine-section-head">
          <h2>Machine profile</h2>
          {can("locations.manage") && machine.activa && (
            <label className="check">
              <input
                type="checkbox"
                checked={machine.estandar}
                onChange={(event) => void updateStandard(event.target.checked)}
              />
              Standard turret machine
            </label>
          )}
        </div>
        {can("machine.manage") ? (
          <ProfileEditor key={machine.codigo} machine={machine} onSaved={() => void load()} />
        ) : (
          <div className="panel machine-profile-readonly">
            {PROFILE_FIELDS.map(({ key, label }) => (
              <Fact key={key} label={label} value={machine[key] || "—"} />
            ))}
            {machine.notas && <Fact label="Notes" value={machine.notas} />}
          </div>
        )}

        <div className="machine-section-head">
          <div>
            <h2>Stations</h2>
          </div>
        </div>
        {can("machine.manage") && machine.activa && (
          <StationCreateForm onCreate={addStation} />
        )}
        {machine.estaciones.length === 0 ? (
          <p className="note note-info">No stations are registered for this machine.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Station</th><th>Size</th><th>Position</th><th>AutoIndex</th><th>State</th><th>Note</th>
                  {can("machine.manage") && <th />}
                </tr>
              </thead>
              <tbody>
                {machine.estaciones.map((station) => (
                  <tr key={station.numero} className={station.estado === "danada" ? "inactive" : ""}>
                    <td><strong>{station.numero}</strong></td>
                    <td>{station.tamano ?? "—"}</td>
                    <td>{station.posicion ?? "—"}</td>
                    <td>{station.autoindex ? "Yes" : "No"}</td>
                    <td>{station.estado === "danada" ? "Damaged" : "Available"}</td>
                    <td>{station.nota ?? "—"}</td>
                    {can("machine.manage") && (
                      <td className="row-actions">
                        <button className="btn" onClick={() => setEditingStation(station)}>Edit</button>
                        <button
                          className="btn"
                          onClick={() => void setStationState(station, station.estado === "danada" ? "disponible" : "danada")}
                        >
                          {station.estado === "danada" ? "Mark available" : "Mark damaged"}
                        </button>
                        <button className="btn btn-ghost-danger" onClick={() => setDeletingStation(station)}>Delete</button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!can("machine.manage") && <p className="muted small">Your role can view machine profiles but cannot edit them.</p>}

        {history.length > 0 && (
          <>
            <div className="machine-section-head">
              <h2>Availability history</h2>
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Event</th>
                    <th>Reason</th>
                    <th>Expected back</th>
                    <th>By</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((event) => (
                    <tr key={event.id}>
                      <td>{new Date(event.at).toLocaleString()}</td>
                      <td>{event.action === "disabled" ? `Disabled · ${DISABLE_LABEL[event.type ?? ""] ?? event.type}` : "Enabled"}</td>
                      <td>{event.reason ?? "—"}</td>
                      <td>{event.expected_return ?? "—"}</td>
                      <td>{event.user}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        {disabling && (
          <DisableDialog
            code={machine.codigo}
            onClose={() => setDisabling(false)}
            onDone={() => {
              setDisabling(false);
              setMessage(`${machine.codigo} is disabled.`);
              void load();
            }}
          />
        )}

        {creating && <CreateMachineDialog onClose={() => setCreating(false)} onCreate={createMachine} />}
        {transferring && live && (
          <TransferDialog
            machines={running}
            origin={live}
            username={session?.username ?? ""}
            onClose={() => setTransferring(false)}
            onTransferred={transferPart}
          />
        )}
        {editingStation && (
          <StationEditor
            station={editingStation}
            onClose={() => setEditingStation(null)}
            onSave={updateStation}
          />
        )}
        {deletingStation && (
          <ConfirmDialog
            title="Delete station"
            message={`Delete station ${deletingStation.numero} from ${machine.codigo}? Stations referenced by saved setups are protected and cannot be deleted.`}
            confirmLabel="Delete station"
            danger
            onClose={() => setDeletingStation(null)}
            onConfirm={() => void deleteStation()}
          />
        )}
      </section>
    );
  }

  return (
    <section className="page">
      <div className="board-bar">
        <h1>Machines</h1>
        <div className="bar-right">
          {can("locations.manage") && <button className="btn btn-primary" onClick={() => setCreating(true)}>Add machine</button>}
          <button className="btn" onClick={() => void load()}>Refresh</button>
        </div>
      </div>
      <ErrorNote message={error} />
      {message && <div className="note note-info">{message}</div>}
      <div className="filters">
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Filter by machine, type, building or line" aria-label="Filter machines" />
      </div>
      {filteredMachines.length === 0 ? (
        <p className="note note-info">{machines.length === 0 ? "No machines are registered." : "No machines match the filter."}</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Machine</th><th>State</th><th>Type</th><th>Building / line</th><th>Running part</th>
                <th>Thickness</th><th>Stations</th><th>Kitting</th><th />
              </tr>
            </thead>
            <tbody>
              {filteredMachines.map((row) => {
                const live = runningByCode.get(row.codigo);
                return (
                  <tr key={row.codigo} className={!row.activa ? "inactive" : ""}>
                    <td>
                      <strong>{row.codigo}</strong>
                      {row.estandar && <div className="muted small">Standard machine</div>}
                    </td>
                    <td>{!row.activa ? "Inactive" : row.disabled ? <span className="chip st-missing">Disabled · {DISABLE_LABEL[row.disabled.type] ?? row.disabled.type}</span> : "Active"}</td>
                    <td>{row.tipo_maquina ?? "Not configured"}</td>
                    <td>{[row.edificio, row.linea].filter(Boolean).join(" · ") || "—"}</td>
                    <td>{live?.numero_parte ?? "—"}</td>
                    <td>{live?.thickness ? `${live.thickness} in` : "—"}</td>
                    <td>{row.estaciones_disponibles}/{row.estaciones_total} available</td>
                    <td>{live?.kit_state ? `${KIT_STATE_LABEL[live.kit_state]} · ${live.kit_cart ?? ""}${live.kit_count && live.kit_count > 1 ? ` (+${live.kit_count - 1})` : ""}` : "—"}</td>
                    <td className="row-actions">
                      <button className="btn" onClick={() => go(`/machines/${encodeURIComponent(row.codigo)}`)}>Details</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {creating && <CreateMachineDialog onClose={() => setCreating(false)} onCreate={createMachine} />}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="muted small">{label}</div>
      <strong>{value}</strong>
    </div>
  );
}

function ProfileEditor({ machine, onSaved }: { machine: MachineProfile; onSaved: () => void }) {
  const [form, setForm] = useState<ProfileForm>(() => ({
    tipo_maquina: machine.tipo_maquina,
    edificio: machine.edificio,
    linea: machine.linea,
    capacidad: machine.capacidad,
    espesor_min: machine.espesor_min,
    espesor_max: machine.espesor_max,
    blank_max: machine.blank_max,
    area_pallet: machine.area_pallet,
    notas: machine.notas,
  }));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("PUT", `/workflow/machine-profiles/${encodeURIComponent(machine.codigo)}`, form);
      onSaved();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The machine profile could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel" onSubmit={submit}>
      <div className="form two-col">
        {PROFILE_FIELDS.map(({ key, label }) => (
          <label key={key}>
            {label}
            <input
              list={key === "tipo_maquina" ? "known-machine-types" : undefined}
              value={form[key] ?? ""}
              onChange={(event) => setForm({ ...form, [key]: event.target.value })}
            />
          </label>
        ))}
        <datalist id="known-machine-types">
          <option value="4 Index" /><option value="2 Index" /><option value="45 STA" />
        </datalist>
        <label className="span-2">
          Notes
          <textarea value={form.notas ?? ""} onChange={(event) => setForm({ ...form, notas: event.target.value })} rows={3} />
        </label>
      </div>
      <ErrorNote message={error} />
      <div className="form-actions">
        <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? "Saving…" : "Save profile"}</button>
      </div>
    </form>
  );
}

function CreateMachineDialog({ onClose, onCreate }: {
  onClose: () => void;
  onCreate: (payload: { codigo: string; nombre: string }) => Promise<void>;
}) {
  const [codigo, setCode] = useState("");
  const [nombre, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onCreate({ codigo: codigo.trim().toUpperCase(), nombre: nombre.trim() || `Machine ${codigo.trim().toUpperCase()}` });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : caught instanceof Error ? caught.message : "The machine could not be created.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog title="Add machine" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <label>Machine code<input value={codigo} onChange={(event) => setCode(event.target.value)} placeholder="e.g. T-118" required autoFocus /></label>
        <label>Name<input value={nombre} onChange={(event) => setName(event.target.value)} placeholder="Optional" /></label>
        <ErrorNote message={error} />
        <div className="form-actions">
          <button className="btn" type="button" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? "Creating…" : "Create machine"}</button>
        </div>
      </form>
    </Dialog>
  );
}

function StationCreateForm({ onCreate }: {
  onCreate: (station: Omit<MachineStation, "estado">) => Promise<void>;
}) {
  const [numero, setNumber] = useState("");
  const [tamano, setSize] = useState("");
  const [posicion, setPosition] = useState("");
  const [autoindex, setAutoindex] = useState(false);
  const [nota, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onCreate({ numero: numero.trim(), tamano: tamano || null, posicion: posicion || null, autoindex, nota: nota.trim() || null });
      setNumber("");
      setNote("");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : caught instanceof Error ? caught.message : "The station could not be added.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel machine-station-create" onSubmit={submit}>
      <h3>Add station</h3>
      <div className="form two-col">
        <label>Station number<input value={numero} onChange={(event) => setNumber(event.target.value)} required /></label>
        <label>Size<select value={tamano} onChange={(event) => setSize(event.target.value)}><option value="">Not set</option>{["A", "B", "C", "D", "E"].map((size) => <option key={size}>{size}</option>)}</select></label>
        <label>Position<select value={posicion} onChange={(event) => setPosition(event.target.value)}><option value="">Not set</option>{["DENTRO", "CENTRO", "AFUERA"].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label className="check"><input type="checkbox" checked={autoindex} onChange={(event) => setAutoindex(event.target.checked)} />AutoIndex</label>
        <label className="span-2">Note<input value={nota} onChange={(event) => setNote(event.target.value)} /></label>
      </div>
      <ErrorNote message={error} />
      <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? "Adding…" : "Add station"}</button>
    </form>
  );
}

function StationEditor({ station, onClose, onSave }: {
  station: MachineStation;
  onClose: () => void;
  onSave: (station: MachineStation) => Promise<void>;
}) {
  const [form, setForm] = useState(station);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    await onSave(form);
    setBusy(false);
  }

  return (
    <Dialog title={`Edit station ${station.numero}`} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <label>Size<select value={form.tamano ?? ""} onChange={(event) => setForm({ ...form, tamano: event.target.value || null })}><option value="">Not set</option>{["A", "B", "C", "D", "E"].map((size) => <option key={size}>{size}</option>)}</select></label>
        <label>Position<select value={form.posicion ?? ""} onChange={(event) => setForm({ ...form, posicion: event.target.value || null })}><option value="">Not set</option>{["DENTRO", "CENTRO", "AFUERA"].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>State<select value={form.estado} onChange={(event) => setForm({ ...form, estado: event.target.value as MachineStation["estado"] })}><option value="disponible">Available</option><option value="danada">Damaged</option></select></label>
        <label className="check"><input type="checkbox" checked={form.autoindex} onChange={(event) => setForm({ ...form, autoindex: event.target.checked })} />AutoIndex</label>
        <label>Note<input value={form.nota ?? ""} onChange={(event) => setForm({ ...form, nota: event.target.value || null })} /></label>
        <div className="form-actions"><button className="btn" type="button" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy}>{busy ? "Saving…" : "Save station"}</button></div>
      </form>
    </Dialog>
  );
}

function TransferDialog({ machines, origin, username, onClose, onTransferred }: {
  machines: RunningMachine[];
  origin: RunningMachine;
  username: string;
  onClose: () => void;
  onTransferred: (result: { numero_parte: string; origen: string; destino: string; herramientas_movidas: number }) => Promise<void>;
}) {
  const destinations = machines.filter((row) =>
    row.machine !== origin.machine && row.tipo === origin.tipo && !row.numero_parte && !row.kit_state,
  );
  const [destino, setDestination] = useState(destinations[0]?.machine ?? "");
  const [motivo, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!destino) return setError("Choose an available destination machine.");
    if (!motivo.trim()) return setError("A transfer reason is required.");
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ numero_parte: string; origen: string; destino: string; herramientas_movidas: number }>(
        "POST",
        "/workflow/machines/transfer",
        { origen: origin.machine, destino, motivo: motivo.trim(), realizado_por: username },
      );
      await onTransferred(result);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The part could not be transferred.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog title="Transfer running part" onClose={onClose}>
      {destinations.length === 0 ? (
        <p className="note note-info">There is no empty, compatible machine available for this transfer.</p>
      ) : (
        <form className="form" onSubmit={submit}>
          <p>Move <strong>{origin.numero_parte}</strong> and its mounted tools from <strong>{origin.machine}</strong>.</p>
          <label>Destination machine<select value={destino} onChange={(event) => setDestination(event.target.value)}>{destinations.map((row) => <option key={row.machine} value={row.machine}>{row.machine} · {row.tipo}</option>)}</select></label>
          <label>Reason (required)<textarea value={motivo} onChange={(event) => setReason(event.target.value)} rows={3} required /></label>
          <ErrorNote message={error} />
          <div className="form-actions"><button className="btn" type="button" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={busy}>{busy ? "Transferring…" : "Transfer part"}</button></div>
        </form>
      )}
    </Dialog>
  );
}
