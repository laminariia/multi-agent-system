import { useEffect, useRef } from "react";

/**
 * Hook that sends a WebSocket subscribe message when a component mounts
 * and can optionally handle incoming messages for the subscribed channel.
 *
 * The WebSocket connection is managed in _app.tsx and stored as a global ref.
 * This hook accesses it via window.__masWs.
 */
export function useWsSubscription(
  type: "subscribe:agent" | "subscribe:project",
  id: string | undefined,
  onMessage?: (msg: Record<string, unknown>) => void
) {
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  useEffect(() => {
    if (!id) return;

    const trySubscribe = () => {
      const ws = (window as unknown as Record<string, unknown>).__masWs as WebSocket | undefined;
      if (ws && ws.readyState === WebSocket.OPEN) {
        const payload: Record<string, string> = { type };
        if (type === "subscribe:agent") payload.agent = id;
        if (type === "subscribe:project") payload.project_id = id;
        ws.send(JSON.stringify(payload));
        return true;
      }
      return false;
    };

    // Try immediately, then retry every second for up to 5 seconds
    if (!trySubscribe()) {
      let attempts = 0;
      const interval = setInterval(() => {
        attempts++;
        if (trySubscribe() || attempts >= 5) {
          clearInterval(interval);
        }
      }, 1000);
      return () => clearInterval(interval);
    }
  }, [type, id]);
}
