import { useState } from "react";

import { ChatView } from "./components/ChatView";
import { PersonaModal } from "./components/PersonaModal";
import { usePersona } from "./state/persona";

export function App() {
  const persona = usePersona();
  const [picking, setPicking] = useState(true);

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

  return (
    <ChatView
      key={`${persona.role}-${persona.serviceRegion ?? ""}`}
      role={persona.role}
      serviceRegion={persona.serviceRegion}
      onChangePersona={() => {
        persona.clearPersona();
        setPicking(true);
      }}
    />
  );
}
