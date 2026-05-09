import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import "./styles/globals.css";
import { AppRoutes } from "./routes";
import { configureAmplify } from "./auth/amplifyConfig";
import { DeveloperModeProvider } from "./state/developerMode";

configureAmplify();

const container = document.getElementById("root");
if (!container) {
  throw new Error("missing #root element");
}

createRoot(container).render(
  <StrictMode>
    <DeveloperModeProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </DeveloperModeProvider>
  </StrictMode>,
);
