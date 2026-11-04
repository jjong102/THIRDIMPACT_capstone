#!/usr/bin/env python3
"""
update_model.py
수집·라벨링이 끝난 뒤 **명령 하나로** 모델을 새로 만들고, 검사를 통과할 때만 바꾼다.

  1. 학습        training/data → 새 모델 (임시 파일)
  2. 성능 검사    replay_eval: 녹화 하나씩 빼고 학습 → 판정기 재생 → 악취 출동률·잘못 출동 횟수
  3. 펌웨어 검사  firmware_parity: 와드 C 판정이 파이썬과 같은지 + 무선 모델 읽기 시험
  4. 통과하면     이전 모델을 inference/model_history/ 에 보관 → 새 모델로 교체 → model.h 다시 생성
  5. (--push)    MQTT 로 와드에 바로 보냄 (push_model.py) — 펌웨어 다시 올릴 필요 없음

통과 기준 (바꿀 수 있음): 악취 출동 ≥ 90% · 잘못 출동 ≤ 시간당 1번 · C 판정 일치 ≥ 85% · 무선 모델 읽기 정상.
기준에 못 미치면 지금 모델을 그대로 두고 이유를 보여 준다 (--force 면 그래도 교체).

사용:
  python update_model.py                 # 학습 → 검사 → 교체 (와드에는 안 보냄)
  python update_model.py --push          # + 와드로 무선 전송
  python update_model.py --skip-parity   # gcc 가 없는 PC (Jetson 등)
  python update_model.py --rollback      # 바로 이전 모델로 되돌림 (+ --push 로 와드도)
"""

import argparse
import contextlib
import io
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import DATA, MODEL                              # noqa: E402
from open_set import load_dir, fit, AIR                    # noqa: E402
from export_model import export, model_id                  # noqa: E402
import replay_eval                                          # noqa: E402

HISTORY = MODEL.parent / "model_history"
FIT_ARGS = dict(tau_mode="segment", decide_min=0.3)        # 수집_가이드·CLAUDE.md 의 기본 학습 설정


def step(n, title):
    print(f"\n━━━ {n}. {title} ━━━")


def train(on):
    with contextlib.redirect_stdout(io.StringIO()):
        df = load_dir(DATA)
    model = fit(df, AIR, **FIT_ARGS)
    model["on_labels"] = list(on)
    return model, df


def describe_change(old, new):
    rows = []
    for c in sorted(set(old.get("classes", {})) | set(new["classes"])):
        a, b = old.get("classes", {}).get(c), new["classes"].get(c)
        if a and b:
            rows.append(f"  {c:10s} 허용 {a['angle_thr']:5.1f}° → {b['angle_thr']:5.1f}°   녹화 {len(a['recordings'])} → {len(b['recordings'])}")
        elif b:
            rows.append(f"  {c:10s} 새로 추가 (허용 {b['angle_thr']:.1f}°, 녹화 {len(b['recordings'])})")
        else:
            rows.append(f"  {c:10s} 빠짐")
    rows.append(f"  Air 문턱 {old.get('tau_air', float('nan')):.3f} → {new['tau_air']:.3f}")
    return "\n".join(rows)


def rollback(args):
    hist = sorted(HISTORY.glob("openset_model_*.json"))
    if not hist:
        sys.exit("되돌릴 이전 모델이 없습니다.")
    prev = hist[-1]
    model = json.loads(prev.read_text(encoding="utf-8"))
    shutil.copy(MODEL, HISTORY / f"openset_model_{datetime.now():%Y%m%d_%H%M%S}_rolledback_{model_id(json.loads(MODEL.read_text(encoding='utf-8')))}.json")
    shutil.copy(prev, MODEL)
    prev.unlink()
    digest, _ = export(model, set(model.get("on_labels", ["Coffee", "Vinegar"])), export_out())
    print(f"되돌림: {prev.name} → {MODEL.name}  (model.h id {digest})")
    if args.push:
        push(model, args)


def export_out():
    from export_model import DEFAULT_OUT
    return DEFAULT_OUT


def push(model, args):
    import push_model
    from export_model import model_text
    txt = model_text(model, set(model.get("on_labels", ["Coffee", "Vinegar"])))
    ack = push_model.push(txt, args.device, args.wait, expect_id=model_id(model))
    if ack and ack.get("result") in ("ok", "same"):
        print(f"✓ 와드 적용: model {ack.get('model')}")
    elif ack:
        print(f"✗ 와드 거절: {ack}")
    else:
        print("답장 없음 — 와드가 꺼져 있으면 켜질 때 받는다 (retained).")


def main():
    ap = argparse.ArgumentParser(description="학습 → 검사 → (통과 시) 모델 교체 → (선택) 와드 전송")
    ap.add_argument("--on", nargs="*", default=["Coffee", "Vinegar"], help="출동 라벨")
    ap.add_argument("--min-hit", type=float, default=0.90, help="악취 출동률 최소 (기본 0.90)")
    ap.add_argument("--max-false-per-h", type=float, default=1.0, help="잘못 출동 최대 (시간당, 기본 1.0)")
    ap.add_argument("--min-parity", type=float, default=0.85, help="C 판정 일치 최소 (기본 0.85)")
    ap.add_argument("--skip-eval", action="store_true", help="성능 검사 생략 (빠르지만 위험)")
    ap.add_argument("--skip-parity", action="store_true", help="펌웨어 검사 생략 (gcc 없는 PC)")
    ap.add_argument("--force", action="store_true", help="기준에 못 미쳐도 교체")
    ap.add_argument("--push", action="store_true", help="교체 후 MQTT 로 와드에 보냄")
    ap.add_argument("--device", default="ward-01")
    ap.add_argument("--wait", type=float, default=60.0, help="와드 답장 기다릴 초")
    ap.add_argument("--rollback", action="store_true", help="이전 모델로 되돌림")
    args = ap.parse_args()

    if args.rollback:
        rollback(args)
        return

    old = json.loads(MODEL.read_text(encoding="utf-8")) if MODEL.exists() else {}
    problems = []

    step(1, "학습")
    new, df = train(args.on)
    for lab, g in df.groupby("label"):
        print(f"  {lab:10s} 사이클 {len(g):5d}  녹화 {g['recording'].nunique()}개")
    print(f"\n  새 모델 id {model_id(new)}  (지금 {model_id(old) if old else '없음'})")
    print(describe_change(old, new))
    for c, n in new.get("skipped", {}).items():
        print(f"  {c:10s} 학습 제외 (확실한 사이클 {n}개 < 15) — 대조군이면 정상")

    if not args.skip_eval:
        step(2, "성능 검사 (녹화 하나씩 빼고 학습 → 판정기 재생)")
        ns = replay_eval.parser().parse_args([])
        ns.on = list(args.on)
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            tot = replay_eval.run(ns)
        summary = buf.getvalue().split("=== 요약", 1)[-1].split("\n", 1)[-1]
        print("  " + summary.strip().replace("\n", "\n  "))
        hit = tot["hit"] / tot["mal"] if tot["mal"] else float("nan")
        fph = tot["false_lab"] / (tot["off_s"] / 3600) if tot["off_s"] else 0.0
        if tot["mal"] and hit < args.min_hit:
            problems.append(f"악취 출동률 {hit:.0%} < {args.min_hit:.0%}")
        if fph > args.max_false_per_h:
            problems.append(f"잘못 출동 시간당 {fph:.1f}번 > {args.max_false_per_h}")
        if not tot["mal"]:
            print("  (악취 라벨이 없어 출동률은 확인 못 함)")

    tmpdir = Path(tempfile.mkdtemp())
    tmp_model = tmpdir / "new_model.json"
    tmp_model.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
    if not args.skip_parity:
        step(3, "펌웨어 검사 (와드 C 판정 == 파이썬, 무선 모델 읽기)")
        try:
            import firmware_parity
            r = firmware_parity.run(None, tmp_model, verbose=False)
            print(f"  라운드 판정 일치 {r['round_match']:.1%} ({r['rounds']}라운드) · 출동 파이썬 {r['on_py']} / C {r['on_c']}, "
                  f"1분 안 일치 {r['on_matched']} · 무선 모델 읽기 {'정상' if r['parser_ok'] else '✗ 이상'}")
            if r["round_match"] < args.min_parity:
                problems.append(f"C 판정 일치 {r['round_match']:.0%} < {args.min_parity:.0%}")
            if not r["parser_ok"]:
                problems.append("무선 모델 읽기 시험 실패")
        except RuntimeError as e:
            problems.append(f"펌웨어 검사 못 함: {e}")

    step(4, "결정")
    if problems and not args.force:
        print("✗ 기준 미달 — 지금 모델을 그대로 둡니다:")
        for p in problems:
            print(f"   - {p}")
        print("  (그래도 바꾸려면 --force)")
        shutil.rmtree(tmpdir, ignore_errors=True)
        sys.exit(1)
    if problems:
        print("△ 기준 미달이지만 --force 로 교체:", "; ".join(problems))
    HISTORY.mkdir(parents=True, exist_ok=True)
    if MODEL.exists():
        keep = HISTORY / f"openset_model_{datetime.now():%Y%m%d_%H%M%S}_{model_id(old)}.json"
        shutil.copy(MODEL, keep)
        print(f"  이전 모델 보관 → {keep.relative_to(MODEL.parent)}")
    shutil.copy(tmp_model, MODEL)
    shutil.rmtree(tmpdir, ignore_errors=True)
    digest, _ = export(new, set(args.on), export_out())
    print(f"✓ 새 모델 적용: {MODEL.name}  ·  model.h 다시 생성 (id {digest})")

    if args.push:
        step(5, "와드로 무선 전송")
        push(new, args)
    else:
        print("\n다음: 와드에 반영하려면  python push_model.py  (무선)  또는  Arduino IDE 로 ward_mqtt 업로드")


if __name__ == "__main__":
    main()
