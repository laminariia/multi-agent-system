import { useEffect, useRef, useState } from "react";

/**
 * Animates a number counting up from 0 to the target value.
 * Returns the current displayed value (integer).
 */
export function useCountUp(
  target: number,
  opts?: { duration?: number; enabled?: boolean }
): number {
  const duration = opts?.duration ?? 600;
  const enabled = opts?.enabled ?? true;
  const [display, setDisplay] = useState(enabled ? 0 : target);
  const rafRef = useRef<number>(0);
  const prevTarget = useRef(target);

  useEffect(() => {
    if (!enabled) {
      setDisplay(target);
      return;
    }

    const from = prevTarget.current !== target ? display : 0;
    prevTarget.current = target;

    if (from === target) {
      setDisplay(target);
      return;
    }

    const start = performance.now();
    const diff = target - from;

    const tick = (now: number) => {
      const elapsed = now - start;
      const progress = Math.min(elapsed / duration, 1);
      // ease-out cubic
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplay(Math.round(from + diff * eased));
      if (progress < 1) {
        rafRef.current = requestAnimationFrame(tick);
      }
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, duration, enabled]);

  return display;
}
