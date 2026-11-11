import { useState } from "react";
import { locationCode } from "../scan";
import { ScanInput } from "./ScanInput";

interface Option {
  codigo: string;
  tipo?: string;
}

interface Props {
  label: string;
  options: Option[];
  value: string;
  onChange: (code: string) => void;
  autoFocus?: boolean;
  showType?: boolean;
}

/** Pick a location from the list or scan its label (rack, TOGU, machine); either way ends in the same value. */
export function LocationPicker({ label, options, value, onChange, autoFocus, showType }: Props) {
  const [scan, setScan] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  function apply() {
    const code = locationCode(scan);
    if (!code) return;
    const match = options.find((option) => option.codigo.toUpperCase() === code);
    if (match) {
      onChange(match.codigo);
      setMessage(null);
    } else {
      setMessage(`${code} is not one of the options here.`);
    }
    setScan("");
  }

  return (
    <div className="location-picker">
      <label>
        {label}
        <select value={value} onChange={(event) => onChange(event.target.value)} autoFocus={autoFocus}>
          <option value="">Select…</option>
          {options.map((option) => (
            <option key={option.codigo} value={option.codigo}>
              {option.codigo}
              {showType && option.tipo ? ` (${option.tipo})` : ""}
            </option>
          ))}
        </select>
      </label>
      <ScanInput
        value={scan}
        onValue={setScan}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            apply();
          }
        }}
        placeholder="…or scan the label here"
        aria-label={`${label}: scan the label`}
      />
      {message && <span className="warn-text small">{message}</span>}
    </div>
  );
}
