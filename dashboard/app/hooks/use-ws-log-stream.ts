import { useState, useEffect, useRef, useCallback } from "react";

export interface StreamedLog {
  id: string;
  timestamp: string;
  level: "info" | "warning" | "error" | "debug";
  message: string;
  event_type?: string;
  isNew?: boolean;
}

interface UseWsLogStreamOptions {
  maxLines?: number;
}

interface UseWsLogStreamReturn {
  logs: StreamedLog[];
  isConnected: boolean;
  clearLogs: () => void;
}

const DEFAULT_MAX_LINES = 500;

export function useWsLogStream(
  agentName: string | undefined,
  options?: UseWsLogStreamOptions
): UseWsLogStreamReturn {
  const maxLines = options?.maxLines ?? DEFAULT_MAX_LINES;
  const [logs, setLogs] = useState<StreamedLog[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout>>();
  const reconnectAttemptsRef = useRef(0);
  const mountedRef = useRef(true);

  const clearLogs = useCallback(() => {
    setLogs([]);
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!agentName) return;

    function connect() {
      // Access the shared WS from the app layout
      const checkWs = () => {
        const ws = (window as unknown as Record<string, unknown>)
          .__masWs as WebSocket | undefined;

        if (!ws || ws.readyState !== WebSocket.OPEN) {
          // Retry checking for the global WS
          if (reconnectAttemptsRef.current < 10) {
            reconnectAttemptsRef.current++;
            reconnectTimeoutRef.current = setTimeout(checkWs, 1000);
          }
          return;
        }

        wsRef.current = ws;
        reconnectAttemptsRef.current = 0;

        // Subscribe to agent log stream
        ws.send(
          JSON.stringify({
            type: "subscribe:agent",
            agent: agentName,
          })
        );

        setIsConnected(true);

        const handleMessage = (event: MessageEvent) => {
          if (!mountedRef.current) return;

          try {
            const data = JSON.parse(event.data);

            // Filter for log messages from this agent
            if (
              data.type === "agent:log" &&
              data.agent === agentName
            ) {
              const newLog: StreamedLog = {
                id: data.id ?? `${Date.now()}-${Math.random()}`,
                timestamp: data.timestamp ?? new Date().toISOString(),
                level: data.level ?? "info",
                message: data.message ?? "",
                event_type: data.event_type,
                isNew: true,
              };

              setLogs((prev) => {
                const updated = [...prev, newLog];
                if (updated.length > maxLines) {
                  return updated.slice(updated.length - maxLines);
                }
                return updated;
              });

              // Clear the "isNew" flag after the animation
              setTimeout(() => {
                if (!mountedRef.current) return;
                setLogs((prev) =>
                  prev.map((l) =>
                    l.id === newLog.id ? { ...l, isNew: false } : l
                  )
                );
              }, 2000);
            }
          } catch {
            // ignore unparseable messages
          }
        };

        ws.addEventListener("message", handleMessage);

        const handleClose = () => {
          if (!mountedRef.current) return;
          setIsConnected(false);
          ws.removeEventListener("message", handleMessage);
          ws.removeEventListener("close", handleClose);

          // Attempt to reconnect
          if (reconnectAttemptsRef.current < 5) {
            reconnectAttemptsRef.current++;
            const delay = Math.min(1000 * 2 ** reconnectAttemptsRef.current, 30000);
            reconnectTimeoutRef.current = setTimeout(connect, delay);
          }
        };

        ws.addEventListener("close", handleClose);
      };

      checkWs();
    }

    connect();

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      setIsConnected(false);
    };
  }, [agentName, maxLines]);

  return { logs, isConnected, clearLogs };
}
