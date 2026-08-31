import { Navigate, Route, Routes } from "react-router-dom";

import { ProtectedRoute } from "@/components/ProtectedRoute";
import { ApiKeysPage } from "@/pages/ApiKeysPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { LandingPage } from "@/pages/LandingPage";
import { LoginPage } from "@/pages/LoginPage";
import { RegisterPage } from "@/pages/RegisterPage";
import { SecurityPage } from "@/pages/SecurityPage";
import { SessionsPage } from "@/pages/SessionsPage";
import { WebhooksPage } from "@/pages/WebhooksPage";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />

      <Route element={<ProtectedRoute />}>
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/dashboard/api-keys" element={<ApiKeysPage />} />
        <Route path="/dashboard/sessions" element={<SessionsPage />} />
        <Route path="/dashboard/webhooks" element={<WebhooksPage />} />
        <Route path="/dashboard/security" element={<SecurityPage />} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
