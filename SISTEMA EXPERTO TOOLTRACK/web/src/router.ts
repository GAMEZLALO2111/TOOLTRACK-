import { useEffect, useState } from "react";

export interface Route {
  path: string;
  query: URLSearchParams;
}

function parse(): Route {
  const hash = window.location.hash.replace(/^#/, "") || "/board";
  const [path, search = ""] = hash.split("?");
  return { path: path || "/board", query: new URLSearchParams(search) };
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(parse);
  useEffect(() => {
    const onChange = () => setRoute(parse());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function go(path: string, params?: Record<string, string>) {
  const search = params ? `?${new URLSearchParams(params).toString()}` : "";
  window.location.hash = `${path}${search}`;
}
