import { useCallback, useState } from "react";
import { api } from "../api";
import type { ServiceRequest } from "../types";
import { usePoll } from "../usePoll";
import { WAIT_LATE_MIN, waitMinutes } from "./Service";

/** Navigation tab with the number of requests still waiting for someone to take them. */
export function RequestsTab({ active, programOnly }: { active: boolean; programOnly: boolean }) {
  const [rows, setRows] = useState<ServiceRequest[]>([]);

  const load = useCallback(async () => {
    try {
      setRows(await api<ServiceRequest[]>("GET", "/workflow/service-requests?state=active"));
    } catch {
      // The tab keeps its last count; the page itself reports errors.
    }
  }, []);
  usePoll(load, 15000);

  const waiting = rows.filter((row) => row.state === "open" && (!programOnly || row.type === "program_problem"));
  const late = waiting.some((row) => waitMinutes(row) >= WAIT_LATE_MIN);

  return (
    <a href="#/requests" className={active ? "active" : ""}>
      Requests
      {waiting.length > 0 && (
        <span className={`tab-badge${late ? " late" : ""}`} aria-label={`${waiting.length} waiting`}>
          {waiting.length}
        </span>
      )}
    </a>
  );
}
