import { useEffect, useMemo, useState } from "react";
import { useAuth } from "../auth";
import { DemandForm } from "../components/DemandForm";
import { MachineCard } from "../components/MachineCard";
import { QueueDialog } from "../components/QueueDialog";
import { LEVEL_CHIP, LEVEL_ORDER } from "../labels";
import { go } from "../router";
import type { Demand } from "../types";
import { useBoard } from "../useBoard";

function Clock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);
  return <span>{now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}</span>;
}

// En TV, si las tarjetas no caben en pantalla, baja una página cada 10 s y vuelve arriba.
function useAutoScroll(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    const timer = setInterval(() => {
      const page = window.innerHeight * 0.85;
      const bottom = document.documentElement.scrollHeight - window.innerHeight;
      if (bottom <= 8) return;
      const atEnd = window.scrollY >= bottom - 8;
      window.scrollTo({ top: atEnd ? 0 : Math.min(window.scrollY + page, bottom), behavior: "smooth" });
    }, 10000);
    return () => clearInterval(timer);
  }, [enabled]);
}

function Summary({ counts, total }: { counts: Record<string, number>; total: number }) {
  return (
    <div className="summary">
      {LEVEL_ORDER.map((level) => (
        <span key={level} className={`chip sum lvl-${level}`}>
          {counts[level] ?? 0} {LEVEL_CHIP[level]}
        </span>
      ))}
      <span className="muted summary-total">Building TIE: {total.toFixed(1)} HR</span>
    </div>
  );
}

export function BoardPage({ tv = false }: { tv?: boolean }) {
  const { can } = useAuth();
  const { board, error, updatedAt, reload } = useBoard("B1");
  useAutoScroll(tv);
  const [openCode, setOpenCode] = useState<string | null>(null);
  const [creating, setCreating] = useState<string | null | false>(false);
  const [editing, setEditing] = useState<{ code: string; demand: Demand } | null>(null);

  const machines = board?.maquinas ?? [];
  const total = useMemo(() => machines.reduce((sum, m) => sum + m.tie_total, 0), [machines]);
  const openMachine = machines.find((m) => m.maquina === openCode) ?? null;
  const editMachine = editing ? machines.find((m) => m.maquina === editing.code) ?? null : null;

  const kit = (code: string, part: string) => go("/kitting", { machine: code, part });

  return (
    <section className={tv ? "board tv" : "board"}>
      <div className="board-bar">
        <h1>Sequence Board B1</h1>
        {tv ? (
          <div className="bar-right">
            <Clock />
            <a className="btn" href="#/board">
              Exit TV mode
            </a>
          </div>
        ) : (
          <div className="bar-right">
            <span className="muted">{updatedAt ? `Updated ${updatedAt.toLocaleTimeString()}` : "Loading…"}</span>
            <button className="btn" onClick={() => void reload()}>
              Refresh
            </button>
            <a className="btn" href="#/tv">
              TV mode
            </a>
            {can("demand.manage") && (
              <button className="btn btn-primary" onClick={() => setCreating(null)}>
                New demand
              </button>
            )}
          </div>
        )}
      </div>

      {error && <div className="note note-warn">⚠ {error} {board ? "Showing the last data received; retrying automatically." : ""}</div>}
      {board && <Summary counts={board.resumen} total={total} />}
      {!tv && <p className="legend">Traffic light by the hours left on the running part: red &lt; 2 h · yellow 2–4 h · green &gt; 4 h. Click a card to see its queue.</p>}
      {!board && !error && <p className="muted">Loading the board…</p>}

      <div className="grid">
        {machines.map((machine) => (
          <MachineCard
            key={machine.maquina}
            machine={machine}
            tv={tv}
            canKit={can("kitting.operate")}
            canAdd={can("demand.manage")}
            onOpen={tv ? undefined : setOpenCode}
            onKit={kit}
            onAdd={(code) => setCreating(code)}
          />
        ))}
      </div>

      {openMachine && board && (
        <QueueDialog
          machine={openMachine}
          marks={board.marcas}
          onClose={() => setOpenCode(null)}
          onChanged={() => void reload()}
          onKit={kit}
          onAdd={(code) => setCreating(code)}
          onEdit={(demand) => setEditing({ code: openMachine.maquina, demand })}
        />
      )}
      {creating !== false && board && (
        <DemandForm
          mode="create"
          machines={machines}
          presetMachine={creating ?? undefined}
          onClose={() => setCreating(false)}
          onSaved={() => {
            setCreating(false);
            void reload();
          }}
        />
      )}
      {editing && editMachine && board && (
        <DemandForm
          mode="edit"
          machine={editMachine}
          demand={editing.demand}
          marks={board.marcas}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void reload();
          }}
        />
      )}
    </section>
  );
}
