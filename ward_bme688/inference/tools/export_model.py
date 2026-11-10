#!/usr/bin/env python3
"""
export_model.py
open_set.py fit 이 만든 판정 모델(json)을 와드 펌웨어가 쓰는 두 가지 형태로 바꾼다.

  model/openset_model.json
     ├──▶ firmware/ward/model.h   펌웨어에 고정되는 "기본 모델" (업로드할 때 들어감)
     └──▶ 글자 형식 (model_text)                  MQTT 로 와드에 바로 보내는 모델 (tools/push_model.py)

모델은 "클래스마다 평균 방향(숫자 10개) + 허용 각도" 와 문턱 두 개뿐이라 그대로 옮기면 된다.
모델 id = 원본 json 의 sha1 앞 10자리. 와드는 부팅·모델 교체 때 이 id 를 시리얼과 MQTT 로 알린다.

사용:
  python export_model.py                         # 기본 경로 (model.h 생성)
  python export_model.py --text                  # 글자 형식도 화면에 출력
  python export_model.py --model x.json --out y/model.h
  python export_model.py --on Coffee Vinegar     # 출동 라벨 (기본: 모델에 저장된 값)
"""

import argparse
import hashlib
import json
import sys
import zlib
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import MODEL, WARD_FW                      # noqa: E402

N_STEPS = 10
MAX_CLASSES = 8          # ward_infer.h WI_MAX_CLASSES
NAME_LEN = 16            # ward_infer.h WI_NAME_LEN (끝 '\0' 포함)
DEFAULT_OUT = WARD_FW / "model.h"


def cfloat(x):
    s = f"{float(x):.7g}"
    if "e" not in s and "." not in s:
        s += ".0"
    return s + "f"


def model_id(model):
    """내용으로 정하는 id — 학습 시각(created)은 빼서 같은 데이터·설정이면 같은 id."""
    raw = json.dumps({k: v for k, v in model.items() if k != "created"}, sort_keys=True,
                     ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:10]


def _check(model):
    if model.get("method") != "angle":
        sys.exit(f"각도 방식 모델만 지원합니다 (지금: {model.get('method')}). open_set.py fit 기본값으로 다시 학습하세요.")
    classes = sorted(model["classes"].items())
    if not classes:
        sys.exit("학습된 클래스가 없습니다.")
    if len(classes) > MAX_CLASSES:
        sys.exit(f"클래스가 {len(classes)}개 — 펌웨어 최대 {MAX_CLASSES}개")
    for name, p in classes:
        if len(name.encode("utf-8")) >= NAME_LEN or " " in name:
            sys.exit(f"클래스 이름 '{name}' 이 너무 길거나 공백이 있습니다 (영문 {NAME_LEN-1}자 이하)")
        assert len(p["mu"]) == N_STEPS
    return classes


def model_text(model, on_labels):
    """MQTT 로 보내는 글자 형식. ward_infer.h wi_model_parse() 가 읽는다. 마지막 줄 = 앞 전부의 CRC-32."""
    classes = _check(model)
    lines = ["WARDMODEL 1", f"id {model_id(model)}",
             f"tau {model['tau_air']:.7g} {model.get('tau_decide', model['tau_air']):.7g}"]
    for name, p in classes:
        mu = " ".join(f"{v:.7g}" for v in p["mu"])
        lines.append(f"class {name} {1 if name in on_labels else 0} {p['angle_thr']:.7g} {mu}")
    lines.append("end")
    body = "\n".join(lines) + "\n"
    return body + f"crc {zlib.crc32(body.encode('ascii')) & 0xFFFFFFFF:08x}\n"


def export(model, on_labels, out):
    classes = _check(model)
    digest = model_id(model)
    L = [
        "// 자동 생성 파일 — 직접 고치지 말 것. tools/export_model.py 로 다시 만든다.",
        f"// 원본: {model.get('created', '?')}  id:{digest}",
        "// 펌웨어에 고정되는 기본 모델. 와드가 MQTT 로 새 모델을 받으면(tools/push_model.py) 그쪽을 쓴다.",
        "#pragma once",
        "",
        f"#define WI_DEFAULT_MODEL_ID \"{digest}\"",
        "",
        "static const wi_model_t WI_DEFAULT_MODEL = {",
        f"  \"{digest}\",",
        f"  {cfloat(model['tau_air'])},   /* tau_air: 이보다 작으면 Air */",
        f"  {cfloat(model.get('tau_decide', model['tau_air']))},   /* tau_decide: 이보다 작으면 보류(Weak) */",
        f"  {len(classes)},",
        "  {   /* 이름, 출동(1 = 악취), 허용 각도(도), 평균 방향(단위벡터, 10단계) */",
    ]
    for name, p in classes:
        mu = ", ".join(cfloat(v) for v in p["mu"])
        on = 1 if name in on_labels else 0
        L.append(f"    {{\"{name}\", {on}, {cfloat(p['angle_thr'])}, {{{mu}}}}},"
                 f"   /* 사이클 {p['n_cycles']}, 녹화 {len(p['recordings'])}개 */")
    L += ["  }", "};", "",
          f"/* 학습한 Air 녹화 {len(model.get('air_recordings', []))}개, Air 사이클 {model.get('air_cycles', '?')} */", ""]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    return digest, classes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, default=MODEL)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--on", nargs="*", default=None, help="출동 라벨 (기본: 모델의 on_labels)")
    ap.add_argument("--text", action="store_true", help="MQTT 로 보낼 글자 형식도 출력")
    args = ap.parse_args()
    model = json.loads(args.model.read_text(encoding="utf-8"))
    on = args.on if args.on is not None else model.get("on_labels", ["Coffee", "Vinegar"])
    digest, classes = export(model, set(on), args.out)
    print(f"[모델] {args.model}  → {args.out}  (id {digest})")
    for name, p in classes:
        print(f"  {name:10s} {'출동' if name in on else '끔  '}  허용 {p['angle_thr']:.1f}°")
    print(f"  Air 문턱 {model['tau_air']:.3f} / 판정 문턱 {model.get('tau_decide', model['tau_air']):.3f}")
    if args.text:
        print("\n--- 글자 형식 ---\n" + model_text(model, set(on)), end="")


if __name__ == "__main__":
    main()
