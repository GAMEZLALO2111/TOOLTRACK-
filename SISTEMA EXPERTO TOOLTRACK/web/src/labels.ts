import type { Level } from "./types";

// El backend todavía guarda estados y marcas en español; aquí se traducen para mostrarlos.
// Cuando el backend migre a inglés (fase 5) solo hay que ajustar estos mapas.
export const LEVEL_LABEL: Record<Level, string> = {
  ROJO: "STOPS SOON",
  AMARILLO: "ATTENTION",
  VERDE: "OK",
  PARO: "SCHEDULED STOP",
  SIN_COLA: "NO QUEUE",
};

export const LEVEL_CHIP: Record<Level, string> = {
  ROJO: "stops soon",
  AMARILLO: "attention",
  VERDE: "running fine",
  PARO: "scheduled stop",
  SIN_COLA: "no queue",
};

export const LEVEL_ORDER: Level[] = ["ROJO", "AMARILLO", "VERDE", "PARO", "SIN_COLA"];

export const STATUS_LABEL: Record<string, string> = {
  "NO INICIADO": "NOT STARTED",
  SURTIENDO: "KITTING",
  "FALTA HERRAMIENTAS": "MISSING TOOLS",
  "EN MÁQUINA": "ON MACHINE",
  CORRIENDO: "RUNNING",
  TERMINADO: "DONE",
};

export const STATUS_CLASS: Record<string, string> = {
  "NO INICIADO": "st-notstarted",
  SURTIENDO: "st-kitting",
  "FALTA HERRAMIENTAS": "st-missing",
  "EN MÁQUINA": "st-onmachine",
  CORRIENDO: "st-running",
  TERMINADO: "st-done",
};

export const MARK_LABEL: Record<string, string> = {
  "PARO PROGRAMADO": "SCHEDULED STOP",
  URGENTE: "URGENT",
  "LÁSER": "LASER",
  "CORTE DE BLANCK": "BLANK CUT",
};

export const MARK_CLASS: Record<string, string> = {
  "PARO PROGRAMADO": "mk-stop",
  URGENTE: "mk-urgent",
  "LÁSER": "mk-laser",
  "CORTE DE BLANCK": "mk-blank",
};

export const KIT_STATE_LABEL: Record<string, string> = {
  free: "Free",
  preparing: "In progress",
  paused: "Paused",
  ready: "Ready to deliver",
  delivered: "Delivered",
  cancelled: "Cancelled",
};

export const KIT_STATE_CLASS: Record<string, string> = {
  free: "ks-free",
  preparing: "ks-preparing",
  paused: "ks-paused",
  ready: "ks-ready",
  delivered: "ks-delivered",
  cancelled: "ks-cancelled",
};

export const COMPONENT_STATE_LABEL: Record<string, string> = {
  missing: "To pick",
  ready: "On cart",
  cancelled: "Cancelled",
};

export const ACTION_LABEL: Record<string, string> = { new: "New", replace: "Replace", keep: "Keep" };

export const CATEGORY_LABEL: Record<string, string> = { punch: "Punch", die: "Die", punzon: "Punch", matriz: "Die" };

export const PICK_ACTION_LABEL: Record<string, string> = {
  supply: "Put on the cart",
  verify_clearance: "Check the die; supply a replacement if it does not meet the clearance",
  confirm_with_engineering: "Confirm the die with Engineering",
};

export const ROLE_INFO: Record<string, string> = {
  SYSTEM_ADMIN: "Users, machines, locations, catalogs, audit",
  ENGINEERING: "Programs and part numbers, read-only elsewhere",
  PLANNING: "Creates and edits the kitting demand",
  TOOLCRIB_SUPERVISOR: "Toolcrib operations, inventory adjustments, audit",
  TOOLCRIB_OPERATOR: "Kitting, deliveries, returns, run progress",
  MAINTENANCE_TECHNICIAN: "Receives, sharpens and releases tools",
};

export function fmtDuration(hours: number | null): string {
  if (hours === null) return "—";
  const minutes = Math.round(hours * 60);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")} min`;
}

export function fmtEta(hours: number | null): string | null {
  if (hours === null) return null;
  const when = new Date(Date.now() + hours * 3600_000);
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  const days = Math.floor((when.setHours(0, 0, 0, 0) - new Date().setHours(0, 0, 0, 0)) / 86_400_000);
  return days > 0 ? `${time} (+${days}d)` : time;
}
