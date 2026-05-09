import { Navigate, Route, Routes } from "react-router-dom";

import { App } from "./App";
import { AuthGate } from "./auth/AuthGate";
import { AuthCallback } from "./auth/AuthCallback";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/auth/callback" element={<AuthCallback />} />
      <Route
        path="/"
        element={
          <AuthGate>
            <App />
          </AuthGate>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
