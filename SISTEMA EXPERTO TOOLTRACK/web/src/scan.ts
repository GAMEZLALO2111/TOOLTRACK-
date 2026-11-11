// Mirror of scan_codes.py: what a scanned or typed string means. The server validates again.
const PREFIXES: Record<string, string> = {
  "TOOLTRACK-TOOL:": "tool",
  "TOOLTRACK-LOCATION:": "location",
  "TOOLTRACK-MACHINE:": "machine",
  "TOOLTRACK-CART:": "cart",
  "TOOLTRACK-KIT:": "kit",
};

// Scanners append CR, LF, TAB or GS; none of them belongs to the code.
// eslint-disable-next-line no-control-regex
const CONTROL = /[\x00-\x1f\x7f]/g;

export interface ParsedScan {
  kind: string | null;
  code: string;
}

export function parseScan(value: string): ParsedScan {
  const text = value.replace(CONTROL, "").trim();
  const upper = text.toUpperCase();
  for (const [prefix, kind] of Object.entries(PREFIXES)) {
    if (upper.startsWith(prefix)) return { kind, code: text.slice(prefix.length).trim() };
  }
  if (text.includes("|")) return { kind: "tool", code: text.split("|", 1)[0].trim() };
  return { kind: null, code: text };
}

export function toolCode(value: string): string {
  return parseScan(value).code;
}

export function locationCode(value: string): string {
  return parseScan(value).code.toUpperCase();
}

export function sameCode(a: string, b: string): boolean {
  return a.trim().toUpperCase() === b.trim().toUpperCase();
}

/** How a code reached the form: from a scanner (fast burst of keys) or typed by hand. */
export type Capture = "scan" | "manual";
