import { create } from "zustand";

export interface AppNotification {
  id: string;
  type: "hitl" | "agent" | "project" | "orch" | "system";
  title: string;
  description?: string;
  timestamp: number;
  read: boolean;
  link?: string;
}

interface NotificationState {
  notifications: AppNotification[];
  unreadCount: number;
  addNotification: (n: Omit<AppNotification, "id" | "timestamp" | "read">) => void;
  markAllRead: () => void;
  markRead: (id: string) => void;
  clear: () => void;
}

const MAX_NOTIFICATIONS = 50;

let counter = 0;

export const useNotificationStore = create<NotificationState>()((set, get) => ({
  notifications: [],
  unreadCount: 0,

  addNotification: (n) => {
    const id = `notif-${Date.now()}-${++counter}`;
    const notification: AppNotification = {
      ...n,
      id,
      timestamp: Date.now(),
      read: false,
    };
    set((state) => {
      const updated = [notification, ...state.notifications].slice(0, MAX_NOTIFICATIONS);
      return {
        notifications: updated,
        unreadCount: updated.filter((x) => !x.read).length,
      };
    });
  },

  markAllRead: () =>
    set((state) => ({
      notifications: state.notifications.map((n) => ({ ...n, read: true })),
      unreadCount: 0,
    })),

  markRead: (id) =>
    set((state) => {
      const updated = state.notifications.map((n) =>
        n.id === id ? { ...n, read: true } : n
      );
      return {
        notifications: updated,
        unreadCount: updated.filter((x) => !x.read).length,
      };
    }),

  clear: () => set({ notifications: [], unreadCount: 0 }),
}));
