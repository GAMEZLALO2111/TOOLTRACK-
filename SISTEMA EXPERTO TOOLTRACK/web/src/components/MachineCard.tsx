import { fmtDuration, fmtEta, LEVEL_LABEL, MARK_CLASS, MARK_LABEL, STATUS_CLASS, STATUS_LABEL } from "../labels";
import type { Demand, Machine } from "../types";

export function Chip({ text, className }: { text: string; className: string }) {
  return <span className={`chip ${className}`}>{text}</span>;
}

export function StatusChip({ status }: { status: string }) {
  return <Chip text={STATUS_LABEL[status] ?? status} className={STATUS_CLASS[status] ?? ""} />;
}

export function MarkChip({ mark }: { mark: string | null }) {
  if (!mark) return null;
  return <Chip text={MARK_LABEL[mark] ?? mark} className={MARK_CLASS[mark] ?? ""} />;
}

export function Progress({ demand }: { demand: Demand }) {
  const pct = demand.cantidad ? Math.min(100, (demand.piezas_producidas / demand.cantidad) * 100) : 0;
  return (
    <div className="progress" aria-label={`${Math.round(pct)}% produced`}>
      <div style={{ width: `${pct}%` }} />
    </div>
  );
}

interface CardProps {
  machine: Machine;
  tv?: boolean;
  canKit: boolean;
  canAdd: boolean;
  onOpen?: (code: string) => void;
  onKit?: (code: string, part: string) => void;
  onAdd?: (code: string) => void;
}

export function MachineCard({ machine, tv, canKit, canAdd, onOpen, onKit, onAdd }: CardProps) {
  const actual = machine.demandas.find((d) => d.es_actual);
  const next = machine.demandas.find((d) => d.es_siguiente);
  const pending = machine.demandas.filter((d) => d.pendiente > 0);
  const more = pending.length - (actual ? 1 : 0) - (next ? 1 : 0);
  const big = machine.hr_restante !== null ? fmtDuration(machine.hr_restante) : LEVEL_LABEL[machine.nivel];
  const eta = machine.nivel === "PARO" || !actual ? null : fmtEta(actual.hr_pendiente);
  const needsKit = next && next.estado !== "EN MÁQUINA";
  const clickable = !!onOpen;

  return (
    <article
      className={`card lvl-${machine.nivel}${machine.surtir_urgente ? " urgent" : ""}${clickable ? " clickable" : ""}`}
      onClick={clickable ? () => onOpen(machine.maquina) : undefined}
      onKeyDown={clickable ? (event) => event.target === event.currentTarget && (event.key === "Enter" || event.key === " ") && onOpen(machine.maquina) : undefined}
      tabIndex={clickable ? 0 : undefined}
      aria-label={clickable ? `${machine.maquina}: open queue` : undefined}
    >
      <header className="card-head">
        <div className="card-title">
          <strong>{machine.maquina}</strong>
          <span>{machine.tipo}</span>
        </div>
        <div className="card-big">{big}</div>
        <div className="card-reason">{machine.motivo}</div>
      </header>

      <div className="card-body">
        {actual ? (
          <>
            <div className="np-line">
              <small>RUNNING</small>
              <strong>{actual.numero_parte}</strong>
              <MarkChip mark={actual.marca} />
            </div>
            <Progress demand={actual} />
            <div className="muted">
              {actual.piezas_producidas}/{actual.cantidad} pcs{eta ? ` · stops ≈ ${eta}` : ""}
            </div>
          </>
        ) : (
          <div className="muted">{machine.corriendo ? `On machine: ${machine.corriendo}` : "No part mounted"}</div>
        )}

        <hr />

        {next ? (
          <div>
            <div className="np-line">
              <small>NEXT</small>
              <strong>{next.numero_parte}</strong>
            </div>
            <div className="chips">
              <StatusChip status={next.estado} />
              <MarkChip mark={next.marca} />
            </div>
          </div>
        ) : (
          <div className="muted">Nothing next in the queue</div>
        )}
        {pending.length > 0 && (
          <div className="muted small">
            Queue: {fmtDuration(machine.hr_cola)}
            {more > 0 ? ` · +${more} more` : ""}
          </div>
        )}
      </div>

      {!tv && (
        <footer className="card-foot" onClick={(event) => event.stopPropagation()}>
          {needsKit && canKit && onKit ? (
            <button className={`btn ${machine.surtir_urgente ? "btn-danger" : "btn-primary"} block`} onClick={() => onKit(machine.maquina, next.numero_parte)}>
              {machine.surtir_urgente ? "⚠ KIT NEXT NOW" : "Kit next"}
            </button>
          ) : needsKit ? (
            <div className={`foot-note${machine.surtir_urgente ? " warn" : ""}`}>Next part still to be kitted</div>
          ) : next ? (
            <div className="foot-note ok">✔ Next part is on the machine</div>
          ) : machine.nivel === "SIN_COLA" && canAdd && onAdd ? (
            <button className="btn btn-primary block" onClick={() => onAdd(machine.maquina)}>
              Add demand
            </button>
          ) : null}
        </footer>
      )}
    </article>
  );
}
