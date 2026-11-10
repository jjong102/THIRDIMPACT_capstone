#!/usr/bin/env python3
"""
analyze_run.py
predict_serial.py --log 로 저장한 실시간 기록을 학습 데이터와 비교해
오분류의 원인을 짚어낸다.

확인하는 것:
  ① 신호 크기 : 지금 향이 학습 때보다 진한가 / 옅은가
                → 학습보다 크면 크기가 큰 클래스(Citrus)로 쏠린다
  ② 신호 방향 : 향의 화학적 지문이 학습 때와 같은가
                → 방향이 맞는데 크기만 다르면 농도 문제 (cycle_unit 모델로 해결)
                → 방향까지 다르면 조건이 달라진 것 (재수집 필요)
  ③ 기준선    : 실시간 기준선이 학습의 Air 와 얼마나 어긋났나

사용법:
  python analyze_run.py run.csv --train features5_bc.csv

  run.csv 는 --mark 로 실제 향을 표시해 두었을 때 가장 유용하다.
"""

import argparse
import sys

import numpy as np
import pandas as pd

N = 10
G = [f"log_gas_{i}" for i in range(N)]
B = [f"base_{i}" for i in range(N)]
TEMP = [320, 100, 100, 100, 200, 200, 200, 320, 320, 320]


def shape(v):
    """레벨 제거"""
    return v - v.mean()


def unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run", help="predict_serial.py --log 로 만든 CSV")
    ap.add_argument("--train", default="features5_bc.csv",
                    help="학습에 쓴 기준선 차감 데이터")
    args = ap.parse_args()

    run = pd.read_csv(args.run)
    if not set(G).issubset(run.columns):
        sys.exit("실시간 로그 형식이 아닙니다 (log_gas_* 컬럼 없음)")

    tr = pd.read_csv(args.train)

    # ---------- 학습 쪽 기준 ----------
    print("=" * 66)
    print("학습 데이터 (기준)")
    print("=" * 66)
    ref = {}
    for lab, g in tr.groupby("label"):
        v = shape(g[G].to_numpy(float).mean(0))
        ref[lab] = v
        print(f"  {lab:<8} 신호 크기 {np.linalg.norm(v):.3f}")

    # ---------- 실시간 쪽 ----------
    print()
    print("=" * 66)
    print("실시간 기록")
    print("=" * 66)

    has_mark = "mark" in run.columns and run["mark"].notna().any()
    groups = run.groupby("mark") if has_mark else [("(전체)", run)]

    for mark, g in groups:
        if isinstance(mark, float) and np.isnan(mark):
            continue
        sig = shape((g[G].to_numpy(float) - g[B].to_numpy(float)).mean(0))
        mag = np.linalg.norm(sig)
        pred = g["pred"].value_counts()

        print(f"\n[{mark}]  사이클 {len(g)}개")
        print(f"  판정 분포: " + "  ".join(f"{k} {v}" for k, v in pred.items()))
        print(f"  신호 크기: {mag:.3f}", end="")

        if mark in ref:
            r = np.linalg.norm(ref[mark])
            ratio = mag / r if r > 0 else float("inf")
            print(f"   (학습 {r:.3f} 의 {ratio:.1f}배)", end="")
            if ratio > 1.4:
                print("   << 학습보다 진함")
            elif ratio < 0.7:
                print("   << 학습보다 옅음")
            else:
                print("   OK")
        else:
            print()

        # 어느 학습 클래스와 방향이 가까운가
        d = {k: np.linalg.norm(unit(sig) - unit(v)) for k, v in ref.items()
             if np.linalg.norm(v) > 0}
        near = sorted(d.items(), key=lambda x: x[1])
        print("  방향 거리: " + "  ".join(f"{k} {v:.3f}" for k, v in near))
        if mark in ref:
            own = d.get(mark)
            best = near[0]
            if own is not None and best[0] != mark:
                print(f"  → 방향이 {best[0]} 에 더 가깝다 ({best[1]:.3f} < {own:.3f})")
                print("     크기 비율이 정상인데 이러면 조건 자체가 달라진 것")
            else:
                print(f"  → 방향은 {mark} 가 가장 가까움 (정상)")

        print("  스텝별 신호:")
        print("        " + "".join(f"{t:>7}" for t in TEMP))
        print("   실시간" + "".join(f"{v:>7.3f}" for v in sig))
        if mark in ref:
            print("   학습  " + "".join(f"{v:>7.3f}" for v in ref[mark]))

    # ---------- 기준선 비교 ----------
    print()
    print("=" * 66)
    print("기준선 점검")
    print("=" * 66)
    bmean = run[B].to_numpy(float).mean(0)
    bstd = run[B].to_numpy(float).std(0).mean()
    print(f"  실시간 기준선 평균 log저항 {bmean.mean():.3f}")
    print(f"  기록 중 기준선 변동폭      {bstd:.4f}")
    if bstd > 0.05:
        print("  → 기준선이 기록 중에 많이 움직였다. 예열이 덜 됐거나 --alpha 가 큼")
    else:
        print("  → 기준선 안정")

    print()
    print("=" * 66)
    print("해석 요약")
    print("=" * 66)
    print("  크기만 크고 방향은 맞다   → 농도 문제. cycle_unit 모델을 쓰면 해결")
    print("  방향까지 다르다           → 측정 조건이 학습과 다름. 재수집 검토")
    print("  기준선 변동이 크다        → 예열 부족 또는 --alpha 과다")


if __name__ == "__main__":
    main()
