#!/usr/bin/env python3
"""
parse_devkit_multi.py
bme688_multiprofile_logger.ino 로 찍은 CSV 를 40차원 특징으로 변환한다.

=== 왜 40차원인가 ===
8개 센서에 4가지 히터 프로파일을 2개씩 배정했다.
서로 다른 온도 프로그램은 서로 다른 화학 정보를 끌어내므로,
네 프로파일의 10스텝 지문을 이어 붙이면 40차원이 된다.

  센서 0,1 → HP354  표준   (10.8초)
  센서 2,3 → HP501  완만   (26.9초)
  센서 4,5 → HP411  급전이 (24.6초)
  센서 6,7 → HP301  장시간 (18.3초)

=== 두 개의 가상 배열 ===
센서를 짝수/홀수로 나눠 두 벌의 4프로파일 세트를 만든다.

  배열 A = 센서 {0, 2, 4, 6}
  배열 B = 센서 {1, 3, 5, 7}

각 배열이 독립적으로 40차원 샘플을 만들므로 표본이 두 배가 되고,
센서 개체차도 학습 데이터 안의 변동으로 자연스럽게 포함된다.

=== 시간 정렬 ===
프로파일마다 사이클 길이가 다르다(10.8 ~ 26.9초).
가장 빠른 HP354 의 사이클이 끝날 때마다 한 샘플을 만들고,
나머지 세 프로파일은 '그 시점 이전에 끝난 가장 최근 사이클' 을 가져다 쓴다.
  - 너무 오래된 사이클(MAX_AGE_S 초과)은 쓰지 않고 그 샘플을 버린다
  - 결과적으로 약 11초마다 한 샘플이 나온다

출력 컬럼은 log_gas_0 ~ log_gas_39 (프로파일 순서대로 10개씩) + 메타.
기존 baseline_correct.py / train_scent.py 가 그대로 처리할 수 있다.

=== 사용법 ===
  python parse_devkit_multi.py data7\\Air Air7.csv --label Air --ignore-heat-stab
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

N_STEPS = 10
N_PROFILE = 4
PROF_NAME = ["HP354", "HP501", "HP411", "HP301"]
PROF_CYCLE_S = [10.8, 26.9, 24.6, 18.3]

# 펌웨어의 profileOf() 와 반드시 같아야 한다
SENSOR_PROFILE = {0: 0, 1: 0, 2: 1, 3: 1, 4: 2, 5: 2, 6: 3, 7: 3}
ARRAYS = {"A": [0, 2, 4, 6], "B": [1, 3, 5, 7]}

ANCHOR_PROFILE = 0          # HP354 (가장 빠름) 기준으로 샘플 생성
MAX_AGE_S = 40.0            # 다른 프로파일 사이클이 이보다 오래되면 버림

COLUMN_ALIASES = {
    "time": ["TimeStamp(ms)", "TimeStamp", "timestamp"],
    "sensor_index": ["Sensor Index", "sensor_index"],
    "gas": ["Gas Resistance(ohm)", "Gas Resistance", "gas_resistance"],
    "heater_step": ["Gas Index", "gas_index"],
    "temp": ["Temperature(deg C)", "Temperature", "temperature"],
    "hum": ["Humidity(%)", "Humidity", "humidity"],
    "gas_valid": ["Gas Valid", "gas_valid"],
    "heat_stab": ["Heater Stable", "Heater Stability", "heat_stab"],
}


def resolve(df):
    cols = {c.strip(): c for c in df.columns}
    r = {k: next((cols[a] for a in al if a in cols), None)
         for k, al in COLUMN_ALIASES.items()}
    miss = [k for k in ("time", "sensor_index", "gas", "heater_step") if r[k] is None]
    if miss:
        raise KeyError(f"필수 컬럼 없음: {miss} / {list(df.columns)}")
    return r


def load_cycles(path, quality_filter=True, ignore_heat_stab=False):
    """CSV → 센서별 사이클 목록 {sensor: [(끝시각초, 10차원 log저항, temp, hum), ...]}"""
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    c = resolve(df)
    stats = Counter()

    w = pd.DataFrame({
        "t": pd.to_numeric(df[c["time"]], errors="coerce"),
        "s": pd.to_numeric(df[c["sensor_index"]], errors="coerce"),
        "step": pd.to_numeric(df[c["heater_step"]], errors="coerce"),
        "gas": pd.to_numeric(df[c["gas"]], errors="coerce"),
        "temp": pd.to_numeric(df[c["temp"]], errors="coerce") if c["temp"] else np.nan,
        "hum": pd.to_numeric(df[c["hum"]], errors="coerce") if c["hum"] else np.nan,
    })
    has_valid = c["gas_valid"] is not None
    has_stab = c["heat_stab"] is not None
    w["valid"] = (pd.to_numeric(df[c["gas_valid"]], errors="coerce").fillna(1) != 0) \
        if has_valid else True
    w["stab"] = (pd.to_numeric(df[c["heat_stab"]], errors="coerce").fillna(1) != 0) \
        if has_stab else True

    stats["rows_read"] = len(w)
    w = w.dropna(subset=["t", "s", "step", "gas"])
    w = w[w["gas"] > 0]

    # ① 더미 슬롯 제거 (사이클 조립 전)
    if quality_filter and has_valid:
        n0 = len(w)
        w = w[w["valid"]]
        stats["rows_dummy"] = n0 - len(w)

    w["s"] = w["s"].astype(int)
    w["step"] = w["step"].astype(int)
    t0 = w["t"].min()

    out = {}
    for s, g in w.groupby("s", sort=False):
        g = g.sort_values("t").reset_index(drop=True)
        cid = (g["step"] < g["step"].shift(fill_value=-1)).cumsum()
        lst = []
        for _, cyc in g.groupby(cid):
            stats["cycles_seen"] += 1
            if sorted(cyc["step"].tolist()) != list(range(N_STEPS)):
                stats["drop_incomplete"] += 1
                continue
            # ③ 히터 불안정은 사이클 단위
            if quality_filter and has_stab and not ignore_heat_stab \
                    and (~cyc["stab"]).any():
                stats["drop_unstable"] += 1
                continue
            cyc = cyc.sort_values("step")
            lst.append(((cyc["t"].max() - t0) / 1000.0,
                        np.log10(cyc["gas"].to_numpy()),
                        float(cyc["temp"].mean()), float(cyc["hum"].mean())))
            stats["cycles_kept"] += 1
        out[s] = lst
    return out, stats


def build_samples(cycles, label, fname):
    """센서별 사이클 → 배열 A/B 각각의 40차원 샘플"""
    rows = []
    dropped = Counter()

    for arr_name, sensors in ARRAYS.items():
        # 이 배열의 센서가 다 있는지
        if any(s not in cycles or not cycles[s] for s in sensors):
            dropped["array_missing"] += 1
            continue

        # 프로파일 → 해당 센서
        by_prof = {SENSOR_PROFILE[s]: cycles[s] for s in sensors}
        if len(by_prof) != N_PROFILE:
            dropped["profile_missing"] += 1
            continue

        anchor = by_prof[ANCHOR_PROFILE]
        others = {p: v for p, v in by_prof.items() if p != ANCHOR_PROFILE}
        # 각 프로파일의 시각 배열 (이진 탐색용)
        times = {p: np.array([x[0] for x in v]) for p, v in others.items()}

        for t_end, vec, tp, hm in anchor:
            feats = [vec]
            ages = []
            ok = True
            for p in range(N_PROFILE):
                if p == ANCHOR_PROFILE:
                    continue
                idx = np.searchsorted(times[p], t_end, side="right") - 1
                if idx < 0:
                    ok = False               # 아직 한 사이클도 안 끝남
                    dropped["no_prior_cycle"] += 1
                    break
                age = t_end - times[p][idx]
                if age > MAX_AGE_S:
                    ok = False               # 너무 오래된 값
                    dropped["stale"] += 1
                    break
                ages.append(age)
                feats.append(others[p][idx][1])
            if not ok:
                continue

            v40 = np.concatenate(feats)
            r = {f"log_gas_{i}": v40[i] for i in range(N_PROFILE * N_STEPS)}
            r["temp"] = tp
            r["hum"] = hm
            r["sensor_index"] = arr_name          # A / B (가상 배열 식별자)
            r["max_age_s"] = round(max(ages), 1)  # 정렬 품질 확인용
            r["label"] = label
            r["source_file"] = fname
            rows.append(r)

    return pd.DataFrame(rows), dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_dir", type=Path)
    ap.add_argument("output_csv", type=Path)
    ap.add_argument("--label", required=True)
    ap.add_argument("--no-quality-filter", action="store_true")
    ap.add_argument("--ignore-heat-stab", action="store_true")
    args = ap.parse_args()

    files = sorted(args.input_dir.rglob("*.csv"))
    if not files:
        sys.exit(f"{args.input_dir} 아래에 CSV 없음")

    print("프로파일 배정:")
    for p in range(N_PROFILE):
        ss = [s for s, q in SENSOR_PROFILE.items() if q == p]
        print(f"  {PROF_NAME[p]:<6} 센서 {ss}  사이클 {PROF_CYCLE_S[p]}초")
    print(f"기준 프로파일: {PROF_NAME[ANCHOR_PROFILE]} "
          f"(이 사이클마다 샘플 1개)\n")

    frames, total, dtotal = [], Counter(), Counter()
    for f in files:
        try:
            cyc, st = load_cycles(f, not args.no_quality_filter,
                                  args.ignore_heat_stab)
            total.update(st)
            df, dr = build_samples(cyc, args.label, f.name)
            dtotal.update(dr)
            per = {PROF_NAME[SENSOR_PROFILE[s]]: len(v) for s, v in sorted(cyc.items())}
            print(f"[OK] {f.name}: 샘플 {len(df)}개  "
                  + " ".join(f"{k}{v}" for k, v in per.items()))
            if not df.empty:
                frames.append(df)
        except Exception as e:
            print(f"[FAIL] {f.name}: {e}")

    print("\n=== 품질 리포트 ===")
    print(f"읽은 행       : {total.get('rows_read', 0)}")
    if total.get("rows_dummy"):
        print(f"  더미 제거   : {total['rows_dummy']}")
    seen, kept = total.get("cycles_seen", 0), total.get("cycles_kept", 0)
    print(f"사이클 전체   : {seen}")
    if seen:
        print(f"  채택        : {kept} ({kept / seen * 100:.1f}%)")
    for k, msg in [("drop_incomplete", "10스텝 미완성"),
                   ("drop_unstable", "히터 불안정")]:
        if total.get(k):
            print(f"  폐기 - {msg:<14}: {total[k]}")
    for k, msg in [("no_prior_cycle", "다른 프로파일 미완성(초반)"),
                   ("stale", f"다른 프로파일이 {MAX_AGE_S}초 초과로 낡음"),
                   ("array_missing", "배열 센서 누락")]:
        if dtotal.get(k):
            print(f"  샘플 제외 - {msg}: {dtotal[k]}")

    if not frames:
        sys.exit("\n샘플이 없습니다. 블록이 너무 짧았을 수 있습니다(5분 이상 권장)")

    out = pd.concat(frames, ignore_index=True)
    out.to_csv(args.output_csv, index=False)
    print(f"\n정렬 지연 중앙값: {out['max_age_s'].median():.1f}초 "
          f"(최대 {out['max_age_s'].max():.1f}초)")
    print(f"세션(파일) 수 : {out['source_file'].nunique()}")
    print(f"총 {len(out)}개 샘플 x {N_PROFILE * N_STEPS}차원 → {args.output_csv}")


if __name__ == "__main__":
    main()
