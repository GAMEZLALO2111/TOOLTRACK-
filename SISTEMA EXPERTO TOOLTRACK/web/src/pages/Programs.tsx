import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { ErrorNote } from "../components/Dialog";
import { go } from "../router";
import type { ProgramLogRow } from "../types";
import { LogTable, ProgramsTab } from "./ProgramsTab";

const TABS = [
  { key: "4i", type: "4I", label: "4 Index (4I)" },
  { key: "2i", type: "2I", label: "2 Index (2I)" },
  { key: "45sta", type: "45STA", label: "45 stations" },
  { key: "log", type: "", label: "Change log" },
];

export function ProgramsPage({ tab }: { tab: string }) {
  const { can } = useAuth();
  if (!can("program.view")) return <p className="note note-info">Your role cannot see the part numbers.</p>;
  const current = TABS.find((t) => t.key === tab) ?? TABS[0];
  return (
    <section className="page">
      <div className="board-bar">
        <h1>Part numbers</h1>
      </div>
      <div className="tabs-inline wrap">
        {TABS.map((t) => (
          <button key={t.key} className={`btn${t.key === current.key ? " btn-primary" : ""}`} onClick={() => go(`/programs/${t.key}`)}>
            {t.label}
          </button>
        ))}
      </div>
      {current.type ? <ProgramsTab key={current.type} type={current.type} /> : <LogTab />}
    </section>
  );
}

function LogTab() {
  const [search, setSearch] = useState("");
  const [rows, setRows] = useState<ProgramLogRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      api<ProgramLogRow[]>("GET", `/workflow/programs/log?numero_parte=${encodeURIComponent(search.trim())}`)
        .then((data) => {
          setRows(data);
          setError(null);
        })
        .catch((caught) => setError(caught instanceof ApiError ? caught.message : "Could not load the change log."));
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  return (
    <div>
      <p className="muted">Every load or revision of a program: who, when, why and what changed. Shows the latest 500.</p>
      <div className="filters">
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Filter by part number" aria-label="Filter the log" autoFocus />
      </div>
      <ErrorNote message={error} />
      {rows && <LogTable rows={rows} showPart />}
    </div>
  );
}
