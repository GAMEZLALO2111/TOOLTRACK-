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

export interface WaitingTool {
  folio: string;
  station: string | null;
  category: "punzon" | "matriz" | null;
  type: string;
  state: string;
  since: string;
}

export interface Machine {
  maquina: string;
  tipo: string;
  corriendo: string | null;
  tie_total: number;
  tie_cubierto: number;
  demandas: Demand[];
  herramientas_esperando: WaitingTool[];
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
  employee_number: string | null;
  full_name: string | null;
  area: string | null;
  shift: string | null;
}

export interface ManufacturingMachineOption {
  codigo: string;
  nombre: string;
  activa: boolean;
  tipo_maquina: string | null;
  edificio: string | null;
  linea: string | null;
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

export interface MachineStation {
  numero: string;
  tamano: string | null;
  posicion: string | null;
  autoindex: boolean;
  estado: "disponible" | "danada";
  nota: string | null;
}

export interface MachineProfile {
  codigo: string;
  nombre: string;
  activa: boolean;
  estandar: boolean;
  tipo_maquina: string | null;
  linea: string | null;
  capacidad: string | null;
  espesor_max: string | null;
  espesor_min: string | null;
  edificio: string | null;
  blank_max: string | null;
  area_pallet: string | null;
  notas: string | null;
  numero_parte: string | null;
  estaciones_total: number;
  estaciones_disponibles: number;
  estaciones_danadas: number;
  estaciones: MachineStation[];
}

export interface ServiceRequest {
  id: number;
  folio: string;
  machine: string;
  part_number: string | null;
  requirement_id: number | null;
  station: string | null;
  category: "punzon" | "matriz" | null;
  tn: string | null;
  type: ServiceRequestType;
  state: ServiceRequestState;
  problem: string | null;
  resolution: string | null;
  note: string | null;
  tool_id: string | null;
  tool_status: string | null;
  tool_location: string | null;
  service: ToolServiceResult | null;
  replacement_tool_id: string | null;
  destination: string | null;
  requested_by: string;
  requested_by_name: string;
  requested_by_shift: string | null;
  requested_at: string;
  attended_by: string | null;
  attended_by_name: string | null;
  attended_at: string | null;
  collected_by: string | null;
  collected_at: string | null;
  closed_by: string | null;
  closed_at: string | null;
  cancel_reason: string | null;
  same_tool?: boolean;
  restored?: boolean;
}

export type ServiceRequestType = "sharpening" | "inspection" | "replacement" | "damage" | "performance" | "return" | "tool_request" | "program_problem";
export type ServiceRequestState = "open" | "attending" | "collected" | "replaced" | "closed" | "cancelled";

export interface ToolServiceResult {
  result: "ready" | "retired";
  rebaje: number | null;
  abnormal: boolean;
  justification: string | null;
  note: string | null;
  done_by: string;
  done_at: string;
}

export interface ManufacturingWorkspace {
  machine: string;
  machine_name: string;
  active: boolean;
  building: string | null;
  line: string | null;
  machine_type: string | null;
  part_number: string | null;
  setup: {
    id: number;
    drawing_name: string | null;
    material: string | null;
    thickness: string;
    created_at: string;
  } | null;
  program: {
    revision: number;
    client: string | null;
    family: string | null;
    program_type: string;
    drawing_name: string | null;
    material: string | null;
    thickness: string | null;
    file_name: string | null;
    updated_by: string;
    updated_at: string;
    requirements: { station: string; tn: string; angle: string | null }[];
    planning: {
      pieces_per_blank: number | null;
      cycle_time_s: number | null;
      cycle_with_lead_s: number | null;
      theoretical_uph: number | null;
    };
  } | null;
  coverage: { installed: number; required: number; complete: boolean };
  stations: {
    requirement_id: number | null;
    station: string;
    tn: string;
    angle: string | null;
    machine_station: {
      size: string | null;
      position: string | null;
      autoindex: boolean;
      state: string;
      note: string | null;
    } | null;
    components: {
      category: "punzon" | "matriz";
      tool_id: string | null;
      tn: string;
      tool_type: string | null;
      tool_size: string | null;
      location: string | null;
      tool_status: string | null;
      clearance: string | null;
      clearance_valid: boolean | null;
      state: "installed" | "missing" | "elsewhere" | "incompatible";
      open_request: ServiceRequest | null;
    }[];
  }[];
  pending_pickups: { tool_id: string; tn: string | null; category: string | null; created_at: string }[];
  requirements_source: "setup" | "program" | "none";
  active_kit: {
    id: string;
    state: string;
    created_at: string;
    updated_at: string;
  } | null;
  service_requests: ServiceRequest[];
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

export interface ToolRow {
  tool_id: string;
  tn: string;
  category: string | null;
  type: string | null;
  size: string | null;
  clearance: string | null;
  location: string | null;
  location_type: string | null;
  status: string;
  pending_pickup: boolean;
  drawing_name: string | null;
  station: string | null;
  angle: string | null;
  last_movement_at: string | null;
  home_rack: string | null;
  estado: string;
  estado_label: string;
}

export interface Movement {
  id: number;
  tool_id: string;
  from: string | null;
  to: string | null;
  type: string;
  by: string;
  created_at: string;
  details: string | null;
}

export interface MountedTool {
  tool_id: string;
  tn: string;
  category: string;
  station: string | null;
  angle: string | null;
  clearance: string | null;
  on_machine: boolean;
}

export interface MachineTools {
  machine: string;
  drawing_name: string | null;
  tools: MountedTool[];
}

export interface InvRow {
  id: string;
  tn: string | null;
  size: string | null;
  type: string | null;
  life: string | null;
  clearance: string | null;
  so: string | null;
  categoria: string;
  ubicacion: string | null;
  catalog_linked: boolean;
  estado: string;
}

export interface InvPage {
  total: number;
  limit: number;
  offset: number;
  summary: { total: number; linked: number; unlinked: number };
  items: InvRow[];
}

export interface InvOptions {
  categorias: string[];
  tipos: Record<string, string[]>;
  sizes: string[];
  estados: string[];
}

export interface CatalogConfig {
  tn: string;
  size: string;
  type: string;
  variantes: { id: number; categoria: string; clearance: string | null }[];
}

export interface Planning {
  piezas_blank: number | null;
  cycle_time_s: number | null;
  cycle_con_lead_s: number | null;
  uph_teorico: number | null;
}

export interface Program {
  numero_parte: string;
  tipo_maquina: string;
  cliente: string | null;
  familia: string | null;
  tipo_programa: string;
  revision: number;
  nombre_archivo: string | null;
  drawing_name: string | null;
  material: string | null;
  thickness: string | null;
  actualizado_por: string;
  actualizado_en: string;
  requisitos: { station: string; tn: string; angle: string | null }[];
  planeacion: Planning;
  contenido?: string;
}

export interface ProgramLogRow {
  fecha: string;
  numero_parte: string;
  tipo_maquina: string;
  accion: string;
  revision: number;
  motivo: string | null;
  usuario: string;
  detalle: string | null;
}

export interface ProgramPreview {
  exists: boolean;
  revision: number | null;
  next_revision: number;
  drawing_name: string | null;
  material: string | null;
  thickness: string;
  requisitos: { station: string; tn: string; angle: string | null }[];
  planeacion: Planning;
  diff: string | null;
}

export interface Family {
  nombre: string;
  cliente: string;
}

export interface MovableTool {
  tool_id: string;
  tn: string;
  category: string | null;
  clearance: string | null;
  location: string;
  location_type: string;
  home_rack: string | null;
}
