import { useRef, type ComponentProps } from "react";
import type { Capture } from "../scan";

// A scanner types the whole code in a few milliseconds; a person needs far longer per character.
const SCAN_MS_PER_CHAR = 35;
const MIN_SCAN_LENGTH = 4;

interface Props extends Omit<ComponentProps<"input">, "value" | "onChange"> {
  value: string;
  onValue: (value: string, capture: Capture) => void;
}

/**
 * Text field for scanned codes. A keyboard-style scanner fills it and presses Enter (inside a form that
 * submits it); typing by hand works the same way. It reports how the value was entered.
 */
export function ScanInput({ value, onValue, ...rest }: Props) {
  const startedAt = useRef(0);
  const pasted = useRef(false);
  const previousLength = useRef(0);

  return (
    <input
      {...rest}
      value={value}
      autoComplete="off"
      autoCapitalize="characters"
      spellCheck={false}
      onPaste={() => {
        pasted.current = true;
      }}
      onChange={(event) => {
        const next = event.target.value;
        const now = performance.now();
        if (previousLength.current === 0 || next.length <= 1) startedAt.current = now;
        const elapsed = now - startedAt.current;
        const burst = next.length >= MIN_SCAN_LENGTH && elapsed / next.length < SCAN_MS_PER_CHAR;
        const capture: Capture = !pasted.current && burst ? "scan" : "manual";
        if (next.length === 0) pasted.current = false;
        previousLength.current = next.length;
        onValue(next, capture);
      }}
    />
  );
}
