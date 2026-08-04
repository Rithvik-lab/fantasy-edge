import {
  useEffect, useLayoutEffect, useRef, useState, type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

const GAP = 8;   // breathing room between the trigger and the panel
const EDGE = 8;  // never let a panel touch the window edge

/**
 * Everything that floats in this app renders through here: at the body, in
 * fixed position, clamped to the window.
 *
 * Three bugs made this necessary and all three were the same mistake — an
 * absolutely-positioned panel belongs to its parent, and its parent is usually
 * the wrong box.
 *
 *  - A profile card opened from the board list was clipped by that list's own
 *    scroll box, so rows near the top produced a card cut in half.
 *  - A 268px card anchored near the right edge WIDENED THE DOCUMENT, which is
 *    where the sideways scrolling came from. Same for every w-64 tooltip on a
 *    right-hand number.
 *  - The glossary drawer never appeared, because the header carries
 *    `backdrop-blur`, and a backdrop-filter makes an element the containing
 *    block for its fixed-position descendants. A full-height drawer was being
 *    laid out inside a 44px-tall header.
 *
 * A portal at the body has no parent to be clipped by, cannot widen the page,
 * and can be clamped — so a card by an edge slides into view instead of
 * hanging off it.
 */
export function Floating({
  anchor, width, z = 60, interactive, onEnter, onLeave, children,
}: {
  anchor: DOMRect;
  width: number;
  z?: number;
  interactive?: boolean;
  onEnter?: () => void;
  onLeave?: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState<{ left: number; top: number } | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const h = el.offsetHeight;
    const w = el.offsetWidth;
    // Above by default: the pointer sits below the trigger, and a panel under
    // the cursor covers the very rows you are comparing against. Flip only
    // when there is genuinely no room.
    const above = anchor.top - h - GAP >= EDGE;
    const top = above ? anchor.top - h - GAP : anchor.bottom + GAP;
    const left = anchor.left + anchor.width / 2 - w / 2;
    setAt({
      left: Math.min(Math.max(EDGE, left), window.innerWidth - w - EDGE),
      top: Math.min(Math.max(EDGE, top), window.innerHeight - h - EDGE),
    });
  }, [anchor]);

  return createPortal(
    <div
      ref={ref}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      style={{
        position: "fixed",
        width,
        zIndex: z,
        left: at?.left ?? 0,
        top: at?.top ?? 0,
        // Measured on the first frame, placed on the second. Hidden until
        // then so nothing is ever seen in the wrong spot.
        visibility: at ? "visible" : "hidden",
      }}
      className={cn("tick-in", !interactive && "pointer-events-none")}
    >
      {children}
    </div>,
    document.body,
  );
}

/**
 * Hover state for a trigger and the panel it opens, with a grace period.
 *
 * A profile card is worth reading, and reading it means moving the pointer
 * INTO it — which leaves the trigger. Closing on that leave made the card
 * unreachable: it vanished in the gap. So the close is delayed, and entering
 * the panel cancels it.
 */
export function useHover({ delay = 110, grace = 180 } = {}) {
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const ref = useRef<HTMLSpanElement>(null);
  const openT = useRef<number | undefined>(undefined);
  const closeT = useRef<number | undefined>(undefined);

  useEffect(() => () => {
    window.clearTimeout(openT.current);
    window.clearTimeout(closeT.current);
  }, []);

  // A fixed panel does not travel with a scrolling list, so it would hang in
  // place next to nothing. Capture-phase catches inner scrollers too.
  useEffect(() => {
    if (!anchor) return;
    const close = () => setAnchor(null);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [anchor]);

  function show() {
    window.clearTimeout(closeT.current);
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    openT.current = window.setTimeout(() => setAnchor(r), delay);
  }

  function hide() {
    window.clearTimeout(openT.current);
    closeT.current = window.setTimeout(() => setAnchor(null), grace);
  }

  /** Called when the pointer enters the panel itself: cancel the close. */
  function keep() {
    window.clearTimeout(closeT.current);
  }

  return { ref, anchor, show, hide, keep };
}
