import { useState } from "react";
import { Dialog, ErrorNote } from "./Dialog";

interface Props {
  title: string;
  message: string;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: (reason: string) => void;
  onClose: () => void;
}

/** Pide un motivo obligatorio antes de una acción que cambia el estado de algo. */
export function ReasonDialog({ title, message, confirmLabel, danger, onConfirm, onClose }: Props) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  function submit() {
    if (!reason.trim()) return setError("Write a reason to continue.");
    onConfirm(reason.trim());
  }

  return (
    <Dialog
      title={title}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Back
          </button>
          <button className={`btn ${danger ? "btn-danger" : "btn-primary"}`} onClick={submit}>
            {confirmLabel}
          </button>
        </>
      }
    >
      <p>{message}</p>
      <label className="form">
        <span className="muted small">Reason (required)</span>
        <input value={reason} onChange={(event) => setReason(event.target.value)} autoFocus onKeyDown={(event) => event.key === "Enter" && submit()} />
      </label>
      <ErrorNote message={error} />
    </Dialog>
  );
}
