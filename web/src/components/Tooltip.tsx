// A hand-rolled information affordance — an `ⓘ` button that reveals a short
// explanation. No tooltip dependency and no floating-UI library; positioning is
// left to the stylesheet. These sit inside forms, so the trigger is always a
// `type="button"` and must never submit.

import { useEffect, useId, useRef, useState } from "react";
import { InfoIcon } from "./icons";

type TooltipProps = { text: string; label?: string };

export function Tooltip({ text, label }: TooltipProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const popoverId = useId();
  const wrapRef = useRef<HTMLSpanElement>(null);

  // While open, Escape and any click outside the wrapper dismiss the popover.
  useEffect(() => {
    if (!open) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };
    const onDocClick = (event: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onDocClick);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onDocClick);
    };
  }, [open]);

  return (
    <span className="tooltip-wrap" ref={wrapRef}>
      <button
        type="button"
        className="tooltip-trigger"
        aria-label={label ?? "More information"}
        aria-describedby={open ? popoverId : undefined}
        onClick={() => setOpen((prev) => !prev)}
        onPointerEnter={() => setOpen(true)}
        onPointerLeave={() => setOpen(false)}
      >
        <InfoIcon />
      </button>
      {open && (
        <span id={popoverId} role="tooltip" className="tooltip-popover">
          {text}
        </span>
      )}
    </span>
  );
}
