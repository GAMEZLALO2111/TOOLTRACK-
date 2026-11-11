import { PROGRAM_PROBLEMS, SERVICE_STATE_CLASS, SERVICE_STATE_LABEL } from "../labels";
import type { ServiceRequest } from "../types";
import { Chip } from "./MachineCard";

export function when(iso: string | null | undefined): string {
  return iso ? new Date(`${iso}Z`).toLocaleString() : "—";
}

export function RequestChip({ request }: { request: Pick<ServiceRequest, "state"> & Partial<Pick<ServiceRequest, "type">> }) {
  const program = request.type === "program_problem";
  const text = program && request.state === "open" ? "Waiting for Tool Crib or Engineering" : program && request.state === "attending" ? "Being reviewed" : (SERVICE_STATE_LABEL[request.state] ?? request.state);
  return <Chip text={text} className={SERVICE_STATE_CLASS[request.state] ?? ""} />;
}

/** Manufacturing can receive the tool once Tool Crib recovered the old one (or, for a tool request, once Tool Crib is on it). */
export function canReceive(request: Pick<ServiceRequest, "state" | "type">): boolean {
  return request.state === "collected" || (request.type === "tool_request" && request.state === "attending");
}

export function positionLabel(request: Pick<ServiceRequest, "type" | "station" | "category" | "tn" | "part_number">): string {
  if (request.type === "program_problem") {
    return `Part number ${request.part_number ?? "—"}${request.station ? ` · station ${request.station}` : ""}`;
  }
  return `Station ${request.station} · ${request.category === "punzon" ? "Punch" : "Die"} · TN ${request.tn}`;
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
export function isWaitingForTool(request: Pick<ServiceRequest, "state">): boolean {
  return request.state === "open" || request.state === "attending" || request.state === "collected";
}

/** The old tool is physically in service and its result has not been recorded yet. */
export function needsServiceResult(request: Pick<ServiceRequest, "state" | "type" | "service" | "tool_status">): boolean {
  return (
    (request.state === "collected" || request.state === "replaced") &&
    request.service === null &&
    ["sharpening", "damage", "performance", "inspection", "replacement"].includes(request.type)
  );
}

export const ABNORMAL_REBAJE = 0.02;

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
