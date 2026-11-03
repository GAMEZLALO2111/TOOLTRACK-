import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import type { Board } from "./types";

const REFRESH_MS = 30000;

export function useBoard(building = "B1") {
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const alive = useRef(true);

  const reload = useCallback(async () => {
    try {
      const data = await api<Board>("GET", `/workflow/demandas/board?edificio=${encodeURIComponent(building)}`);
      if (!alive.current) return;
      setBoard(data);
      setError(null);
      setUpdatedAt(new Date());
    } catch (caught) {
      if (!alive.current) return;
      setError(caught instanceof ApiError ? caught.message : "Could not refresh the board.");
    }
  }, [building]);

  useEffect(() => {
    alive.current = true;
    void reload();
    const timer = setInterval(() => {
      if (!document.hidden) void reload();
    }, REFRESH_MS);
    const onVisible = () => {
      if (!document.hidden) void reload();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive.current = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [reload]);

  return { board, error, updatedAt, reload };
}
