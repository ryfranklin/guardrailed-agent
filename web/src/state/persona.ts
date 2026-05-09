import { useCallback, useState } from "react";

import type { PersonaRole } from "../api/types";

export interface PersonaState {
  role: PersonaRole | null;
  serviceRegion: string | null;
}

export interface UsePersonaResult extends PersonaState {
  setPersona: (role: PersonaRole, serviceRegion: string | null) => void;
  clearPersona: () => void;
  isReady: boolean;
}

export function usePersona(): UsePersonaResult {
  const [state, setState] = useState<PersonaState>({
    role: null,
    serviceRegion: null,
  });

  const setPersona = useCallback(
    (role: PersonaRole, serviceRegion: string | null) => {
      setState({
        role,
        serviceRegion: role === "technician_lead" ? serviceRegion : null,
      });
    },
    [],
  );

  const clearPersona = useCallback(() => {
    setState({ role: null, serviceRegion: null });
  }, []);

  return {
    ...state,
    setPersona,
    clearPersona,
    isReady: state.role !== null,
  };
}
