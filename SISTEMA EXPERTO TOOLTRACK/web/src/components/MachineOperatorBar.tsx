import { useState } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { go } from "../router";
import type { MachineOperator } from "../types";
import { ErrorNote } from "./Dialog";

export const MACHINE_KEY = "mira.machine";

export function useOnMachine(operators: MachineOperator[] | undefined): boolean {
  const { session } = useAuth();
  return !!session && (operators ?? []).some((operator) => operator.username === session.username);
}

/** Who is logged into a machine, and the button an operator uses to log into it or leave it. */
export function MachineOperatorBar({ code, operators, onChanged, showOpen = false }: { code: string; operators: MachineOperator[]; onChanged: () => void; showOpen?: boolean }) {
  const { can } = useAuth();
  const mine = useOnMachine(operators);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canOperate = can("machine.operate");

  async function run(action: "take" | "leave") {
    setBusy(true);
    setError(null);
    try {
      await api("POST", `/workflow/manufacturing/machines/${encodeURIComponent(code)}/${action}`);
      if (action === "take") localStorage.setItem(MACHINE_KEY, code);
      onChanged();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "It could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  if (!canOperate && operators.length === 0) return null;
  return (
    <div className="q-operators">
      <span className="grow">
        {operators.length > 0 ? (
          <>
            <strong>On this machine:</strong> {operators.map((operator) => `${operator.full_name} (${operator.shift})`).join(" · ")}
          </>
        ) : (
          <span className="muted">Nobody has logged into this machine.</span>
        )}
      </span>
      {canOperate && !mine && (
        <button className="btn btn-primary" disabled={busy} onClick={() => void run("take")}>
          Take this machine
        </button>
      )}
      {canOperate && mine && showOpen && (
        <button
          className="btn btn-primary"
          onClick={() => {
            localStorage.setItem(MACHINE_KEY, code);
            go("/manufacturing", { machine: code });
          }}
        >
          Open my machine
        </button>
      )}
      {canOperate && mine && (
        <button className="btn" disabled={busy} onClick={() => void run("leave")}>
          Leave machine
        </button>
      )}
      <ErrorNote message={error} />
    </div>
  );
}

export async function requestNextPart(code: string, demandId: number) {
  return api("POST", `/workflow/manufacturing/machines/${encodeURIComponent(code)}/part-request`, { demand_id: demandId });
}
