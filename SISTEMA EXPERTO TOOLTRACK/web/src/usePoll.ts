import { useEffect, useRef } from "react";

/** Ejecuta `load` ahora y cada `ms` mientras la pestaña esté visible. */
export function usePoll(load: () => void | Promise<void>, ms: number) {
  const latest = useRef(load);
  latest.current = load;
  useEffect(() => {
    void latest.current();
    const timer = setInterval(() => {
      if (!document.hidden) void latest.current();
    }, ms);
    const onVisible = () => {
      if (!document.hidden) void latest.current();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [ms]);
}
