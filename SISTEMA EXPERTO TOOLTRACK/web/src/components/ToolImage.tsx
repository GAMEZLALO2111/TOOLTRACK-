import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, fetchImageUrl } from "../api";
import { ErrorNote } from "./Dialog";

const MAX_SIDE = 1024;

/** Shrinks a photo in the browser so the upload stays small (JPEG, longest side 1024 px). */
function shrink(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      const scale = Math.min(1, MAX_SIDE / Math.max(image.width, image.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(image.width * scale);
      canvas.height = Math.round(image.height * scale);
      const context = canvas.getContext("2d");
      if (!context) return reject(new Error("no canvas"));
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL("image/jpeg", 0.85));
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("not an image"));
    };
    image.src = url;
  });
}

interface Props {
  toolId: string;
  tn: string | null;
  /** "tool" when the tool has its own photo, "tn" when it shows the one of its TN, null when there is none. */
  source: string | null;
  canEditTool: boolean;
  canEditTn: boolean;
  onChanged: () => void;
}

export function ToolImage({ toolId, tn, source, canEditTool, canEditTn, onChanged }: Props) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const target = useRef<"tool" | "tn">("tool");

  useEffect(() => {
    let current: string | null = null;
    let cancelled = false;
    if (source) {
      void fetchImageUrl(`/inventario/herramientas/${encodeURIComponent(toolId)}/imagen`).then((made) => {
        if (cancelled) {
          if (made) URL.revokeObjectURL(made);
          return;
        }
        current = made;
        setUrl(made);
      });
    } else {
      setUrl(null);
    }
    return () => {
      cancelled = true;
      if (current) URL.revokeObjectURL(current);
    };
  }, [toolId, source]);

  const choose = useCallback((kind: "tool" | "tn") => {
    target.current = kind;
    input.current?.click();
  }, []);

  async function picked(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const data = await shrink(file);
      const path = target.current === "tn" && tn ? `/catalogo/tn/${encodeURIComponent(tn)}/imagen` : `/inventario/herramientas/${encodeURIComponent(toolId)}/imagen`;
      await api("PUT", path, { data, realizado_por: "web" });
      onChanged();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Choose a photo (PNG, JPEG or WebP).");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await api("DELETE", `/inventario/herramientas/${encodeURIComponent(toolId)}/imagen`);
      onChanged();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "The photo could not be removed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="tool-image">
      {url ? <img src={url} alt={`Tool ${toolId}`} /> : <div className="tool-image-empty muted small">No photo yet</div>}
      {source === "tn" && <div className="muted small">Photo shared by TN {tn}</div>}
      {(canEditTool || canEditTn) && (
        <div className="tool-image-actions">
          {canEditTool && (
            <button className="btn" disabled={busy} onClick={() => choose("tool")}>
              {source === "tool" ? "Replace photo" : "Add photo"}
            </button>
          )}
          {canEditTn && tn && (
            <button className="btn" disabled={busy} onClick={() => choose("tn")} title="Every tool of this TN without its own photo will show it">
              Use for the whole TN
            </button>
          )}
          {canEditTool && source === "tool" && (
            <button className="btn btn-ghost-danger" disabled={busy} onClick={() => void remove()}>
              Remove
            </button>
          )}
        </div>
      )}
      <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" capture="environment" hidden onChange={(event) => void picked(event.target.files?.[0])} />
      <ErrorNote message={error} />
    </div>
  );
}
