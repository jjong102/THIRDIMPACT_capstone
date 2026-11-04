#!/usr/bin/env python3
"""
replay_eval.py
실제 판정기(ward_controller.py)와 똑같은 규칙으로 녹화를 재생해 **청정기 동작 기준** 성능을 잰다.

open_set.py eval 은 사이클 하나하나를 채점한다. 하지만 실제 청정기는 "켬 판정 3사이클 연속" 일 때만 켜지고,
켠 뒤 최소 시간·연속 조건이 있어 사이클 점수와 실제 동작이 다르다. 이 도구는 그 차이를 메운다.

  녹화 하나를 뺀 데이터로 모델을 학습 → 그 녹화 원본을 처음부터 끝까지 판정기에 흘려보냄 →
  라벨 구간(training/data/<라벨>/<녹화>__segN.csv)과 청정기 ON 시점을 맞춰 본다.

채점
  악취 구간(--on 라벨, 기본 Coffee Vinegar) : 구간 시작 −30초 ~ 끝 +60초 사이에 ON 이 나오면 "출동 성공",
                                              시작부터 ON 까지 걸린 초 = 반응 시간
  그 밖의 라벨 구간(Air·향·대조 양말)        : 그 구간 안에서 ON 으로 바뀌면 "잘못 출동"
  라벨이 없는 시간(회복 중 등)                : 따로 "라벨 없는 구간 출동" 으로 센다 (참고)

사용
  python replay_eval.py                       # training/data + training/recordings 전부
  python replay_eval.py --only rec_20261102_180201_day2_sock
  python replay_eval.py -v                    # 녹화마다 ON/OFF 시점 출력
"""

import argparse
import contextlib
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import DATA, RECORDINGS                       # noqa: E402
from open_set import load_dir, fit, AIR                   # noqa: E402
from ward_controller import Controller                    # noqa: E402


def segments(data_dir, rec_path, rec):
    """그 녹화의 라벨 구간 → [(라벨, 시작초, 끝초)]. 시각은 녹화 첫 행 기준 (판정기와 같음)."""
    t0 = pd.read_csv(rec_path, usecols=[0], nrows=1).iloc[0, 0]
    out = []
    for f in Path(data_dir).glob(f"*/{rec}__seg*.csv"):
        ts = pd.to_numeric(pd.read_csv(f, usecols=[0], on_bad_lines="skip").iloc[:, 0], errors="coerce").dropna()
        if len(ts):
            out.append((f.parent.name, (ts.min() - t0) / 1000.0, (ts.max() - t0) / 1000.0))
    return sorted(out, key=lambda x: x[1])


SCENTS = ("Peppermint", "Lavender", "Orange", "Water")


def run_controller(model, rec_path, args, sprays=()):
    """sprays: [(시작초, 길이초)] — 시스템이 스스로 향을 뿌린 때 (재생에서는 향 라벨 구간으로 대신)."""
    ns = argparse.Namespace(on=args.on, on_cycles=args.on_cycles, off_cycles=args.off_cycles,
                            min_on=args.min_on, warmup=args.warmup, flat=args.flat,
                            base_at=None, track=args.track, rebase=args.rebase,
                            rebase_weak=args.rebase_weak,
                            spray_lock=args.spray_lock, on_cmd="ON000", verbose=False)
    c = Controller(model, ns, send=False)
    pending = sorted(sprays)
    with contextlib.redirect_stdout(io.StringIO()):
        for ln in rec_path.read_text(encoding="utf-8", errors="replace").splitlines():
            c.feed(ln.strip())
            while pending and pending[0][0] <= c.st.sec_now:
                a, dur = pending.pop(0)
                c.note_spray(a, dur)
    on_times, off_times, prev = [], [], False
    for sec, *_rest, on in c.log:
        if on and not prev:
            on_times.append(sec)
        if prev and not on:
            off_times.append(sec)
        prev = on
    base_sec = c.log[0][0] if c.log else None
    return on_times, off_times, base_sec, len(c.log), c.rebases


def parser():
    ap = argparse.ArgumentParser(description="녹화 단위로 빼고 학습 → 판정기 재생 → 청정기 동작 채점")
    ap.add_argument("data_dir", nargs="?", type=Path, default=DATA)
    ap.add_argument("--recordings", type=Path, default=RECORDINGS)
    ap.add_argument("--only", nargs="*", default=None, help="이 녹화만 시험 (이름, .csv 없이)")
    ap.add_argument("--on", nargs="*", default=["Coffee", "Vinegar"], help="출동해야 하는 라벨")
    ap.add_argument("--tau-mode", default="segment")
    ap.add_argument("--decide-min", type=float, default=0.3)
    ap.add_argument("--on-cycles", type=int, default=3)
    ap.add_argument("--off-cycles", type=int, default=12)
    ap.add_argument("--min-on", type=float, default=180.0)
    ap.add_argument("--warmup", type=float, default=60.0)
    ap.add_argument("--flat", type=float, default=0.05)
    ap.add_argument("--track", type=float, default=0.02)
    ap.add_argument("--rebase", type=float, default=180.0, help="평평한 Air/보류 N초면 기준선 다시 잡기 (0 끔)")
    ap.add_argument("--rebase-weak", action="store_true", help="보류(Weak)에서도 기준선 다시 잡기")
    ap.add_argument("--spray-lock", type=float, default=180.0,
                    help="향 라벨 구간을 '스스로 뿌림'으로 보고, 끝난 뒤 N초까지 Unknown 으로 안 켬 (0 끔)")
    ap.add_argument("--hit-before", type=float, default=30.0, help="악취 구간 시작 몇 초 전 ON 까지 성공으로 칠지")
    ap.add_argument("--hit-after", type=float, default=60.0, help="악취 구간 끝 몇 초 뒤 ON 까지 성공으로 칠지")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def main():
    run(parser().parse_args())


def run(args):
    """채점 결과 dict 를 돌려준다: mal(악취 구간 수), hit, lat(반응 초 목록), false_lab, false_unl, off_s"""
    print(f"=== 읽는 중: {args.data_dir} ===")
    with contextlib.redirect_stdout(io.StringIO()):
        df = load_dir(args.data_dir)
    recs = sorted(df["recording"].unique())
    if args.only:
        recs = [r for r in recs if r in set(args.only)]
    print(f"녹화 {len(recs)}개 · 출동 라벨 {', '.join(args.on)} · 규칙: 켬 {args.on_cycles}사이클 연속, "
          f"끔 {args.off_cycles}사이클 + 최소 {args.min_on:.0f}초 · 기준선 다시 잡기 "
          f"{'끔' if args.rebase <= 0 else f'{args.rebase:.0f}초'} · 발향 잠금 "
          f"{'끔' if args.spray_lock <= 0 else f'{args.spray_lock:.0f}초'}\n")

    tot = dict(mal=0, hit=0, lat=[], false_lab=0, false_unl=0, off_s=0.0)
    false_by = {}
    for R in recs:
        rec_path = args.recordings / f"{R}.csv"
        if not rec_path.exists():
            print(f"[{R}] 원본 녹화 없음 — 건너뜀")
            continue
        tr = df[df["recording"] != R]
        if (tr["label"] == AIR).sum() < 10:
            print(f"[{R}] 나머지에 Air 가 없음 — 건너뜀")
            continue
        with contextlib.redirect_stdout(io.StringIO()):
            model = fit(tr, AIR, test_only=(), tau_mode=args.tau_mode, decide_min=args.decide_min)
        model["on_labels"] = args.on
        segs = segments(args.data_dir, rec_path, R)
        sprays = [(a, b - a) for lab, a, b in segs if lab in SCENTS]
        ons, offs, base_sec, n_cyc, rebases = run_controller(model, rec_path, args, sprays)

        mal = [s for s in segs if s[0] in args.on]
        oth = [s for s in segs if s[0] not in args.on]
        used = set()
        lines = []
        for lab, a, b in mal:
            hit = [t for t in ons if a - args.hit_before <= t <= b + args.hit_after]
            tot["mal"] += 1
            if hit:
                tot["hit"] += 1
                lat = hit[0] - a
                tot["lat"].append(lat)
                used.add(hit[0])
                lines.append(f"    {lab:9s} {a/60:5.1f}~{b/60:5.1f}분  → 출동 {hit[0]/60:5.1f}분 (반응 {lat:+.0f}초)")
            else:
                lines.append(f"    {lab:9s} {a/60:5.1f}~{b/60:5.1f}분  → ✗ 출동 안 함")
        for t in ons:
            if t in used:
                continue
            where = next((lab for lab, a, b in oth if a <= t <= b), None)
            if where:
                tot["false_lab"] += 1
                false_by[where] = false_by.get(where, 0) + 1
                lines.append(f"    ✗ 잘못 출동 {t/60:5.1f}분 — {where} 구간 안")
            else:
                tot["false_unl"] += 1
                lines.append(f"    · 라벨 없는 구간 출동 {t/60:5.1f}분")
        off_s = sum(b - a for _, a, b in oth)
        tot["off_s"] += off_s
        n_false = sum(1 for ln in lines if "잘못 출동" in ln)
        print(f"[{R}]  학습 향 {', '.join(model['classes']) or '없음'} · 기준선 "
              f"{'—' if base_sec is None else f'{base_sec/60:.1f}분부터'} · 판정 {n_cyc}사이클 · "
              f"악취 {sum(1 for l in lines if '출동 ' in l and '반응' in l)}/{len(mal)} 성공 · 잘못 출동 {n_false}")
        if args.verbose or n_false or any("✗" in l for l in lines):
            for ln in lines:
                print(ln)
            if args.verbose:
                print(f"    ON 시점 {[round(t/60, 1) for t in ons]}  OFF 시점 {[round(t/60, 1) for t in offs]}"
                      f"  기준선 다시 잡음 {[round(t/60, 1) for t in rebases]}")

    print("\n=== 요약 (청정기 동작 기준) ===")
    if tot["mal"]:
        lat = np.array(tot["lat"])
        print(f"  악취 → 출동         {tot['hit']}/{tot['mal']}  ({100*tot['hit']/tot['mal']:.0f}%)"
              + (f"   반응 시간 중앙 {np.median(lat):.0f}초 (최대 {lat.max():.0f}초)" if len(lat) else ""))
    hrs = tot["off_s"] / 3600
    print(f"  잘못 출동 (라벨 구간) {tot['false_lab']}번   — 꺼야 할 라벨 구간 {hrs*60:.0f}분 중"
          + (f", 시간당 {tot['false_lab']/hrs:.1f}번" if hrs > 0 else ""))
    if false_by:
        print(f"      어디서: {false_by}")
    print(f"  라벨 없는 구간 출동  {tot['false_unl']}번  (회복 중 등 — 참고)")
    return tot


if __name__ == "__main__":
    main()
