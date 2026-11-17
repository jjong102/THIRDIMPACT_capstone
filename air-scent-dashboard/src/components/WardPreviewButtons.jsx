import { useRef, useState } from "react";

import "./StatusMiniPanel.css";
import { WARD_PREVIEW_OPTIONS, toggleWardPreview, useWardPreview } from "../utils/wardPreview";

const OFFSET_KEY = "air-scent-ward-preview-offset";
const BASE_Y = 92; // 와드 자리에서 아래로 (로봇 아이콘을 가리지 않게)

function loadOffset() {
  try {
    const value = JSON.parse(window.localStorage.getItem(OFFSET_KEY) ?? "null");
    if (value && Number.isFinite(value.x) && Number.isFinite(value.y)) return value;
  } catch {
    // 저장된 값이 없거나 읽을 수 없으면 기본 자리
  }
  return { x: 0, y: 0 };
}

function saveOffset(offset) {
  try {
    window.localStorage.setItem(OFFSET_KEY, JSON.stringify(offset));
  } catch {
    // 저장 못 해도 동작
  }
}

/**
 * 지도 와드 자리 밑: 🌿 💜 🍊 🦨 효과 켜고 끄기 (이 화면에서만, 판정·출동과 무관).
 * 버튼 사이 빈 곳을 잡고 끌면 묶음이 움직이고(위치 기억), 빈 곳을 두 번 누르면 원래 자리로.
 */
export default function WardPreviewButtons({ left, top }) {
  const active = useWardPreview();
  const [offset, setOffset] = useState(loadOffset);
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef(null);

  const onPointerDown = (event) => {
    event.stopPropagation();
    if (event.target.closest("button")) return; // 버튼은 켜기·끄기만
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    dragRef.current = { id: event.pointerId, sx: event.clientX, sy: event.clientY, ox: offset.x, oy: offset.y };
    setDragging(true);
  };

  const onPointerMove = (event) => {
    const drag = dragRef.current;
    if (!drag || drag.id !== event.pointerId) return;
    event.stopPropagation();
    setOffset({ x: drag.ox + event.clientX - drag.sx, y: drag.oy + event.clientY - drag.sy });
  };

  const endDrag = (event) => {
    const drag = dragRef.current;
    if (!drag || drag.id !== event.pointerId) return;
    event.stopPropagation();
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    dragRef.current = null;
    setDragging(false);
    setOffset((current) => {
      saveOffset(current);
      return current;
    });
  };

  const onDoubleClick = (event) => {
    event.stopPropagation();
    if (event.target.closest("button")) return;
    const home = { x: 0, y: 0 };
    setOffset(home);
    saveOffset(home);
  };

  return (
    <div
      className={`ward-preview-btns${dragging ? " is-dragging" : ""}`}
      style={{
        left: `${left}%`,
        top: `${top}%`,
        transform: `translate(calc(-50% + ${offset.x}px), ${BASE_Y + offset.y}px)`,
      }}
      role="group"
      aria-label="와드 효과 미리보기 (빈 곳을 끌면 이동, 두 번 누르면 원래 자리)"
      title="빈 곳을 끌면 이동 · 두 번 누르면 원래 자리"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onClick={(event) => event.stopPropagation()}
      onDoubleClick={onDoubleClick}
    >
      {WARD_PREVIEW_OPTIONS.map((option) => {
        const on = active === option.tone;
        return (
          <button
            key={option.tone}
            type="button"
            className={`ward-preview-btn tone-${option.tone}${on ? " is-on" : ""}`}
            aria-pressed={on}
            title={`${option.label} 효과 ${on ? "끄기" : "켜기"} (미리보기)`}
            onClick={() => toggleWardPreview(option.tone)}
          >
            <span aria-hidden="true">{option.emoji}</span>
            <span>{option.label}</span>
            <em>{on ? "ON" : "OFF"}</em>
          </button>
        );
      })}
    </div>
  );
}
