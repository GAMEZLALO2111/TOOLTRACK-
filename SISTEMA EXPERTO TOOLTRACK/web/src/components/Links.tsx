import type { ReactNode } from "react";
import { useAuth } from "../auth";

function Link({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a className="id-link" href={href} onClick={(event) => event.stopPropagation()}>
      {children}
    </a>
  );
}

/** A tool ID that opens the tool file; plain text for roles that cannot see tools. */
export function ToolLink({ id }: { id: string | null | undefined }) {
  const { can } = useAuth();
  if (!id) return <>—</>;
  return can("inventory.view") ? <Link href={`#/tools/${encodeURIComponent(id)}`}>{id}</Link> : <>{id}</>;
}

/** A machine code that opens its page; plain text for roles that cannot see machines. */
export function MachineLink({ code }: { code: string | null | undefined }) {
  const { can } = useAuth();
  if (!code) return <>—</>;
  return can("machine.view") ? <Link href={`#/machines/${encodeURIComponent(code)}`}>{code}</Link> : <>{code}</>;
}
