#!/usr/bin/env python3
"""
fan_check.py
팬 + 케이스 환경에서 찍은 원본 LOG_xxxx.csv 를 요약해 텍스트로 출력한다.
(원본 파일을 통째로 주고받지 않고 이 출력만 붙여넣으면 판단할 수 있게)

확인하는 것 3가지:
  ① 아웃가싱 : Air 로그의 저항값이 시간에 따라 한쪽으로 흘러가는가
                → 흘러가면 케이스 VOC 가 아직 빠지는 중
  ② 신호 세기 : 향 로그가 Air 로그에서 얼마나 떨어져 있는가
                → 팬 이전보다 커졌으면 Citrus/Floral 분리 가능성 상승
  ③ 회복 시간 : 향을 치운 뒤 Air 기준선으로 몇 초 만에 돌아오는가
                → 블록 간격을 결정한다

사용법 (라벨=파일 형식, 순서 상관없음):
  python fan_check.py Air=LOG_0004.csv Citrus=LOG_0005.csv After=LOG_0007.csv

  라벨을 생략하면 파일명이 라벨이 된다:
  python fan_check.py LOG_0004.csv LOG_0005.csv LOG_0007.csv

  ※ 'Air' 라벨이 있으면 그것을 기준선으로, 'After' 라벨이 있으면
    회복 곡선 분석 대상으로 자동 인식한다.

출력이 길지 않으니 콘솔 내용을 그대로 복사해서 전달하면 된다.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

N_STEPS = 10
TEMP_PROFILE = [320, 100, 100, 100, 200, 200, 200, 320, 320, 320]

ALIASES = {
    "time": ["TimeStamp(ms)", "TimeStamp", "timestamp"],
    "sensor_index": ["Sensor Index", "sensor_index"],
    "gas": ["Gas Resistance(ohm)", "Gas Resistance", "gas_resistance"],
    "step": ["Gas Index", "gas_index"],
    "temp": ["Temperature(deg C)", "Temperature", "temperature"],
    "hum": ["Humidity(%)", "Humidity", "humidity"],
    "valid": ["Gas Valid", "gas_valid"],
    "stab": ["Heater Stable", "Heater Stability", "heat_stab"],
}


def load(path):
    """원본 CSV → 사이클 단위 (시각, 센서, 10스텝 log저항, 온습도)"""
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    col = {k: next((a for a in al if a in df.columns), None)
           for k, al in ALIASES.items()}
    for need in ("time", "sensor_index", "gas", "step"):
        if col[need] is None:
            raise KeyError(f"{path.name}: '{need}' 컬럼 없음 / {list(df.columns)}")

    w = pd.DataFrame({
        "t": pd.to_numeric(df[col["time"]], errors="coerce"),
        "s": pd.to_numeric(df[col["sensor_index"]], errors="coerce"),
        "step": pd.to_numeric(df[col["step"]], errors="coerce"),
        "gas": pd.to_numeric(df[col["gas"]], errors="coerce"),
        "temp": pd.to_numeric(df[col["temp"]], errors="coerce") if col["temp"] else np.nan,
        "hum": pd.to_numeric(df[col["hum"]], errors="coerce") if col["hum"] else np.nan,
    })
    w["valid"] = (pd.to_numeric(df[col["valid"]], errors="coerce").fillna(1) != 0) \
        if col["valid"] else True
    w["stab"] = (pd.to_numeric(df[col["stab"]], errors="coerce").fillna(1) != 0) \
        if col["stab"] else True

    n_raw = len(w)
    w = w.dropna(subset=["t", "s", "step", "gas"])
    w = w[w["valid"]]                       # 더미 슬롯 제거
    w = w[w["gas"] > 0]
    w["s"] = w["s"].astype(int)
    w["step"] = w["step"].astype(int)

    stab_rate = float(w["stab"].mean()) if len(w) else np.nan

    rows = []
    for s, g in w.groupby("s", sort=False):
        g = g.reset_index(drop=True)
        cid = (g["step"] < g["step"].shift(fill_value=-1)).cumsum()
        for _, cyc in g.groupby(cid):
            if sorted(cyc["step"].tolist()) != list(range(N_STEPS)):
                continue
            cyc = cyc.sort_values("step")
            r = {f"g{i}": v for i, v in enumerate(np.log10(cyc["gas"].to_numpy()))}
            r["t"] = cyc["t"].mean()
            r["s"] = s
            r["temp"] = cyc["temp"].mean()
            r["hum"] = cyc["hum"].mean()
            rows.append(r)

    c = pd.DataFrame(rows).sort_values("t").reset_index(drop=True)
    c["sec"] = (c["t"] - c["t"].min()) / 1000.0
    return c, n_raw, stab_rate


G = [f"g{i}" for i in range(N_STEPS)]


def profile(c):
    """센서 평균을 낸 10스텝 지문"""
    return c[G].mean().to_numpy()


def main(args):
    items = []
    for a in args:
        if "=" in a:
            lab, p = a.split("=", 1)
        else:
            p = a
            lab = Path(p).stem
        items.append((lab, Path(p)))

    data = {}
    print("=" * 62)
    print("FAN/CASE CHECK")
    print("=" * 62)
    for lab, p in items:
        c, n_raw, stab = load(p)
        data[lab] = c
        dur = c["sec"].max() if len(c) else 0
        print(f"\n[{lab}]  {p.name}")
        print(f"  원본행 {n_raw} | 사이클 {len(c)} | 길이 {dur:.0f}s "
              f"| 히터안정 {stab*100:.0f}%")
        if len(c):
            print(f"  온도 {c['temp'].mean():.1f}C  습도 {c['hum'].mean():.1f}%  "
                  f"평균log저항 {c[G].mean().mean():.3f}")

    # ---------- ① 아웃가싱: 앞/뒤 3분할 비교 ----------
    print("\n" + "-" * 62)
    print("① 아웃가싱 / 안정성  (앞 1/3 → 뒤 1/3 평균 log저항 변화)")
    print("-" * 62)
    for lab, c in data.items():
        if len(c) < 9:
            print(f"  {lab:<10} 사이클 부족")
            continue
        k = len(c) // 3
        a = c[G].iloc[:k].mean().mean()
        b = c[G].iloc[-k:].mean().mean()
        d = b - a
        # log10 차이를 배율로 환산
        ratio = 10 ** d
        flag = "안정" if abs(d) < 0.02 else ("상승" if d > 0 else "하강")
        print(f"  {lab:<10} {a:.3f} → {b:.3f}   Δ{d:+.3f} ({ratio:.2f}배)  {flag}")
    print("  ※ Air 로그에서 |Δ| > 0.05 면 아웃가싱/미안정 의심")

    # ---------- ② 신호 세기 ----------
    base_key = next((k for k in data if k.lower().startswith("air")
                     and not k.lower().startswith("after")), None)
    if base_key:
        base = profile(data[base_key])
        print("\n" + "-" * 62)
        print(f"② 신호 세기  (기준선 = {base_key})")
        print("-" * 62)
        print("  스텝별 차이 (향 - Air), 단위 log10:")
        print("        " + "".join(f"{TEMP_PROFILE[i]:>7}" for i in range(N_STEPS)))
        for lab, c in data.items():
            if lab == base_key or not len(c):
                continue
            d = profile(c) - base
            print(f"  {lab:<6}" + "".join(f"{v:>7.3f}" for v in d))
            print(f"  {'':6}   → 크기(L2) {np.linalg.norm(d):.3f}   "
                  f"최대 {np.abs(d).max():.3f} (step {int(np.abs(d).argmax())})")
        print("  ※ 크기 0.1 미만이면 신호가 약함. 0.3 이상이면 뚜렷")

    # ---------- ③ 회복 곡선 ----------
    after_key = next((k for k in data if "after" in k.lower()), None)
    scent_key = next((k for k in data
                      if k not in (base_key, after_key) and len(data[k])), None)
    if after_key and base_key:
        base = profile(data[base_key])
        c = data[after_key]
        print("\n" + "-" * 62)
        print(f"③ 회복 곡선  ({after_key} 가 {base_key} 로 돌아오는 과정)")
        print("-" * 62)
        ref = np.linalg.norm(profile(data[scent_key]) - base) if scent_key else None
        print("   구간(s)   기준선과의 거리" + ("   회복률" if ref else ""))
        bins = np.arange(0, c["sec"].max() + 15, 15)
        for i in range(len(bins) - 1):
            m = (c["sec"] >= bins[i]) & (c["sec"] < bins[i + 1])
            if m.sum() < 2:
                continue
            d = np.linalg.norm(c.loc[m, G].mean().to_numpy() - base)
            bar = "#" * int(min(d / 0.02, 40))
            line = f"  {bins[i]:>4.0f}-{bins[i+1]:<4.0f}  {d:.3f}"
            if ref and ref > 0:
                line += f"   {max(0, (1 - d / ref)) * 100:5.0f}%"
            print(line + "  " + bar)
        if ref:
            print(f"  ※ 향 노출 시 거리 = {ref:.3f} (회복률 0% 기준점)")
            print("     회복률 90% 도달 구간이 필요한 블록 간격")

    print("\n" + "=" * 62)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1:])
