#!/usr/bin/env python3
"""
compare_classes.py
라벨링된 세그먼트 폴더(training/data/)를 읽어 **클래스끼리 실제로 갈리는지**를 숫자로 본다.
학습을 돌리기 전에 "이 데이터로 애초에 가능한가"를 판정하는 용도.

왜 필요한가
  train_scent.py 의 정확도만 보면 속는다. 같은 세션 안에서 나눈 점수는
  '세기(농도)' 하나만 보고도 99% 가 나온다. 하지만 농도는 매번 달라지므로
  실전에서는 무너진다. 그래서 **모양(방향)이 다른지**를 따로 봐야 한다.

  실측 사례(2026-09-29): Smoke(인센스) vs Woody 의 방향 유사도 cos = 0.993.
  모양이 사실상 같고 크기만 2.9배 달랐다 → "약한 Woody" 와 구분 불가.

보는 지표
  1) 응답 벡터   : 각 클래스의 (Air 대비) 10스텝 평균 변화량. 향이 오면 음수.
  2) cos 유사도  : 두 클래스 응답의 **방향**이 얼마나 같은가. 1.0 = 완전히 같은 모양.
                   0.98 이상이면 크기 차이로만 갈리는 상태 → 농도가 바뀌면 무너진다.
  3) 분리도 d    : |평균차| / 합성표준편차. 클래스 쌍이 실제로 떨어져 있는지.
                   0.8 이상이면 그 스텝 하나로도 꽤 갈림, 0.3 미만은 거의 못 가림.
  4) 포화 스텝   : 측정 상한(102.4MOhm = log10 8.01)에 붙어 정보가 0인 차원.

사용:
  python compare_classes.py --ignore-heat-stab          # training/data

의존성: pandas, numpy  (matplotlib 있으면 --plot 로 그림도 저장)
"""

import argparse
import itertools
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_devkit_csv import STEP_TEMPS, parse_file  # noqa: E402
from paths import DATA                               # noqa: E402

N_STEPS = 10
SAT_LOG = np.log10(102.4e6)      # 센서 측정 상한 = log10 8.0103
FEATS = [f"log_gas_{i}" for i in range(N_STEPS)]


def load(root: Path, ignore_heat_stab: bool) -> pd.DataFrame:
    """data/<라벨>/*.csv 를 라벨별로 파싱해 사이클 특징으로 모은다."""
    frames = []
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        label, files = sub.name, sorted(sub.rglob("*.csv"))
        if not files:
            continue
        n = 0
        for f in files:
            try:
                feat, _ = parse_file(f, label, True, ignore_heat_stab, None)
            except Exception as e:
                print(f"  [SKIP] {f.name}: {e}")
                continue
            if not feat.empty:
                frames.append(feat)
                n += len(feat)
        print(f"  {label:10s} 사이클 {n:5d}개  (파일 {len(files)}개)")
    if not frames:
        sys.exit(f"{root} 아래에서 읽은 사이클이 없습니다.")
    return pd.concat(frames, ignore_index=True)


def unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir", type=Path, nargs="?", default=DATA, help="라벨 폴더들의 루트 (기본 training/data)")
    ap.add_argument("--ignore-heat-stab", action="store_true",
                    help="히터 불안정 사이클도 채택 (보통 항상 붙임)")
    ap.add_argument("--air", default="Air", help="기준선으로 쓸 라벨 (기본 Air)")
    ap.add_argument("--plot", action="store_true", help="응답 벡터 그림을 PNG 로 저장")
    args = ap.parse_args()

    if not args.data_dir.is_dir():
        sys.exit(f"폴더 없음: {args.data_dir}")
    temps = STEP_TEMPS

    print(f"=== 읽는 중: {args.data_dir} ===")
    df = load(args.data_dir, args.ignore_heat_stab)
    labels = [l for l in df["label"].unique()]
    if args.air not in labels:
        sys.exit(f"기준선 라벨 '{args.air}' 가 없습니다. 있는 라벨: {labels}")
    others = [l for l in labels if l != args.air]
    if not others:
        sys.exit(f"'{args.air}' 말고 다른 클래스가 없습니다.")

    hdr = "        " + "".join(f"{f'st{i}':>8}" for i in range(N_STEPS))
    tmp = "  (C)  " + "".join(f"{t:>8}" for t in temps)

    # ---------- 0) 포화 진단 ----------
    print("\n=== 0. 포화 진단 (측정 상한 log10 = 8.010) ===")
    print(hdr); print(tmp)
    sat = (df[FEATS].to_numpy() > SAT_LOG - 0.005).mean(0) * 100
    sd = df[FEATS].std().to_numpy()
    print("포화율%" + "".join(f"{v:8.1f}" for v in sat))
    print("표준편차" + "".join(f"{v:8.3f}" for v in sd))
    dead = [i for i in range(N_STEPS) if sat[i] > 80 or sd[i] < 0.005]
    if dead:
        print(f"→ 사실상 죽은 스텝: {dead}  (상한 포화 또는 변화 없음)")
        print(f"  실사용 차원 {N_STEPS - len(dead)}/{N_STEPS}")
    else:
        print("→ 죽은 스텝 없음. 10차원 모두 살아 있음 ✅")

    # ---------- 1) 응답 벡터 (Air 대비) ----------
    air_mean = df[df["label"] == args.air][FEATS].mean().to_numpy()
    resp, n_of = {}, {}
    print(f"\n=== 1. 응답 벡터 ({args.air} 대비 log10 변화량, 음수 = 저항 하락) ===")
    print(hdr); print(tmp)
    for l in others:
        x = df[df["label"] == l][FEATS].to_numpy() - air_mean
        resp[l], n_of[l] = x, len(x)
        print(f"{l:8s}" + "".join(f"{v:8.3f}" for v in x.mean(0))
              + f"   |크기| {np.linalg.norm(x.mean(0)):.3f}  n={len(x)}")

    # ---------- 2) 방향(모양) 유사도 ----------
    print(f"\n=== 2. 방향 유사도 cos  (1.0 = 모양이 완전히 같음 → 크기로만 갈림) ===")
    worst = None
    for a, b in itertools.combinations(others, 2):
        c = float(np.dot(unit(resp[a].mean(0)), unit(resp[b].mean(0))))
        ra = np.linalg.norm(resp[a].mean(0))
        rb = np.linalg.norm(resp[b].mean(0))
        if c > 0.98:
            v = "❌ 사실상 같은 모양 — 농도 바뀌면 무너짐"
        elif c > 0.90:
            v = "⚠ 비슷함 — 세션 분리 검증 필수"
        elif c > 0.70:
            v = "△ 갈릴 여지 있음"
        else:
            v = "✅ 뚜렷이 다름"
        print(f"  {a:8s} vs {b:8s}  cos={c:6.3f}  크기비 {max(ra,rb)/max(min(ra,rb),1e-9):5.2f}배   {v}")
        if worst is None or c > worst[0]:
            worst = (c, a, b)

    # ---------- 3) 스텝별 분리도 ----------
    print(f"\n=== 3. 스텝별 분리도 d = |평균차| / 합성표준편차 ===")
    print("    (0.8+ = 그 스텝 하나로도 꽤 갈림 / 0.3 미만 = 거의 못 가림)")
    print(hdr); print(tmp)
    for a, b in itertools.combinations(others, 2):
        x, y = resp[a], resp[b]
        d = np.abs(x.mean(0) - y.mean(0)) / np.sqrt((x.var(0) + y.var(0)) / 2 + 1e-12)
        print(f"{a[:3]}/{b[:3]:4s}" + "".join(f"{v:8.2f}" for v in d)
              + f"   최대 {d.max():.2f}")
    # Air 대비도 같이
    air_x = df[df["label"] == args.air][FEATS].to_numpy() - air_mean
    for l in others:
        x = resp[l]
        d = np.abs(x.mean(0) - air_x.mean(0)) / np.sqrt((x.var(0) + air_x.var(0)) / 2 + 1e-12)
        print(f"{args.air[:3]}/{l[:3]:4s}" + "".join(f"{v:8.2f}" for v in d)
              + f"   최대 {d.max():.2f}")

    # ---------- 4) 판정 ----------
    print("\n=== 판정 ===")
    if worst and worst[0] > 0.98:
        print(f"  가장 겹치는 쌍: {worst[1]} vs {worst[2]}  (cos {worst[0]:.3f})")
        print("  → 이 두 클래스는 지금 데이터로 안정적으로 갈 수 없습니다.")
        print("     ① 소스를 바꾸거나(예: 인센스 → 담배)  ② 히터 설정을 바꾸거나")
        print("     ③ 한쪽 클래스를 포기하는 선택이 필요합니다.")
    elif worst:
        print(f"  가장 겹치는 쌍: {worst[1]} vs {worst[2]}  (cos {worst[0]:.3f}) — 진행 가능")
        print("  → train_scent.py 로 넘어가되, 반드시 날짜 단위 GroupKFold 점수를 보세요.")
    else:
        print(f"  '{args.air}' 외 클래스가 1개뿐이라 클래스 간 비교는 생략했습니다.")
        print("  → 위 '스텝별 분리도' 만 참고하세요 (4 이상이면 그 스텝만으로 확실히 갈림).")
    ns = df["source_file"].nunique()
    print(f"  세션(파일) 수: {ns}" + ("  ⚠ 3개 미만이면 검증 불가" if ns < 3 else ""))

    # ---------- 5) 그림 ----------
    if args.plot:
        try:
            import matplotlib
            import matplotlib.pyplot as plt
            for _f in ("Malgun Gothic", "AppleGothic", "NanumGothic"):
                if any(_f == f.name for f in matplotlib.font_manager.fontManager.ttflist):
                    matplotlib.rcParams["font.family"] = _f
                    break
            matplotlib.rcParams["axes.unicode_minus"] = False
            fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 5))
            xs = range(N_STEPS)
            for l in others:
                m, s = resp[l].mean(0), resp[l].std(0)
                a1.errorbar(xs, m, yerr=s, marker="o", capsize=3, label=l)
                a2.plot(xs, unit(m), marker="o", label=l)
            a1.axhline(0, color="k", lw=0.8)
            a1.set_title(f"응답 벡터 ({args.air} 대비)"); a1.set_ylabel("Δ log10(gas)")
            a2.set_title("방향만 (크기 정규화) — 겹치면 구분 불가")
            for ax in (a1, a2):
                ax.set_xticks(list(xs))
                ax.set_xticklabels([f"{i}\n{temps[i]}C" for i in xs], fontsize=8)
                ax.set_xlabel("히터 스텝"); ax.grid(alpha=0.3); ax.legend()
            out = args.data_dir.parent / f"compare_{args.data_dir.name}.png"
            fig.tight_layout(); fig.savefig(out, dpi=130)
            print(f"\n  그림 저장 → {out}")
        except Exception as e:
            print(f"  (그림 생략: {e})")


if __name__ == "__main__":
    main()
