#!/usr/bin/env python3
"""
export_model.py
open_set.py fit 이 만든 판정 모델(json)을 와드 펌웨어용 C 헤더(model.h)로 바꾼다.

  inference/openset_model.json  ──→  inference/firmware/ward_mqtt/model.h
                                       (ward_infer.h 가 읽는다)

모델은 "클래스마다 평균 방향(숫자 10개) + 허용 각도" 와 문턱 두 개뿐이라 그대로 옮기면 된다.
변환 오차가 없도록 소수 7자리까지 쓰고, 파일 끝에 원본 json 의 요약(만든 시각, 학습 녹화)을 남긴다.

사용:
  python export_model.py                         # 기본 경로
  python export_model.py --model x.json --out y/model.h
  python export_model.py --on Coffee Vinegar     # 출동 라벨 (기본: 모델에 저장된 값)
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import MODEL, WARD_FW                      # noqa: E402

N_STEPS = 10
DEFAULT_OUT = WARD_FW / "model.h"


def cfloat(x):
    s = f"{float(x):.7g}"
    if "e" not in s and "." not in s:
        s += ".0"
    return s + "f"


def export(model, on_labels, out):
    if model.get("method") != "angle":
        sys.exit(f"각도 방식 모델만 지원합니다 (지금: {model.get('method')}). open_set.py fit 기본값으로 다시 학습하세요.")
    classes = sorted(model["classes"].items())
    if not classes:
        sys.exit("학습된 클래스가 없습니다.")
    raw = json.dumps(model, sort_keys=True, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha1(raw).hexdigest()[:10]
    L = [
        "// 자동 생성 파일 — 직접 고치지 말 것. tools/export_model.py 로 다시 만든다.",
        f"// 원본: {model.get('created', '?')}  sha1:{digest}",
        "#pragma once",
        "",
        f"#define WI_MODEL_ID \"{digest}\"",
        f"#define WI_TAU_AIR    {cfloat(model['tau_air'])}   /* 이보다 작으면 Air */",
        f"#define WI_TAU_DECIDE {cfloat(model.get('tau_decide', model['tau_air']))}   /* 이보다 작으면 보류(Weak) */",
        f"#define WI_N_CLASSES  {len(classes)}",
        "",
        "/* 클래스: 이름, 출동 여부(1 = 악취), 허용 각도(도), 평균 방향(단위벡터, 10단계) */",
        "static const wi_class_t WI_CLASSES[WI_N_CLASSES] = {",
    ]
    for name, p in classes:
        mu = ", ".join(cfloat(v) for v in p["mu"])
        assert len(p["mu"]) == N_STEPS
        on = 1 if name in on_labels else 0
        L.append(f"  {{\"{name}\", {on}, {cfloat(p['angle_thr'])}, {{{mu}}}}},"
                 f"   /* 사이클 {p['n_cycles']}, 녹화 {len(p['recordings'])}개 */")
    L += ["};", "",
          f"/* 학습한 Air 녹화 {len(model.get('air_recordings', []))}개, Air 사이클 {model.get('air_cycles', '?')} */", ""]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    return digest, classes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, default=MODEL)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--on", nargs="*", default=None, help="출동 라벨 (기본: 모델의 on_labels)")
    args = ap.parse_args()
    model = json.loads(args.model.read_text(encoding="utf-8"))
    on = args.on if args.on is not None else model.get("on_labels", ["Coffee", "Vinegar"])
    digest, classes = export(model, set(on), args.out)
    print(f"[모델] {args.model}  → {args.out}  (id {digest})")
    for name, p in classes:
        print(f"  {name:10s} {'출동' if name in on else '끔  '}  허용 {p['angle_thr']:.1f}°")
    print(f"  Air 문턱 {model['tau_air']:.3f} / 판정 문턱 {model.get('tau_decide', model['tau_air']):.3f}")


if __name__ == "__main__":
    main()
