import { useEffect, useState } from "react";
import api from "../api/client";
import { AuthContext } from "./auth-context";

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [isAuthLoading, setIsAuthLoading] = useState(true);

  useEffect(() => {
    try { localStorage.removeItem("access_token"); } catch { /* storage may be disabled */ }
    const controller = new AbortController();
    api.get("/api/auth/me", { signal: controller.signal })
      .then(({ data }) => { if (!controller.signal.aborted) setUser(data); })
      .catch(() => { if (!controller.signal.aborted) setUser(null); })
      .finally(() => { if (!controller.signal.aborted) setIsAuthLoading(false); });
    return () => controller.abort();
  }, []);

  const logout = async () => {
    await api.post("/api/auth/logout");
    setUser(null);
  };

  return <AuthContext.Provider value={{ user, isAuthLoading, login: setUser, logout }}>
    {children}
  </AuthContext.Provider>;
}
