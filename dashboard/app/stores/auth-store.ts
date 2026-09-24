import { useSyncExternalStore } from "react";
import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { User, LoginResponse } from "~/lib/types";

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  user: User | null;
  isAuthenticated: boolean;
  login: (data: LoginResponse) => void;
  logout: () => void;
  setUser: (user: User) => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      user: null,
      isAuthenticated: false,

      login: (data: LoginResponse) => {
        set({
          accessToken: data.access_token,
          refreshToken: data.refresh_token,
          user: data.user,
          isAuthenticated: true,
        });
      },

      logout: () => {
        set({
          accessToken: null,
          refreshToken: null,
          user: null,
          isAuthenticated: false,
        });
      },

      setUser: (user: User) => {
        set({ user });
      },
    }),
    {
      name: "auth-storage",
    }
  )
);

// During SSR and the hydration render useAuthStore returns its initial logged-out state,
// so auth guards must wait for this. `persist` is undefined on the server (no localStorage);
// React calls these two callbacks only on the client.
const subscribeHydration = (onChange: () => void) => useAuthStore.persist.onFinishHydration(onChange);
const getHydrated = () => useAuthStore.persist.hasHydrated();
const getServerHydrated = () => false;

/** True once persisted auth state is loaded; false on the server and during hydration. */
export function useAuthHydrated(): boolean {
  return useSyncExternalStore(subscribeHydration, getHydrated, getServerHydrated);
}
