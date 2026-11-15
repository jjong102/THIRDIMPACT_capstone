import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

import "./MapScentPopover.css";

const GAP = 14;

function MapScentPopover({ wardId, title, wide = false, onClose, children }) {
  const panelRef = useRef(null);
  const [box, setBox] = useState(null);
  const panelWidth = wide ? 360 : 196;

  const update = useCallback(() => {
    const anchor = document.querySelector(`[data-ward-anchor="${wardId}"]`);
    const panel = panelRef.current;
    if (!anchor || !panel) return;

    const rect = anchor.getBoundingClientRect();
    const width = Math.min(panelWidth, window.innerWidth - 24);
    const panelHeight = panel.offsetHeight || 120;
    let left = rect.left + rect.width / 2 - width / 2;
    left = Math.max(12, Math.min(left, window.innerWidth - width - 12));

    let top = rect.top - panelHeight - GAP;
    let placement = "above";
    if (top < 12) {
      top = rect.bottom + GAP;
      placement = "below";
    }
    if (top + panelHeight > window.innerHeight - 12) {
      top = Math.max(12, window.innerHeight - panelHeight - 12);
    }

    setBox({
      left,
      top,
      width,
      placement,
      anchorX: rect.left + rect.width / 2 - left,
    });
  }, [panelWidth, wardId]);

  useLayoutEffect(() => {
    if (wide) return undefined;
    update();
    const timer = window.setInterval(update, 200);
    window.addEventListener("resize", update);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("resize", update);
    };
  }, [update, wide]);

  if (wide) {
    return createPortal(
      <div
        className="map-scent-backdrop"
        onPointerDown={(event) => event.stopPropagation()}
      >
        <div
          className="map-scent-popover wide centered"
          role="dialog"
          aria-label={title}
          onPointerDown={(event) => event.stopPropagation()}
        >
          <header className="map-scent-popover-head">
            <p>{title}</p>
            <button type="button" onClick={onClose} aria-label="닫기">
              <X size={16} />
            </button>
          </header>
          <div className="map-scent-popover-body">{children}</div>
        </div>
      </div>,
      document.body
    );
  }

  return createPortal(
    <div
      ref={panelRef}
      className={`map-scent-popover compact ${box?.placement ?? "above"}`}
      style={
        box
          ? { left: box.left, top: box.top, width: box.width }
          : { left: -9999, top: 0, width: panelWidth }
      }
      role="dialog"
      aria-label={title}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <span
        className="map-scent-popover-arrow"
        style={box ? { left: box.anchorX } : undefined}
        aria-hidden="true"
      />
      <div className="map-scent-popover-body">{children}</div>
    </div>,
    document.body
  );
}

export default MapScentPopover;
