import { MachineLink, ToolLink } from "./Links";

export interface RecordColumn {
  key: string;
  label: string;
  export_only: boolean;
}

export type RecordRow = Record<string, string | number | null>;

const TOOL_KEYS = ["tool", "replacement"];
const MACHINE_KEYS = ["machine"];

/** Rows of a record list. Columns marked export-only appear in the Excel file but not on screen. */
export function RecordTable({ columns, rows }: { columns: RecordColumn[]; rows: RecordRow[] }) {
  const shown = columns.filter((column) => !column.export_only);
  return (
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
          {rows.map((row, index) => (
            <tr key={index}>
              {shown.map((column) => {
                const value = row[column.key];
                const text = value === null || value === undefined || value === "" ? "—" : String(value);
                return (
                  <td key={column.key} title={text.length > 40 ? text : undefined} className={text.length > 40 ? "cell-clip" : undefined}>
                    {TOOL_KEYS.includes(column.key) && text !== "—" ? <ToolLink id={text} /> : MACHINE_KEYS.includes(column.key) && text !== "—" ? <MachineLink code={text} /> : text}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
