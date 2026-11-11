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

export const CATEGORY_LABEL: Record<string, string> = { punch: "Punch", die: "Die", punzon: "Punch", matriz: "Die", holder: "Holder", accesorio: "Accessory" };

export const TOOL_STATE_LABEL: Record<string, string> = {
  disponible: "Available",
  afilado: "Being sharpened",
  danado: "Damaged",
  baja: "Retired",
};

export const PICK_ACTION_LABEL: Record<string, string> = {
  supply: "Put on the cart",
  verify_clearance: "Check the die; supply a replacement if it does not meet the clearance",
  confirm_with_engineering: "Confirm the die with Engineering",
};

export const SERVICE_TYPES: { value: string; label: string; help: string; noteRequired?: boolean }[] = [
  { value: "sharpening", label: "Sharpening", help: "The tool is dull and needs to be sharpened." },
  { value: "inspection", label: "Inspection", help: "Tool Crib checks the tool before it goes back." },
  { value: "replacement", label: "Replacement", help: "Swap it for another tool of the same TN." },
  { value: "damage", label: "Damage report", help: "The tool is broken or damaged.", noteRequired: true },
  { value: "performance", label: "Performance issue", help: "Parts or the tool are not performing as expected.", noteRequired: true },
  { value: "return", label: "Return", help: "Take the tool back to Tool Crib; the position stays empty." },
];

export const PROGRAM_PROBLEMS: { value: string; label: string; help: string }[] = [
  { value: "wrong_tool", label: "Wrong tool in the program", help: "The program asks for a tool that is not the right one." },
  { value: "wrong_station_or_angle", label: "Wrong station or angle", help: "A station or angle in the program does not match the machine." },
  { value: "part_out_of_spec", label: "Part out of specification", help: "The parts come out wrong and the program is the suspect." },
  { value: "program_does_not_run", label: "The program does not run", help: "The machine stops or rejects the program." },
  { value: "other", label: "Other", help: "Describe it below." },
];

export const SERVICE_TYPE_LABEL: Record<string, string> = {
  ...Object.fromEntries(SERVICE_TYPES.map((type) => [type.value, type.label])),
  tool_request: "Tool request",
  program_problem: "Program problem",
};

export const SERVICE_STATE_LABEL: Record<string, string> = {
  open: "Waiting for Tool Crib",
  attending: "Tool Crib on the way",
  collected: "Waiting for a tool",
  replaced: "Replaced · old tool in service",
  closed: "Closed",
  cancelled: "Cancelled",
};

export const SERVICE_STATE_CLASS: Record<string, string> = {
  open: "st-kitting",
  attending: "st-running",
  collected: "st-missing",
  replaced: "st-onmachine",
  closed: "st-done",
  cancelled: "st-notstarted",
};

export const MOVEMENT_LABEL: Record<string, string> = {
  service_requested: "Service requested from the machine",
  service_cancelled: "Service request cancelled: back on the machine",
  service_install: "Installed on the machine",
  service_completed: "Service finished, ready for use",
  service_retired: "Retired after service",
  machine_observed: "Registered as mounted on the machine",
  service_collect_sharpening: "Collected from the machine for sharpening",
  service_collect_inspection: "Collected from the machine for inspection",
  service_collect_replacement: "Collected from the machine to be replaced",
  service_collect_damage: "Collected from the machine, reported damaged",
  service_collect_performance: "Collected from the machine, performance issue",
  service_collect_return: "Returned from the machine",
  rack_to_cart_for_kit: "Taken from the rack to the cart",
  kit_selection_reversed: "Cart selection undone",
  kit_cancelled_return_to_rack: "Kit cancelled: back to its rack",
  cart_to_machine_delivery: "Delivered to the machine",
  machine_return: "Returned from the machine to its rack",
  machine_return_sharpening: "Returned for sharpening",
  machine_return_damage: "Returned damaged",
  removed_from_setup_pending_pickup: "Removed from the setup (waiting to be picked up)",
  pending_pickup_to_rack: "Picked up from the machine to its rack",
  status_danado: "Marked as damaged",
  status_baja: "Retired",
  status_disponible: "Released (available)",
};

export function movementLabel(type: string): string {
  if (MOVEMENT_LABEL[type]) return MOVEMENT_LABEL[type];
  if (type.endsWith("_to_rack")) return "Stored in its rack";
  if (type.startsWith("pending_pickup_to_")) return "Picked up from the machine";
  return type.replace(/_/g, " ");
}

export const TOOL_STATE_CLASS: Record<string, string> = {
  disponible: "ts-ok",
  afilado: "ts-sharp",
  danado: "ts-damaged",
  baja: "ts-retired",
};

export const RETURN_REASONS: { value: string; label: string; types: string[] }[] = [
  { value: "regreso", label: "Return to its rack", types: ["rack"] },
  { value: "afilado", label: "Needs sharpening", types: ["togu", "workshop"] },
  { value: "dano", label: "Damaged", types: ["maintenance", "workshop"] },
];

export const ROLE_INFO: Record<string, string> = {
  SYSTEM_ADMIN: "Users, machines, locations, catalogs, audit",
  ENGINEERING: "Programs and part numbers, read-only elsewhere",
  PLANNING: "Creates and edits the kitting demand",
  TOOLCRIB_SUPERVISOR: "Toolcrib operations, inventory adjustments, audit",
  TOOLCRIB_OPERATOR: "Kitting, deliveries, returns, run progress",
  MAINTENANCE_TECHNICIAN: "Receives, sharpens and releases tools",
  PUNCHING_OPERATOR: "Read-only Manufacturing workspace for Punzonado",
  PUNCHING_TECHNICIAN: "Read-only Manufacturing workspace for Punzonado",
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
