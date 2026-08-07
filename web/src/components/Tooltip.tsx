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
  // A mirror of `open` readable synchronously inside event handlers, plus a flag
  // marking a click whose own pointer-enter just opened the popover. A mouse
  // click fires pointer-enter *then* click on the same target, so without this
  // the enter would open and the click would immediately toggle back closed.
  const openRef = useRef(false);
  const openedByPointer = useRef(false);

  const setOpenState = (next: boolean) => {
    openRef.current = next;
    setOpen(next);
  };
  const close = () => {
    openedByPointer.current = false;
    setOpenState(false);
  };

  // While open, Escape and any click outside the wrapper dismiss the popover.
  useEffect(() => {
    if (!open) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        close();
      }
    };
    const onDocClick = (event: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) {
        close();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onDocClick);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onDocClick);
    };
  }, [open]);

  const onPointerEnter = () => {
    // Only a hover that opens from closed should suppress the following click;
    // a pointer-enter while already open leaves the click free to close.
    if (!openRef.current) {
      openedByPointer.current = true;
    }
    setOpenState(true);
  };
  const onPointerLeave = () => {
    close();
  };
  const onClick = () => {
    if (openedByPointer.current) {
      openedByPointer.current = false;
      return;
    }
    setOpenState(!openRef.current);
  };

  return (
    <span className="tooltip-wrap" ref={wrapRef}>
      <button
        type="button"
        className="tooltip-trigger"
        aria-label={label ?? "More information"}
        aria-describedby={open ? popoverId : undefined}
        onClick={onClick}
        onPointerEnter={onPointerEnter}
        onPointerLeave={onPointerLeave}
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
