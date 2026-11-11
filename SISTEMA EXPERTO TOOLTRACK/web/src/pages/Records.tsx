import { useCallback, useEffect, useState } from "react";
import { api, ApiError, download } from "../api";
import { ErrorNote } from "../components/Dialog";
import { go } from "../router";

const PAGE_SIZE = 100;

interface KindInfo {
  key: string;
  label: string;
  filters: string[];
  groups: { value: string; label: string }[];
}

interface RecordPage {
  key: string;
  group: string | null;
  columns: { key: string; label: string; export_only: boolean }[];
  rows: Record<string, string | number | null>[];
  total: number;
  limit: number;
  offset: number;
}

interface Filter {
  date_from: string;
  date_to: string;
  tool: string;
  machine: string;
  user: string;
}

const EMPTY: Filter = { date_from: "", date_to: "", tool: "", machine: "", user: "" };

function query(filter: Filter, group: string, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams(extra);
  for (const [key, value] of Object.entries(filter)) if (value.trim()) params.set(key, value.trim());
  if (group) params.set("group", group);
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function RecordsPage({ kind }: { kind: string }) {
  const [kinds, setKinds] = useState<KindInfo[] | null>(null);
  const [group, setGroup] = useState("");
  const [filter, setFilter] = useState<Filter>(EMPTY);
  const [applied, setApplied] = useState<Filter>(EMPTY);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<RecordPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<KindInfo[]>("GET", "/records")
      .then(setKinds)
      .catch((caught) => setError(caught instanceof ApiError ? caught.message : "Could not load the records."));
  }, []);

  const current = kinds?.find((item) => item.key === kind) ?? kinds?.[0];
  const currentKey = current?.key;
  const firstGroup = current?.groups[0]?.value ?? "";

  useEffect(() => {
    setFilter(EMPTY);
    setApplied(EMPTY);
    setOffset(0);
    setGroup(firstGroup);
    setData(null);
  }, [currentKey, firstGroup]);

  const load = useCallback(async () => {
    if (!currentKey) return;
    try {
      setData(await api<RecordPage>("GET", `/records/${currentKey}${query(applied, group, { limit: String(PAGE_SIZE), offset: String(offset) })}`));
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the records.");
    }
  }, [currentKey, applied, group, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(path: string, name: string) {
    setBusy(true);
    setError(null);
    try {
      await download(path, name);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The file could not be downloaded.");
    } finally {
      setBusy(false);
    }
  }

  function apply() {
    setOffset(0);
    setApplied(filter);
  }

  function clear() {
    setFilter(EMPTY);
    setApplied(EMPTY);
    setOffset(0);
  }

  const shown = data?.columns.filter((column) => !column.export_only) ?? [];
  const from = data && data.total > 0 ? data.offset + 1 : 0;
  const to = data ? Math.min(data.offset + data.limit, data.total) : 0;
  const has = (name: string) => !!current?.filters.includes(name);
  const set = (key: keyof Filter) => (event: { target: { value: string } }) => setFilter({ ...filter, [key]: event.target.value });

  return (
    <section className="page">
      <div className="board-bar">
        <h1>Records</h1>
        <div className="bar-right">
          <button className="btn" disabled={busy} onClick={() => void save(`/records/workbook${query({ ...EMPTY, date_from: applied.date_from, date_to: applied.date_to }, "")}`, "MIRA-records.xlsx")}>
            Download all (.xlsx)
          </button>
        </div>
      </div>
      <div className="tabs-inline wrap">
        {kinds?.map((item) => (
          <button key={item.key} className={`btn${item.key === current?.key ? " btn-primary" : ""}`} onClick={() => go(`/records/${item.key}`)}>
            {item.label}
          </button>
        ))}
      </div>
      {current && current.groups.length > 0 && (
        <div className="tabs-inline wrap">
          {current.groups.map((item) => (
            <button
              key={item.value}
              className={`btn${item.value === group ? " btn-primary" : ""}`}
              onClick={() => {
                setGroup(item.value);
                setOffset(0);
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
      {current && (
        <form
          className="filters"
          onSubmit={(event) => {
            event.preventDefault();
            apply();
          }}
        >
          {has("date") && (
            <>
              <label className="inline-field">
                From
                <input type="date" value={filter.date_from} onChange={set("date_from")} />
              </label>
              <label className="inline-field">
                To
                <input type="date" value={filter.date_to} onChange={set("date_to")} />
              </label>
            </>
          )}
          {has("tool") && <input placeholder="Tool" value={filter.tool} onChange={set("tool")} />}
          {has("machine") && <input placeholder="Machine" value={filter.machine} onChange={set("machine")} />}
          {has("user") && <input placeholder="User" value={filter.user} onChange={set("user")} />}
          <button className="btn btn-primary" type="submit">
            Search
          </button>
          <button className="btn" type="button" onClick={clear}>
            Clear
          </button>
          <button
            className="btn push-right"
            type="button"
            disabled={busy}
            onClick={() => void save(`/records/${current.key}/download${query(applied, group)}`, `MIRA-${current.key}.xlsx`)}
          >
            Download .xlsx
          </button>
        </form>
      )}
      <ErrorNote message={error} />
      {data && data.rows.length === 0 && !error && <p className="note note-info">Nothing found.</p>}
      {data && data.rows.length > 0 && (
        <>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  {shown.map((column) => (
                    <th key={column.key}>{column.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row, index) => (
                  <tr key={index}>
                    {shown.map((column) => {
                      const value = row[column.key];
                      const text = value === null || value === undefined || value === "" ? "—" : String(value);
                      return (
                        <td key={column.key} title={text.length > 40 ? text : undefined} className={text.length > 40 ? "cell-clip" : undefined}>
                          {text}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <span className="muted small">
              {from}–{to} of {data.total}
            </span>
            <span>
              <button className="btn" disabled={data.offset === 0} onClick={() => setOffset(Math.max(0, data.offset - PAGE_SIZE))}>
                ← Previous
              </button>
              <button className="btn" disabled={to >= data.total} onClick={() => setOffset(data.offset + PAGE_SIZE)}>
                Next →
              </button>
            </span>
          </div>
        </>
      )}
      {!data && !error && <p className="muted">Loading…</p>}
    </section>
  );
}
