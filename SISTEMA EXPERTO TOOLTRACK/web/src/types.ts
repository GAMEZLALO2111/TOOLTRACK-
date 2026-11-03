export type Level = "ROJO" | "AMARILLO" | "VERDE" | "PARO" | "SIN_COLA";

export interface Demand {
  id: number;
  numero_parte: string;
  cantidad: number;
  piezas_producidas: number;
  pendiente: number;
  hr: number | null;
  hr_pendiente: number | null;
  sin_programa: boolean;
  estado: string;
  marca: string | null;
  comentario: string | null;
  orden: number;
  es_actual: boolean;
  es_siguiente: boolean;
}

export interface Machine {
  maquina: string;
  tipo: string;
  corriendo: string | null;
  tie_total: number;
  tie_cubierto: number;
  demandas: Demand[];
  nivel: Level;
  motivo: string;
  hr_restante: number | null;
  hr_cola: number;
  actual_id: number | null;
  siguiente_id: number | null;
  surtir_urgente: boolean;
}

export interface Board {
  edificio: string;
  maquinas: Machine[];
  marcas: string[];
  resumen: Record<Level, number>;
}

export interface UserRow {
  username: string;
  role: string;
  is_admin: boolean;
  activo: boolean;
}

export interface ProgramRow {
  numero_parte: string;
  tipo_maquina: string;
}

export type KitState = "free" | "preparing" | "paused" | "ready" | "delivered" | "cancelled";

export interface RunningMachine {
  machine: string;
  tipo: string;
  numero_parte: string | null;
  thickness: string | null;
  since: string | null;
  kit_state: KitState | null;
  kit_cart: string | null;
}

export interface CartRow {
  codigo: string;
  state: KitState;
  kit_id: string | null;
  machine: string | null;
  done: number;
  total: number;
  pending: { tn: string; category: string }[];
  reason: string | null;
}

export interface KitOverview {
  machines: RunningMachine[];
  carts: CartRow[];
}

export interface PickItem {
  kind: "supply" | "verify";
  station: string;
  tn: string;
  angle: string;
  category: "punch" | "die";
  clearance: string | null;
  action: "supply" | "verify_clearance" | "confirm_with_engineering";
}

export interface KitPlan {
  machine: string;
  tipo: string;
  mounted_part: string | null;
  current_part: string | null;
  new_part: string;
  needs_current: boolean;
  comparison: {
    thickness_current: string;
    thickness_new: string;
    thickness_changed: boolean;
    clearance_min: string | null;
    clearance_max: string | null;
    thickness_out_of_range: boolean;
    supply: number;
    verify: number;
  } | null;
  pick_list: PickItem[];
  open_kit: Kit | null;
  carts: CartRow[];
  suggested_cart: string | null;
}

export interface KitComponent {
  component_id: number;
  requirement_id: number;
  station: string | null;
  tn: string;
  angle: string | null;
  category: string;
  action: "new" | "keep" | "replace";
  previous_tool_id: string | null;
  tool_id: string | null;
  state: "missing" | "ready" | "cancelled";
  source_location: string | null;
}

export interface Kit {
  id: string;
  machine: string;
  cart: string;
  state: KitState;
  reason: string | null;
  qr_token: string | null;
  created_at: string;
  delivered_at: string | null;
  components: KitComponent[];
  warnings?: string[];
}

export interface Candidate {
  id: string;
  tn: string;
  type: string | null;
  size: string | null;
  clearance: string | null;
  life: string | null;
  location: string;
}

export interface KitQr {
  kit_id: string;
  machine: string;
  qr_token: string;
  scan_value: string;
}

export interface DeliveryResult {
  kit_id: string;
  machine: string;
  active_setup_id: number;
  active_drawing: string | null;
  pending_pickups_created: number;
  delivered_tools: number;
}

export interface Pending {
  pending_id: number;
  tool_id: string;
  tn: string;
  category: string;
  clearance: string | null;
  machine: string;
  drawing_name: string | null;
  created_at: string;
  age_hours: number;
  standard_machine: boolean;
  overdue: boolean;
  home_rack: string | null;
}

export interface LocationRow {
  codigo: string;
  nombre: string;
  tipo: string;
  activa: boolean;
  estandar: boolean;
}
