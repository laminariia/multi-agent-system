import { useEffect } from "react";
import { useNavigate } from "@remix-run/react";
import { useAuthStore, useAuthHydrated } from "~/stores/auth-store";

export default function IndexRoute() {
  const navigate = useNavigate();
  const hydrated = useAuthHydrated();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  useEffect(() => {
    if (!hydrated) return;
    if (isAuthenticated) {
      navigate("/dashboard", { replace: true });
    } else {
      navigate("/login", { replace: true });
    }
  }, [hydrated, isAuthenticated, navigate]);

  return null;
}
