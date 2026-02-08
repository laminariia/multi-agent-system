import { useEffect } from "react";
import { useNavigate } from "@remix-run/react";
import { useAuthStore } from "~/stores/auth-store";

export default function IndexRoute() {
  const navigate = useNavigate();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  useEffect(() => {
    if (isAuthenticated) {
      navigate("/hitl", { replace: true });
    } else {
      navigate("/login", { replace: true });
    }
  }, [isAuthenticated, navigate]);

  return null;
}
