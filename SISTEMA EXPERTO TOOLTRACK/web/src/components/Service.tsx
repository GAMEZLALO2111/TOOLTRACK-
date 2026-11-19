import { useEffect, useState } from "react";
import { api } from "../api";
import { DAMAGE_CAUSES, DAMAGE_MOMENTS, PROGRAM_PROBLEMS, SERVICE_STATE_CLASS, SERVICE_STATE_LABEL } from "../labels";
import type { OperatorOption, ServiceRequest, ToolIncident } from "../types";
import { Chip } from "./MachineCard";

export function when(iso: string | null | undefined): string {
  return iso ? new Date(`${iso}Z`).toLocaleString() : "—";
}

export function causeLabel(value: string | null): string {
  return DAMAGE_CAUSES.find((cause) => cause.value === value)?.label ?? value ?? "—";
}

export function momentLabel(value: string | null): string {
  return DAMAGE_MOMENTS.find((moment) => moment.value === value)?.label ?? value ?? "—";
}

export interface IncidentValues {
  operator: string;
  cause: string;
  moment: string;
}

export function incidentMissing(values: IncidentValues): string | null {
  if (!values.operator) return "Choose who was on the machine.";
  if (!values.cause) return "Choose the cause.";
  if (!values.moment) return "Choose when it happened.";
  return null;
}

/** Who was on the machine, why it happened and when; the shift comes from that operator's profile. */
export function IncidentFields({ values, onChange, showOperator = true }: { values: IncidentValues; onChange: (values: IncidentValues) => void; showOperator?: boolean }) {
  const [operators, setOperators] = useState<OperatorOption[]>([]);
  useEffect(() => {
    api<OperatorOption[]>("GET", "/workflow/operators")
      .then(setOperators)
      .catch(() => setOperators([]));
  }, []);
  const chosen = operators.find((person) => person.username === values.operator);
  return (
    <>
      {showOperator && (
        <label>
          Who was on the machine?
          <select value={values.operator} onChange={(event) => onChange({ ...values, operator: event.target.value })}>
            <option value="">Choose…</option>
            {operators.map((person) => (
              <option key={person.username} value={person.username}>
                {person.full_name}
                {person.employee_number ? ` · ${person.employee_number}` : ""}
              </option>
            ))}
          </select>
          {chosen && <span className="muted small">Shift: {chosen.shift ?? "not set in the profile"}</span>}
        </label>
      )}
      <label>
        Cause
        <select value={values.cause} onChange={(event) => onChange({ ...values, cause: event.target.value })}>
          <option value="">Choose…</option>
          {DAMAGE_CAUSES.map((cause) => (
            <option key={cause.value} value={cause.value}>
              {cause.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        When did it happen?
        <select value={values.moment} onChange={(event) => onChange({ ...values, moment: event.target.value })}>
          <option value="">Choose…</option>
          {DAMAGE_MOMENTS.map((moment) => (
            <option key={moment.value} value={moment.value}>
              {moment.label}
            </option>
          ))}
        </select>
      </label>
    </>
  );
}

export function IncidentSummary({ incident }: { incident: ToolIncident }) {
  return (
    <div className="muted small">
      <div>
        Incident: {causeLabel(incident.cause)} · {momentLabel(incident.moment)} · {incident.operator ?? "—"}
        {incident.shift ? ` (${incident.shift})` : ""}
      </div>
      {incident.finding && <div>Tool Crib found: {incident.finding}</div>}
    </div>
  );
}

export function RequestChip({ request }: { request: Pick<ServiceRequest, "state"> & Partial<Pick<ServiceRequest, "type">> }) {
  const program = request.type === "program_problem";
  const part = request.type === "part_request";
  const text = program && request.state === "open" ? "Waiting for Tool Crib or Engineering" : program && request.state === "attending" ? "Being reviewed" : part && request.state === "attending" ? "Tool Crib is preparing the kit" : (SERVICE_STATE_LABEL[request.state] ?? request.state);
  return <Chip text={text} className={SERVICE_STATE_CLASS[request.state] ?? ""} />;
}

/** Manufacturing can receive the tool once Tool Crib recovered the old one (or, for a tool request, once Tool Crib is on it). */
export function canReceive(request: Pick<ServiceRequest, "state" | "type">): boolean {
  return request.state === "collected" || (request.type === "tool_request" && request.state === "attending");
}

export function PilotNote({ request }: { request: Pick<ServiceRequest, "tn"> & Partial<Pick<ServiceRequest, "pilot_change" | "installed_tn" | "tn_difference">> }) {
  if (!request.pilot_change) return null;
  return (
    <p className="note note-info">
      <strong>Pilot change.</strong> Requested TN {request.tn}
      {request.installed_tn ? ` · installed TN ${request.installed_tn}${request.tn_difference ? ` (${request.tn_difference} in.)` : ""}` : " · any round tool is accepted"}
    </p>
  );
}

export function positionLabel(request: Pick<ServiceRequest, "type" | "station" | "category" | "tn" | "part_number"> & Partial<Pick<ServiceRequest, "work_order" | "quantity" | "pilot_change">>): string {
  if (request.type === "part_request") {
    return `Part number ${request.part_number ?? "—"}${request.work_order ? ` · WO ${request.work_order}` : ""}${request.quantity ? ` · ${request.quantity} pcs` : ""}`;
  }
  if (request.type === "program_problem") {
    return `Part number ${request.part_number ?? "—"}${request.station ? ` · station ${request.station}` : ""}`;
  }
  return `Station ${request.station} · ${request.category === "punzon" ? "Punch" : "Die"} · TN ${request.tn}${request.pilot_change ? " · PILOT CHANGE" : ""}`;
}

export function problemLabel(value: string | null): string {
  return PROGRAM_PROBLEMS.find((problem) => problem.value === value)?.label ?? value ?? "";
}

// Minutes after which an unfinished request turns amber and red.
export const WAIT_WARN_MIN = 5;
export const WAIT_LATE_MIN = 15;

export function isActive(request: Pick<ServiceRequest, "state">): boolean {
  return request.state === "open" || request.state === "attending" || request.state === "collected" || request.state === "replaced";
}

/** True while the machine position is still without a tool: that is the time worth watching. */
export function isWaitingForTool(request: Pick<ServiceRequest, "state"> & Partial<Pick<ServiceRequest, "type">>): boolean {
  return request.type !== "part_request" && (request.state === "open" || request.state === "attending" || request.state === "collected");
}

/** The old tool is physically in service and its result has not been recorded yet (a Maintenance ticket records its own). */
export function needsServiceResult(request: Pick<ServiceRequest, "state" | "type" | "service" | "tool_status" | "ticket" | "retirement">): boolean {
  return (
    (request.state === "collected" || request.state === "replaced") &&
    request.service === null &&
    request.ticket === null &&
    request.retirement === null &&
    ["sharpening", "damage", "performance", "inspection", "replacement"].includes(request.type)
  );
}

export const ABNORMAL_REBAJE = 0.02;
export const REBAJE_CHOICES = Array.from({ length: 51 }, (_, step) => (step / 1000).toFixed(3));

export function waitMinutes(request: Pick<ServiceRequest, "requested_at">): number {
  return Math.max(0, Math.floor((Date.now() - new Date(`${request.requested_at}Z`).getTime()) / 60000));
}

export function waitClass(minutes: number): string {
  return minutes >= WAIT_LATE_MIN ? "wait-late" : minutes >= WAIT_WARN_MIN ? "wait-warn" : "";
}

export function waitText(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}
