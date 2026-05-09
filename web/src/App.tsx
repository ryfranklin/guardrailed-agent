import { useEffect, useState } from "react";

import type { AskResponse } from "./api/types";
import { ChatView } from "./components/ChatView";
import { DataView } from "./components/DataView";
import { FlowView } from "./components/FlowView";
import { PersonaModal } from "./components/PersonaModal";
import { usePersona } from "./state/persona";
import { useDeveloperMode } from "./state/developerMode";

type Tab = "chat" | "data" | "flow";

export function App() {
  const persona = usePersona();
  const { enabled: developerMode } = useDeveloperMode();
  const [picking, setPicking] = useState(true);
  const [tab, setTab] = useState<Tab>("chat");
  const [lastResponse, setLastResponse] = useState<AskResponse | null>(null);

  // Snap back to Chat if the user toggles developer mode off while parked
  // on the Flow tab (which is otherwise hidden).
  useEffect(() => {
    if (!developerMode && tab === "flow") setTab("chat");
  }, [developerMode, tab]);

  // Drop any captured response when developer mode goes off so we don't
  // hold response state nobody is watching, and so flipping back on
  // doesn't show stale data from a previous session.
  useEffect(() => {
    if (!developerMode && lastResponse !== null) setLastResponse(null);
  }, [developerMode, lastResponse]);

  if (picking || !persona.isReady || persona.role === null) {
    return (
      <PersonaModal
        defaultRole={persona.role}
        defaultServiceRegion={persona.serviceRegion}
        onConfirm={(role, serviceRegion) => {
          persona.setPersona(role, serviceRegion);
          setPicking(false);
        }}
        cancellable={persona.isReady}
        onCancel={persona.isReady ? () => setPicking(false) : undefined}
      />
    );
  }

  const onChangePersona = () => {
    persona.clearPersona();
    setLastResponse(null);
    setPicking(true);
  };

  // Stable key per (persona, region) so ChatView remounts on persona change
  // and discards stale message state. DataView caches its own results per
  // (table, persona, region) so a tab flip is instant on cached entries.
  const personaKey = `${persona.role}-${persona.serviceRegion ?? ""}`;

  return (
    <div className="flex h-full flex-col">
      <nav
        className="flex items-center gap-1 border-b border-slate-200 bg-white px-4"
        role="tablist"
        aria-label="View"
      >
        <TabButton active={tab === "chat"} onClick={() => setTab("chat")}>
          Chat
        </TabButton>
        <TabButton active={tab === "data"} onClick={() => setTab("data")}>
          Data
        </TabButton>
        {developerMode && (
          <TabButton active={tab === "flow"} onClick={() => setTab("flow")}>
            Flow
          </TabButton>
        )}
      </nav>
      <div className="flex-1 overflow-hidden">
        {tab === "chat" && (
          <ChatView
            key={personaKey}
            role={persona.role}
            serviceRegion={persona.serviceRegion}
            onChangePersona={onChangePersona}
            onResponse={developerMode ? setLastResponse : undefined}
          />
        )}
        {tab === "data" && (
          <DataView
            key={personaKey}
            role={persona.role}
            serviceRegion={persona.serviceRegion}
            onChangePersona={onChangePersona}
          />
        )}
        {tab === "flow" && developerMode && (
          <FlowView
            key={personaKey}
            role={persona.role}
            serviceRegion={persona.serviceRegion}
            onChangePersona={onChangePersona}
            response={lastResponse}
          />
        )}
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
        active
          ? "border-sky-500 text-slate-900"
          : "border-transparent text-slate-500 hover:text-slate-800"
      }`}
    >
      {children}
    </button>
  );
}
