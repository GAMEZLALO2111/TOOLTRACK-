import { useCallback, useState } from "react";
import { api, ApiError } from "../api";
import { Chip } from "../components/MachineCard";
import { KIT_STATE_CLASS, KIT_STATE_LABEL } from "../labels";
import { go } from "../router";
import type { CartRow, KitOverview } from "../types";
import { usePoll } from "../usePoll";

export function useKitOverview() {
  const [overview, setOverview] = useState<KitOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setOverview(await api<KitOverview>("GET", "/workflow/kitting/overview"));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the carts.");
    }
  }, []);
  usePoll(load, 20000);
  return { overview, error, reload: load };
}

export function CartsPanel({ carts }: { carts: CartRow[] }) {
  if (carts.length === 0) return <p className="muted">No carts are registered. An admin can add them in Locations.</p>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Cart</th>
            <th>Status</th>
            <th>Machine</th>
            <th>Progress</th>
            <th className="hide-sm">Left to pick</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {carts.map((cart) => {
            const names = cart.pending.slice(0, 5).map((p) => `${p.tn} ${p.category === "punzon" ? "punch" : "die"}`);
            return (
              <tr key={cart.codigo}>
                <td>
                  <strong>{cart.codigo}</strong>
                </td>
                <td>
                  <Chip text={KIT_STATE_LABEL[cart.state]} className={KIT_STATE_CLASS[cart.state]} />
                </td>
                <td>{cart.machine ?? "—"}</td>
                <td>{cart.kit_id ? `${cart.done}/${cart.total}` : "—"}</td>
                <td className="hide-sm muted">
                  {cart.state === "ready"
                    ? "Nothing. Scan the machine QR and the kit QR to deliver."
                    : cart.pending.length
                      ? `${cart.pending.length}: ${names.join(", ")}${cart.pending.length > 5 ? "…" : ""}`
                      : ""}
                  {cart.reason ? ` · ${cart.reason}` : ""}
                </td>
                <td className="row-actions">
                  {cart.kit_id && (
                    <button className="btn" onClick={() => go(`/kitting/kit/${cart.kit_id}`)}>
                      Open
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
