#!/usr/bin/env python3
"""
baseline_correct.py
세션 드리프트를 Air(무향) 기준선 차감으로 상쇄한다.

=== 원리 ===
MOX 가스센서는 시간이 지나면 지문이 통째로 흘러간다(드리프트).
그런데 이 드리프트는 그 시점에 무슨 향이 있든 상관없이 똑같이 작용한다.
따라서 "같은 시간대의 Air 지문"을 빼주면 드리프트만 소거되고 향 성분이 남는다.

    향(1라운드) − Air(1라운드)  ≈  향(2라운드) − Air(2라운드)
                                    ↑ 드리프트 소거

센서 개체차도 함께 사라지므로 센서별로 따로 계산한다.

=== 짝짓기 규칙 ===
source_file 하나(= 라벨 조각 하나)마다 기준선 Air 를 하나 고른다.

  [시리얼 녹화]  rec_날짜_시각_태그__segN.csv  (label_tool.py 가 만든 조각)
    1순위: 같은 녹화 안의 다른 Air 조각 중 시간(t_ms)상 가장 가까운 것
    2순위: 같은 녹화에 Air 가 없으면, 녹화 시작 시각이 가장 가까운 다른 녹화의 Air
           (그 녹화가 앞이면 마지막 Air, 뒤면 첫 Air)

Air 조각은 '자기 자신을 뺀' 결과가 정확히 0이 되어 "Air = 원점" 순환논리로
성능이 부풀려지므로, 자신을 제외한 가장 가까운 다른 Air 를 기준선으로 쓴다.

짝을 못 찾은 행(기준선 Air 없음 / 해당 센서의 Air 없음)은 보정 안 된 값이
섞이지 않도록 기본적으로 출력에서 뺀다. (keep_unpaired=True 로 원본 유지)

open_set.py(학습·평가)가 apply_baseline() 을 부른다. 출력에는 baseline_file 컬럼(어떤 Air 를 뺐는지)이 추가된다.
"""

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

import numpy as np
import pandas as pd

from session_id import session_info


def detect_gas_cols(df):
    """log_gas_* 컬럼 개수를 자동 감지한다. 히터 설정 하나면 10개, 4개면 40개."""
    n = 0
    while f"log_gas_{n}" in df.columns:
        n += 1
    if n == 0:
        raise KeyError("log_gas_* 컬럼이 없습니다")
    return [f"log_gas_{i}" for i in range(n)]


def build_file_table(df, air_label):
    """source_file 별 메타데이터 표."""
    has_t = "t_ms" in df.columns and df["t_ms"].notna().any()
    rows = []
    for f, g in df.groupby("source_file", sort=False):
        labels = g["label"].unique()
        if len(labels) > 1:
            print(f"  경고: {f} 안에 라벨이 여러 개 {list(labels)} → 첫 라벨로 처리")
        info = session_info(f)
        rows.append({
            "file": f, "label": labels[0], "is_air": labels[0] == air_label,
            "kind": info["kind"], "recording": info["recording"],
            "start": info["start"], "seq": info["seq"], "seg": info["seg"],
            "t_mid": float(g["t_ms"].median()) if has_t else np.nan,
            "t_min": float(g["t_ms"].min()) if has_t else np.nan,
            "t_max": float(g["t_ms"].max()) if has_t else np.nan,
        })
    return pd.DataFrame(rows), has_t


def _order_in_rec(row):
    """녹화 안에서의 시간 위치. t_ms 가 없으면 조각 번호로 대신한다(라벨링 순서라 부정확)."""
    if not np.isnan(row["t_mid"]):
        return row["t_mid"]
    return float(row["seg"]) if not pd.isna(row["seg"]) else 0.0


def choose_air(target, files):
    """target 행(파일 메타)에 대한 기준선 Air 파일과 설명을 돌려준다. 없으면 (None, 사유)."""
    pool = files[files["is_air"] & (files["file"] != target["file"])]
    if pool.empty:
        return None, "다른 Air 없음"

    if target["kind"] == "rec":
        same = pool[(pool["kind"] == "rec") & (pool["recording"] == target["recording"])]
        if not same.empty:
            t0 = _order_in_rec(target)
            d = same.apply(lambda r: abs(_order_in_rec(r) - t0), axis=1)
            best = same.loc[d.sort_values(kind="stable").index[0]]
            if np.isnan(target["t_mid"]):
                return best["file"], "같은 녹화 (시각 정보 없어 조각 번호로 추정)"
            return best["file"], f"같은 녹화, {abs(best['t_mid'] - target['t_mid']) / 1000:.0f}초 차이"

        other = pool[(pool["kind"] == "rec") & (pool["recording"] != target["recording"])
                     & pool["start"].notna()]
        if other.empty or pd.isna(target["start"]):
            return None, "같은 녹화에 Air 없음 + 다른 녹화 Air 없음"
        gap = other["start"].apply(lambda s: abs((s - target["start"]).total_seconds()))
        rec = other.loc[gap.sort_values(kind="stable").index[0], "recording"]
        cand = other[other["recording"] == rec]
        earlier = cand["start"].iloc[0] < target["start"]
        # 앞 녹화면 그 녹화의 마지막 Air, 뒤 녹화면 첫 Air (시간상 가장 가까운 쪽)
        pos = cand.apply(_order_in_rec, axis=1)
        best = cand.loc[pos.idxmax() if earlier else pos.idxmin()]
        gmin = abs((best["start"] - target["start"]).total_seconds()) / 60
        same_day = best["start"].date() == target["start"].date()
        note = f"다른 녹화 {best['recording']} (시작 {gmin:.0f}분 차이)"
        if not same_day:
            note += "  ⚠ 다른 날"
        return best["file"], note

    # 형식을 모르는 파일: 같은 형식 Air 중 첫 번째 (드묾)
    cand = pool[pool["kind"] == "other"]
    if cand.empty:
        return None, "형식 불명 + 같은 형식 Air 없음"
    return cand["file"].iloc[0], "형식 불명 — 임의 선택"


def apply_baseline(df, air_label="Air", keep_unpaired=False, log=print):
    """parse_devkit_csv 출력 df 에 인접 Air 기준선 차감을 적용해 (보정된 df, 제외 행 수) 를 돌려준다.
    main() 과 open_set.py 가 함께 쓴다. Air 가 하나도 없으면 ValueError."""
    GAS = detect_gas_cols(df)
    files, has_t = build_file_table(df, air_label)
    n_air = int(files["is_air"].sum())
    if n_air == 0:
        raise ValueError(f"'{air_label}' 라벨 파일이 없습니다. 기준선을 만들 수 없음.")
    log(f"파일 {len(files)}개 (그중 {air_label} {n_air}개), 녹화 {files['recording'].nunique()}개")
    if not has_t and (files["kind"] == "rec").any():
        log("주의: t_ms 컬럼이 없음 (구버전 parse_devkit_csv 출력) → 같은 녹화 안의 순서를 조각 번호로 추정합니다."
            "\n      정확한 짝짓기를 원하면 parse_devkit_csv.py 로 다시 파싱하세요.")

    # Air 파일별 · 센서별 평균 지문
    air = df[df["label"] == air_label]
    base = air.groupby(["source_file", "sensor_index"])[GAS].mean()

    log("\n파일 → 기준선 짝짓기")
    out_parts, dropped = [], 0
    order = files.assign(_o=files.apply(
        lambda r: (0.0 if pd.isna(r["start"]) else r["start"].timestamp(),
                   0 if pd.isna(r["seq"]) else r["seq"],
                   _order_in_rec(r)), axis=1)).sort_values("_o")
    for _, t in order.iterrows():
        g = df[df["source_file"] == t["file"]]
        air_file, note = choose_air(t, files)
        tag = f"{t['file']} ({t['label']})"
        if air_file is None:
            log(f"  {tag:<55} → 기준선 없음: {note}"
                + ("  [원본 유지]" if keep_unpaired else "  [제외]"))
            if keep_unpaired:
                out_parts.append(g.assign(baseline_file=""))
            else:
                dropped += len(g)
            continue
        log(f"  {tag:<55} → {air_file}  ({note})")

        gg = g.copy()
        arr = gg[GAS].to_numpy(float).copy()
        keep = np.ones(len(gg), dtype=bool)
        sens = gg["sensor_index"].to_numpy()
        for s in np.unique(sens):
            key = (air_file, s)
            m = sens == s
            if key not in base.index:
                keep &= ~m                      # 이 센서의 Air 가 없음
                continue
            arr[m] -= base.loc[key].to_numpy(float)
        gg[GAS] = arr
        gg["baseline_file"] = air_file
        miss = int((~keep).sum())
        if miss:
            if keep_unpaired:
                log(f"      └ 센서 Air 없음 {miss}행 (원본 유지)")
            else:
                log(f"      └ 센서 Air 없음 {miss}행 제외")
                dropped += miss
                gg = gg[keep]
        out_parts.append(gg)

    out = pd.concat(out_parts, ignore_index=True) if out_parts else df.iloc[0:0].copy()
    return out, dropped
