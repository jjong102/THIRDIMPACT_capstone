#!/usr/bin/env python3
"""
heater_check.py
녹화 파일에서 히터가 목표 온도에 도달했는지(Heater Stable) 스텝별로 본다.
배터리 전원이 충분한지 확인할 때 쓴다.

판단
  - 실패가 '가장 뜨거운 스텝'(400℃)에서 나면 → 전원 부족. 가열은 전류를 가장 많이 쓴다.
  - 실패가 '식히는 스텝'(200℃)에만 몰리면 → 정상. 능동 냉각이 없어 생기는 구조적 한계.
  USB 로 찍은 녹화와 배터리로 찍은 녹화를 같이 넣으면 나란히 비교한다.

사용:
  python heater_check.py ..\\training\\recordings\\rec_..._battery.csv
  python heater_check.py USB녹화.csv 배터리녹화.csv
"""

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_devkit_csv import STEP_TEMPS    # noqa: E402


def check(path):
    df = pd.read_csv(path, on_bad_lines="skip")
    df.columns = [c.strip() for c in df.columns]
    for c in ["TimeStamp(ms)", "Sensor Index", "Gas Index", "Gas Valid", "Heater Stable"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    v = df[(df["Gas Valid"].fillna(0) != 0)].dropna(subset=["Gas Index", "Heater Stable"])
    if v.empty:
        print(f"{Path(path).name}: 유효 측정이 없습니다")
        return None
    temps = STEP_TEMPS
    rate = (v.assign(fail=v["Heater Stable"] == 0).groupby("Gas Index")["fail"].mean() * 100)
    mins = (v["TimeStamp(ms)"].max() - v["TimeStamp(ms)"].min()) / 60000
    return dict(name=Path(path).name, temps=temps, rate=rate, mins=mins)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    res = [r for r in (check(p) for p in sys.argv[1:]) if r]
    if not res:
        return
    temps = res[0]["temps"]
    print("스텝별 히터 실패율 (%) — 목표 온도에 못 미친 측정의 비율\n")
    head = "         " + "".join(f"{'st'+str(i):>7}" for i in range(10))
    print(head)
    if temps:
        print("  (℃)   " + "".join(f"{t:>7}" for t in temps))
    for r in res:
        row = "".join(f"{r['rate'].get(i, np.nan):7.1f}" for i in range(10))
        print(f"         {row}   ← {r['name']} ({r['mins']:.0f}분)")
    print()
    for r in res:
        t = r["temps"]
        hot = max(t)
        hot_rate = np.mean([r["rate"].get(i, 0) for i in range(10) if t[i] == hot])
        cold_rate = max(r["rate"].get(i, 0) for i in range(10) if t[i] < hot)
        if hot_rate > 5:
            verdict = f"⚠ 전원 부족 의심 — 가장 뜨거운 {hot}℃ 에서 {hot_rate:.1f}% 실패. 더 센 보조배터리(2A 이상)나 짧은 케이블로"
        else:
            verdict = f"✅ 전원 정상 — {hot}℃ 실패 {hot_rate:.1f}%. 저온 쪽 실패(최대 {cold_rate:.0f}%)는 냉각 한계라 정상"
        print(f"{r['name']}: {verdict}")


if __name__ == "__main__":
    main()
