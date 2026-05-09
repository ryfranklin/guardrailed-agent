/* eslint-disable react-refresh/only-export-components */
import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";

const STORAGE_KEY = "gagent.developer_mode";

interface DeveloperModeContextValue {
  enabled: boolean;
  setEnabled: (enabled: boolean) => void;
}

const DeveloperModeContext = createContext<DeveloperModeContextValue | null>(
  null,
);

function readInitial(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

interface DeveloperModeProviderProps {
  children: ReactNode;
}

export function DeveloperModeProvider({ children }: DeveloperModeProviderProps) {
  const [enabled, setEnabledState] = useState<boolean>(readInitial);

  const setEnabled = useCallback((value: boolean) => {
    setEnabledState(value);
    try {
      window.localStorage.setItem(STORAGE_KEY, value ? "1" : "0");
    } catch {
      // localStorage may be unavailable (private mode, quota); state still updates.
    }
  }, []);

  return (
    <DeveloperModeContext.Provider value={{ enabled, setEnabled }}>
      {children}
    </DeveloperModeContext.Provider>
  );
}

export function useDeveloperMode(): DeveloperModeContextValue {
  const ctx = useContext(DeveloperModeContext);
  if (!ctx) {
    throw new Error(
      "useDeveloperMode must be used inside a DeveloperModeProvider",
    );
  }
  return ctx;
}

export const __DEVELOPER_MODE_INTERNALS = { STORAGE_KEY };
