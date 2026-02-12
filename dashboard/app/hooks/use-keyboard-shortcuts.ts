import { useEffect } from "react";
import { useNavigate } from "@remix-run/react";

interface Shortcut {
  key: string;
  ctrl?: boolean;
  shift?: boolean;
  handler: () => void;
  description: string;
}

/**
 * Registers global keyboard shortcuts for dashboard navigation.
 * Uses Ctrl+<key> combinations to avoid conflicts with browser defaults.
 * Shortcuts are disabled when focus is inside an input/textarea.
 */
export function useKeyboardShortcuts() {
  const navigate = useNavigate();

  useEffect(() => {
    const shortcuts: Shortcut[] = [
      { key: "d", ctrl: true, shift: true, handler: () => navigate("/dashboard"), description: "Go to Dashboard" },
      { key: "j", ctrl: true, shift: true, handler: () => navigate("/jobs"), description: "Go to Jobs" },
      { key: "h", ctrl: true, shift: true, handler: () => navigate("/hitl"), description: "Go to HITL Queue" },
      { key: "l", ctrl: true, shift: true, handler: () => navigate("/leads"), description: "Go to Leads" },
      { key: "o", ctrl: true, shift: true, handler: () => navigate("/orchestrator"), description: "Go to Orchestrator" },
      { key: "a", ctrl: true, shift: true, handler: () => navigate("/agents"), description: "Go to Agents" },
      { key: "s", ctrl: true, shift: true, handler: () => navigate("/settings"), description: "Go to Settings" },
    ];

    const handleKeyDown = (e: KeyboardEvent) => {
      // Skip when focus is in an editable element
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if ((e.target as HTMLElement)?.isContentEditable) return;

      for (const s of shortcuts) {
        const ctrlMatch = s.ctrl ? (e.ctrlKey || e.metaKey) : true;
        const shiftMatch = s.shift ? e.shiftKey : true;
        if (ctrlMatch && shiftMatch && e.key.toLowerCase() === s.key) {
          e.preventDefault();
          s.handler();
          return;
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [navigate]);
}

/**
 * Returns a description of all available keyboard shortcuts for display.
 */
export function getShortcutsList(): Array<{ keys: string; description: string }> {
  const mod = typeof navigator !== "undefined" && /Mac/.test(navigator.userAgent) ? "Cmd" : "Ctrl";
  return [
    { keys: `${mod}+Shift+D`, description: "Dashboard" },
    { keys: `${mod}+Shift+J`, description: "Jobs" },
    { keys: `${mod}+Shift+H`, description: "HITL Queue" },
    { keys: `${mod}+Shift+L`, description: "Leads" },
    { keys: `${mod}+Shift+O`, description: "Orchestrator" },
    { keys: `${mod}+Shift+A`, description: "Agents" },
    { keys: `${mod}+Shift+S`, description: "Settings" },
  ];
}
