import { useState } from "react";
import { useAuth } from "../auth";
import { go } from "../router";
import { PendingList } from "./Pending";
import { ReturnsTab } from "./ToolTrackingActions";

const TABS = [
  { key: "pickups", label: "Waiting for pickup" },
  { key: "machine", label: "Return from a machine" },
];

/** Every way a tool comes back from a machine, in one place. */
export function ReturnsPage() {
  const { can } = useAuth();
  const [tab, setTab] = useState("pickups");
  if (!can("kitting.view")) return <p className="note note-info">Your role cannot use Returns.</p>;
  return (
    <section className="page">
      <div className="board-bar">
        <h1>Returns</h1>
        <div className="bar-right">
          <button className="btn" onClick={() => go("/kitting")}>
            ← Kitting
          </button>
        </div>
      </div>
      <div className="tabs-inline wrap">
        {TABS.map((item) => (
          <button key={item.key} className={`btn${item.key === tab ? " btn-primary" : ""}`} onClick={() => setTab(item.key)}>
            {item.label}
          </button>
        ))}
      </div>
      {tab === "pickups" ? <PendingList /> : <ReturnsTab />}
    </section>
  );
}
