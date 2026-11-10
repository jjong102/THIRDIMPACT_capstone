#!/usr/bin/env python3
"""
baseline_correct.py
세션 드리프트를 Air(무향) 기준선 차감으로 상쇄한다.

=== 원리 ===
MOX 가스센서는 시간이 지나면 지문이 통째로 흘러간다(드리프트).
그런데 이 드리프트는 그 시점에 무슨 향이 있든 상관없이 똑같이 작용한다.
따라서 "같은 시간대의 Air 지문"을 빼주면 드리프트만 소거되고 향 성분이 남는다.

    Woody(1라운드) − Air(1라운드)  ≈  Woody(2라운드) − Air(2라운드)
                                    ↑ 드리프트 소거

센서 개체차도 함께 사라지므로 센서별로 따로 계산한다.

=== 짝짓기 규칙 ===
파일명의 번호(LOG_0007.csv → 7)를 시간 순서로 보고,
각 세션마다 '번호가 가장 가까운 Air 세션'을 기준선으로 삼는다.

단, Air 세션은 '자기 자신을 뺀' 결과가 정확히 0이 되어버려
"Air = 원점" 이라는 순환논리로 Air 성능이 부풀려진다.
따라서 Air 세션은 '자신을 제외한 가장 가까운 다른 Air 세션'을 기준선으로 쓴다.
(Air 세션이 하나뿐이면 보정할 수 없으므로 경고 후 원본 유지)

=== 사용법 ===
  python3 baseline_correct.py features.csv features_bc.csv
  python3 train_scent.py features_bc.csv --no-temp-hum

온습도는 세션 식별 지름길로 악용되는 것이 진단으로 확인되었으므로
학습 시 --no-temp-hum 을 함께 쓸 것.
"""

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

def detect_gas_cols(df):
    """log_gas_* 컬럼 개수를 자동 감지한다.
    단일 프로파일이면 10개, 4프로파일이면 40개."""
    n = 0
    while f"log_gas_{n}" in df.columns:
        n += 1
    if n == 0:
        raise KeyError("log_gas_* 컬럼이 없습니다")
    return [f"log_gas_{i}" for i in range(n)]


GAS = [f"log_gas_{i}" for i in range(10)]   # main() 에서 실제 개수로 교체된다


def session_number(name: str) -> int:
    """LOG_0007.csv → 7. 숫자가 없으면 등장 순서로 대체."""
    m = re.search(r"(\d+)", str(name))
    return int(m.group(1)) if m else -1


def main(src: str, dst: str, air_label: str = "Air"):
    global GAS
    df = pd.read_csv(src)
    GAS = detect_gas_cols(df)
    print(f"특징 차원: {len(GAS)}"
          + ("  (4프로파일)" if len(GAS) == 40 else "  (단일 프로파일)"))
    for c in ["source_file", "sensor_index", "label"] + GAS:
        if c not in df.columns:
            sys.exit(f"필수 컬럼 없음: {c}")

    df["_sess"] = df["source_file"].map(session_number)

    air_sessions = sorted(df.loc[df["label"] == air_label, "_sess"].unique())
    if not air_sessions:
        sys.exit(f"'{air_label}' 라벨 세션이 없습니다. 기준선을 만들 수 없음.")
    print(f"기준선으로 쓸 {air_label} 세션: {air_sessions}")

    # Air 세션별 · 센서별 평균 지문
    air = df[df["label"] == air_label]
    base = (air.groupby(["_sess", "sensor_index"])[GAS]
               .mean()
               .to_dict("index"))

    out_rows = []
    missing = 0
    print("\n세션 → 기준선 짝짓기")
    for sess, g in df.groupby("_sess", sort=True):
        lab = g["label"].iloc[0]

        # Air 세션은 자기 자신을 기준선으로 쓰지 않는다 (순환논리 방지)
        pool = [a for a in air_sessions if a != sess] if lab == air_label \
            else air_sessions
        if not pool:
            print(f"  LOG_{sess:04d} ({lab:<7}) → 기준선 없음, 보정 생략")
            out_rows.append(g.copy())
            continue

        near = min(pool, key=lambda a: (abs(a - sess), a))
        print(f"  LOG_{sess:04d} ({lab:<7}) → Air LOG_{near:04d}")

        arr = g[GAS].to_numpy(float).copy()
        sens = g["sensor_index"].to_numpy()
        for s in np.unique(sens):
            key = (near, s)
            if key not in base:
                missing += int((sens == s).sum())
                continue
            b = np.array([base[key][c] for c in GAS], dtype=float)
            arr[sens == s] -= b

        gg = g.copy()
        gg[GAS] = arr
        out_rows.append(gg)

    out = pd.concat(out_rows, ignore_index=True).drop(columns=["_sess"])
    out.to_csv(dst, index=False)

    if missing:
        print(f"\n주의: 기준선을 못 찾은 샘플 {missing}개 (해당 센서의 Air 데이터 없음)")

    print(f"\n→ {dst} 저장 ({len(out)}행)")
    print("\n다음 단계:")
    print(f"  python3 train_scent.py {dst} --no-temp-hum")
    print("\n기대: 기준선 차감이 통하면 '세션 분리' F1 이 0.0 에서 크게 오릅니다.")
    print("      여전히 낮으면 드리프트가 단순 오프셋이 아니라는 뜻 →")
    print("      3라운드를 짧은 블록 교대 방식으로 재수집해야 합니다.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2],
         sys.argv[3] if len(sys.argv) > 3 else "Air")
