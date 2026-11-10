#!/usr/bin/env python3
"""
plot_scents.py
향마다 센서가 무엇을 보는지 그림 4장으로 보여준다.

  A. 원본 지문      : 히터 10스텝의 log10 가스저항. 향이 진할수록 아래로 내려간다
  B. Air 대비 차이  : 향 - Air. 어느 온도에서 반응이 큰지 (= 신호)
  C. 방향만 (정규화): 크기를 1로 맞춰 농도를 지운 것. 분류기가 실제로 보는 형태.
                      여기서 겹치는 향은 구분이 불가능하다
  D. 시간에 따른 세기: 블록 안에서 신호가 안정적인지. 계속 변하면 과도구간이라
                      그 블록은 "그 향의 대표값"이 아니다

사용법:
  python plot_scents.py Air=LOG_0020.csv Citrus=LOG_0021.csv Floral=LOG_0023.csv
  python plot_scents.py Air=... Citrus=... --out myplot.png

  ※ 'Air' 라벨이 있으면 자동으로 기준선이 된다 (B, C 패널에 필요).
  ※ 라벨을 생략하면 파일명이 라벨이 된다.
"""

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd

N_STEPS = 10
TEMP_PROFILE = [320, 100, 100, 100, 200, 200, 200, 320, 320, 320]
HOLD = [5, 2, 10, 30, 5, 5, 5, 5, 5, 5]      # 각 스텝의 머무는 배수

ALIASES = {
    "time": ["TimeStamp(ms)", "TimeStamp", "timestamp"],
    "sensor_index": ["Sensor Index", "sensor_index"],
    "gas": ["Gas Resistance(ohm)", "Gas Resistance", "gas_resistance"],
    "step": ["Gas Index", "gas_index"],
    "valid": ["Gas Valid", "gas_valid"],
}


def setup_font():
    """한글 폰트가 있으면 쓰고, 없으면 영문 라벨로 대체."""
    for name in ("Malgun Gothic", "AppleGothic", "NanumGothic",
                 "Noto Sans CJK KR", "Noto Sans KR"):
        try:
            font_manager.findfont(name, fallback_to_default=False)
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False
            return True
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False
    return False


KO = setup_font()


def T(ko, en):
    """한글 폰트가 없으면 영문으로."""
    return ko if KO else en


def load_cycles(path):
    """원본 CSV → 사이클 단위 10차원 log저항 행렬"""
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    col = {k: next((a for a in al if a in df.columns), None)
           for k, al in ALIASES.items()}
    for need in ("time", "sensor_index", "gas", "step"):
        if col[need] is None:
            raise KeyError(f"{path.name}: '{need}' 컬럼 없음")

    w = pd.DataFrame({
        "t": pd.to_numeric(df[col["time"]], errors="coerce"),
        "s": pd.to_numeric(df[col["sensor_index"]], errors="coerce"),
        "step": pd.to_numeric(df[col["step"]], errors="coerce"),
        "gas": pd.to_numeric(df[col["gas"]], errors="coerce"),
    })
    if col["valid"]:
        w = w[pd.to_numeric(df[col["valid"]], errors="coerce").fillna(1) != 0]
    w = w.dropna(subset=["t", "s", "step", "gas"])
    w = w[w["gas"] > 0]
    w["s"] = w["s"].astype(int)
    w["step"] = w["step"].astype(int)

    rows, times = [], []
    for s, g in w.groupby("s", sort=False):
        g = g.reset_index(drop=True)
        cid = (g["step"] < g["step"].shift(fill_value=-1)).cumsum()
        for _, cyc in g.groupby(cid):
            if sorted(cyc["step"].tolist()) != list(range(N_STEPS)):
                continue
            cyc = cyc.sort_values("step")
            rows.append(np.log10(cyc["gas"].to_numpy()))
            times.append(cyc["t"].mean())

    M = np.array(rows)
    t = np.array(times)
    o = np.argsort(t)
    return M[o], (t[o] - t.min()) / 1000.0


def unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def main(args):
    out = "scent_profiles.png"
    items = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--out":
            out = args[i + 1]; i += 2; continue
        lab, p = a.split("=", 1) if "=" in a else (Path(a).stem, a)
        items.append((lab, Path(p)))
        i += 1

    data, times = {}, {}
    for lab, p in items:
        M, t = load_cycles(p)
        data[lab], times[lab] = M, t
        print(f"[{lab}] {p.name}: 사이클 {len(M)}개, {t.max():.0f}s")

    labels = list(data)
    colors = plt.cm.tab10(np.linspace(0, 1, 10))
    cmap = {lab: colors[i % 10] for i, lab in enumerate(labels)}

    base_key = next((k for k in labels if k.lower().startswith("air")
                     and "after" not in k.lower()), None)

    x = np.arange(N_STEPS)
    xt = [f"{i}\n{TEMP_PROFILE[i]}C" for i in range(N_STEPS)]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # ---------- A. 원본 지문 ----------
    ax = axes[0, 0]
    for lab in labels:
        M = data[lab]
        m, sd = M.mean(0), M.std(0)
        ax.plot(x, m, "o-", color=cmap[lab], label=lab, lw=2, ms=5)
        ax.fill_between(x, m - sd, m + sd, color=cmap[lab], alpha=0.15)
    ax.set_title(T("A. 원본 지문 — 아래로 내려갈수록 향이 진함",
                   "A. Raw fingerprint (lower = stronger scent)"))
    ax.set_ylabel(T("log10 가스저항 (ohm)", "log10 gas resistance"))
    ax.set_xticks(x); ax.set_xticklabels(xt, fontsize=8)
    ax.legend(fontsize=9); ax.grid(alpha=0.3)

    # ---------- B. Air 대비 차이 ----------
    ax = axes[0, 1]
    if base_key:
        b = data[base_key].mean(0)
        for lab in labels:
            if lab == base_key:
                continue
            d = data[lab].mean(0) - b
            ax.plot(x, d, "o-", color=cmap[lab], lw=2, ms=5,
                    label=f"{lab}  (L2={np.linalg.norm(d):.2f})")
        ax.axhline(0, color="k", lw=1, ls="--", alpha=0.5)
        ax.set_title(T(f"B. 신호 = 향 - {base_key}  (아래로 클수록 강한 반응)",
                       f"B. Signal = scent - {base_key}"))
        ax.set_ylabel(T("log10 차이", "log10 difference"))
        ax.legend(fontsize=9)
    else:
        ax.text(0.5, 0.5, T("'Air' 라벨이 없어 계산 불가",
                            "No 'Air' label"), ha="center", va="center")
    ax.set_xticks(x); ax.set_xticklabels(xt, fontsize=8); ax.grid(alpha=0.3)

    # ---------- C. 신호의 방향 (분류기 관점) ----------
    # 주의: 원본 지문을 그대로 정규화하면 히터 프로파일 자체의 큰 구조
    # (0번 낮고 1~3번 높고 ...)가 전체를 지배해서 향 차이가 묻힌다.
    # 파이프라인은 baseline_correct 로 Air 를 뺀 뒤 unit 정규화하므로,
    # 여기서도 'Air 대비 차이'의 방향을 봐야 실제 분류 조건과 일치한다.
    ax = axes[1, 0]
    shapes = {}
    if base_key:
        b = data[base_key].mean(0)
        for lab in labels:
            if lab == base_key:
                continue
            shapes[lab] = unit(data[lab].mean(0) - b)
            ax.plot(x, shapes[lab], "o-", color=cmap[lab], label=lab, lw=2, ms=5)
    else:
        for lab in labels:
            v = data[lab].mean(0)
            shapes[lab] = unit(v - v.mean())
            ax.plot(x, shapes[lab], "o-", color=cmap[lab], label=lab, lw=2, ms=5)
    ax.axhline(0, color="k", lw=1, ls="--", alpha=0.5)
    ax.set_title(T("C. 신호의 방향 (농도 제거) - 분류기가 보는 형태",
                   "C. Signal direction (concentration removed)"))
    ax.set_ylabel(T("정규화 값", "normalized"))
    ax.set_xticks(x); ax.set_xticklabels(xt, fontsize=8)
    ax.legend(fontsize=9); ax.grid(alpha=0.3)

    # 거리 행렬을 그림 안에 적어준다
    ks = list(shapes)
    if len(ks) >= 2:
        lines = [T("향끼리 방향 거리:", "pairwise distance:")]
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                d = np.linalg.norm(shapes[ks[i]] - shapes[ks[j]])
                mark = "  <<" if d < 0.10 else ""
                lines.append(f"{ks[i]}-{ks[j]}: {d:.3f}{mark}")
        ax.text(1.02, 0.98, "\n".join(lines), transform=ax.transAxes,
                fontsize=8, va="top",
                bbox=dict(boxstyle="round", fc="lightyellow", alpha=0.9))

    # ---------- D. 시간에 따른 세기 ----------
    ax = axes[1, 1]
    if base_key:
        b = data[base_key].mean(0)
        for lab in labels:
            M, t = data[lab], times[lab]
            if lab == base_key or len(M) < 10:
                continue
            step = max(1, len(M) // 20)
            xs, ys = [], []
            for k in range(0, len(M) - step, step):
                xs.append(t[k:k + step].mean())
                ys.append(np.linalg.norm(M[k:k + step].mean(0) - b))
            ax.plot(xs, ys, "o-", color=cmap[lab], label=lab, lw=2, ms=4)
        ax.axhspan(0.5, 1.0, color="green", alpha=0.12)
        ax.text(0.02, 0.75, T("← 적정 구간 (0.5~1.0)", "target 0.5-1.0"),
                transform=ax.transAxes, fontsize=8, color="green")
        ax.set_title(T("D. 블록 안에서 신호가 안정적인가",
                       "D. Signal stability within block"))
        ax.set_xlabel(T("블록 시작 후 경과 (초)", "seconds into block"))
        ax.set_ylabel(T("Air 대비 신호 크기", "signal magnitude"))
        ax.legend(fontsize=9)
    ax.grid(alpha=0.3)

    fig.suptitle(T("BME688 향별 센서 반응", "BME688 scent responses"),
                 fontsize=14)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    print(f"\n→ {out} 저장")
    if not KO:
        print("  (한글 폰트를 못 찾아 영문 라벨로 출력했습니다)")

    print("\n읽는 법:")
    print("  A: 선이 아래에 있을수록 향이 진함. 띠는 사이클 간 편차")
    print("  B: 아래로 크게 벌어질수록 강한 신호. L2 0.5~1.0 이 적정")
    print("  C: 선이 겹치는 향끼리는 구분 불가. 이게 분류 성능을 결정")
    print("  D: 선이 평평해야 그 블록이 '그 향의 대표값'. 계속 변하면 과도구간")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1:])
