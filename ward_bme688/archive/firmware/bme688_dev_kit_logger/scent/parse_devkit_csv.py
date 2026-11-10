#!/usr/bin/env python3
"""
parse_devkit_csv.py  (v2)
bme688_dev_kit_logger.ino 가 SD에 남기는 LOG_xxxx.csv 를
"히터 사이클 1회 = 특징벡터 1개" 형태의 학습용 CSV로 변환한다.

출력 포맷은 parse_bmerawdata.py 와 동일 → train_scent.py / infer_jetson.py 그대로 사용 가능.

=== 품질 필터 순서가 핵심 ===
parallel 모드는 샘플링 주기를 일정하게 유지하려고 "더미 변환" 슬롯을 규칙적으로
끼워 넣는다 (datasheet 5.3.6.5). 따라서 순서가 중요하다.

  ① Gas Valid == 0 인 '행' 제거      ← 측정이 아니라 자리채움. 사이클 폐기 사유 아님
  ② 남은 진짜 측정으로 사이클(Gas Index 0~9) 조립
  ③ Heater Stable == 0 이면 사이클 폐기  ← --ignore-heat-stab 로 끌 수 있음

③번은 실패가 '특정 스텝에 집중'되면 프로파일 구조상 필연(냉각 스텝 등)이라
폐기가 오히려 손해다. 그래서 폐기율이 높으면 스텝별 진단을 자동 출력한다.

Gas Valid / Heater Stable 은 0/1이 아니라 마스크 값(0 또는 32 / 0 또는 16).
"0이 아니면 참"으로 판정한다.

사용법:
  python3 parse_devkit_csv.py data/Woody woody.csv --label Woody
  python3 parse_devkit_csv.py data/Woody woody.csv --label Woody --ignore-heat-stab
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

# Windows 콘솔(cp949)에서 '—' 같은 문자 출력 시 UnicodeEncodeError 방지
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

import numpy as np
import pandas as pd

N_STEPS = 10
TEMP_PROFILE = [320, 100, 100, 100, 200, 200, 200, 320, 320, 320]

COLUMN_ALIASES = {
    "time": ["TimeStamp(ms)", "TimeStamp", "timestamp"],
    "sensor_index": ["Sensor Index", "sensor_index"],
    "temp": ["Temperature(deg C)", "Temperature", "temperature"],
    "hum": ["Humidity(%)", "Humidity", "humidity"],
    "gas": ["Gas Resistance(ohm)", "Gas Resistance", "gas_resistance"],
    "heater_step": ["Gas Index", "gas_index"],
    "gas_valid": ["Gas Valid", "gas_valid"],
    "heat_stab": ["Heater Stable", "Heater Stability", "heat_stab"],
}


def resolve_columns(df):
    cols = {c.strip(): c for c in df.columns}
    resolved = {k: next((cols[a] for a in al if a in cols), None)
                for k, al in COLUMN_ALIASES.items()}
    missing = [k for k in ("sensor_index", "gas", "heater_step") if resolved[k] is None]
    if missing:
        raise KeyError(f"필수 컬럼 없음: {missing} / 파일의 컬럼: {list(df.columns)}")
    return resolved


def diagnose_heat_stab(diag: pd.DataFrame) -> str:
    """히터 불안정이 '어느 스텝에서' 나는지 분해.
    특정 스텝 집중 → 프로파일 구조상 필연 (폐기하면 손해)
    전 스텝 광범위 → 전원/하드웨어 문제 (원인 해결 필요)"""
    out = ["\n=== 히터 안정성 진단 (스텝별 실패율) ==="]
    g = diag.groupby("heater_step")["heat_stab"]
    bad_steps = []
    for step, ser in g:
        n = len(ser)
        bad = int((~ser).sum())
        rate = bad / n * 100 if n else 0.0
        temp = TEMP_PROFILE[step] if 0 <= step < N_STEPS else "?"
        out.append(f"  step {step} ({temp}C): {rate:5.1f}% 실패 ({bad}/{n}) "
                   + "#" * int(rate / 5))
        if n and rate > 50:
            bad_steps.append(int(step))
    out.append("")
    # 온도 설정값별로 묶어서 본다 (냉각 문제는 '저온 스텝일수록 실패'로 나타남)
    by_temp = {}
    for step, ser in g:
        if not (0 <= step < N_STEPS) or len(ser) == 0:
            continue
        t = TEMP_PROFILE[int(step)]
        by_temp.setdefault(t, []).append((~ser).mean() * 100)
    summary = {t: sum(v) / len(v) for t, v in by_temp.items()}
    if summary:
        out.append("온도대별 평균 실패율: "
                   + ",  ".join(f"{t}C {r:.0f}%" for t, r in sorted(summary.items())))

    hi = summary.get(320)
    lo_temps = [t for t in summary if t < 320]
    lo = max((summary[t] for t in lo_temps), default=None)

    if hi is not None and lo is not None and hi < 5 and lo > 15:
        out.append("")
        out.append("판정: 고온(320C)은 완벽, 저온으로 갈수록 실패 → 냉각 한계 (구조적)")
        out.append("      히터는 가열만 되고 능동 냉각이 안 되므로 320C 직후 저온 도달이 어렵다.")
        out.append("      전원 문제였다면 전류를 가장 많이 쓰는 320C가 먼저 실패했을 것 → 전원은 정상.")
        out.append("      → --ignore-heat-stab 로 진행할 것. 매 사이클 같은 방식으로 식으므로")
        out.append("        해당 스텝의 저항값은 재현성이 있고 분류에 그대로 쓸 수 있다.")
    elif hi is not None and hi > 15:
        out.append("")
        out.append("판정: 고온(320C) 스텝이 실패 → 전원/하드웨어 의심")
        out.append("      → PC USB 대신 전류 여유 있는 어댑터/보조배터리로 재측정 권장")
    elif bad_steps and len(bad_steps) <= 4:
        out.append("")
        out.append(f"판정: 실패가 스텝 {bad_steps} 에 집중 → 프로파일 구조상 필연 가능성")
        out.append("      → --ignore-heat-stab 로 다시 돌려 채택률을 확인할 것")
    else:
        out.append("")
        out.append("판정: 뚜렷한 패턴 없음. --ignore-heat-stab 와 비교해볼 것")
    return "\n".join(out)


def parse_file(path, forced_label, quality_filter=True,
               ignore_heat_stab=False, diag_frames=None):
    stats = Counter()
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    c = resolve_columns(df)

    has_valid = c["gas_valid"] is not None
    has_stab = c["heat_stab"] is not None

    work = pd.DataFrame({
        "sensor_index": pd.to_numeric(df[c["sensor_index"]], errors="coerce"),
        "heater_step": pd.to_numeric(df[c["heater_step"]], errors="coerce"),
        "gas": pd.to_numeric(df[c["gas"]], errors="coerce"),
        "temp": pd.to_numeric(df[c["temp"]], errors="coerce") if c["temp"] else np.nan,
        "hum": pd.to_numeric(df[c["hum"]], errors="coerce") if c["hum"] else np.nan,
    })
    work["gas_valid"] = (pd.to_numeric(df[c["gas_valid"]], errors="coerce").fillna(1) != 0) \
        if has_valid else True
    work["heat_stab"] = (pd.to_numeric(df[c["heat_stab"]], errors="coerce").fillna(1) != 0) \
        if has_stab else True

    stats["rows_read"] = len(work)
    work = work.dropna(subset=["sensor_index", "heater_step", "gas"])
    stats["rows_malformed"] = stats["rows_read"] - len(work)

    # ---- ① 더미 행 제거 (사이클 조립 전에!) ----
    if quality_filter and has_valid:
        before = len(work)
        work = work[work["gas_valid"]]
        stats["rows_dummy_dropped"] = before - len(work)

    work["sensor_index"] = work["sensor_index"].astype(int)
    work["heater_step"] = work["heater_step"].astype(int)

    if diag_frames is not None and has_stab:
        diag_frames.append(work[["heater_step", "heat_stab"]].copy())

    # ---- ② 사이클 조립 ----
    features = []
    for s_idx, g in work.groupby("sensor_index", sort=False):
        g = g.reset_index(drop=True)
        cycle_id = (g["heater_step"] < g["heater_step"].shift(fill_value=-1)).cumsum()

        for _, cyc in g.groupby(cycle_id):
            stats["cycles_seen"] += 1

            if sorted(cyc["heater_step"].tolist()) != list(range(N_STEPS)):
                stats["drop_incomplete"] += 1
                continue
            if (cyc["gas"] <= 0).any():
                stats["drop_nonpositive_gas"] += 1
                continue

            # ---- ③ 히터 불안정 (정책에 따라) ----
            if quality_filter and has_stab and (~cyc["heat_stab"]).any():
                if ignore_heat_stab:
                    stats["kept_despite_unstable"] += 1
                else:
                    stats["drop_heater_unstable"] += 1
                    continue

            cyc = cyc.sort_values("heater_step")
            row = {f"log_gas_{i}": v for i, v in
                   enumerate(np.log10(cyc["gas"].to_numpy()))}
            row["temp"] = cyc["temp"].mean()
            row["hum"] = cyc["hum"].mean()
            row["sensor_index"] = s_idx
            row["label"] = forced_label
            row["source_file"] = path.name      # = 세션 ID (GroupKFold 기준)
            features.append(row)
            stats["cycles_kept"] += 1

    if quality_filter and not (has_valid and has_stab):
        stats["quality_cols_missing"] = 1
    return pd.DataFrame(features), stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_dir", type=Path)
    ap.add_argument("output_csv", type=Path)
    ap.add_argument("--label", required=True)
    ap.add_argument("--no-quality-filter", action="store_true")
    ap.add_argument("--ignore-heat-stab", action="store_true",
                    help="히터 불안정 사이클도 채택 (실패가 특정 스텝에 구조적으로 몰릴 때)")
    args = ap.parse_args()

    files = sorted(args.input_dir.rglob("*.csv"))
    if not files:
        sys.exit(f"{args.input_dir} 아래에 .csv 파일이 없음")

    frames, total, diag_frames = [], Counter(), []
    for p in files:
        try:
            f, st = parse_file(p, args.label, not args.no_quality_filter,
                               args.ignore_heat_stab, diag_frames)
            total.update(st)
            print(f"[OK] {p.name}: 사이클 {len(f)}개")
            if not f.empty:
                frames.append(f)
        except Exception as e:
            print(f"[SKIP] {p.name}: {e}")

    print("\n=== 품질 리포트 ===")
    print(f"읽은 행           : {total.get('rows_read', 0)}")
    if total.get("rows_dummy_dropped"):
        d = total["rows_dummy_dropped"]
        pct = d / max(total.get("rows_read", 1), 1) * 100
        print(f"  더미 행 제거    : {d} ({pct:.0f}% — 자리채움 슬롯, 정상)")
    if total.get("rows_malformed"):
        print(f"  깨진 행 제거    : {total['rows_malformed']}")

    seen, kept = total.get("cycles_seen", 0), total.get("cycles_kept", 0)
    print(f"사이클 전체       : {seen}")
    if seen:
        print(f"  채택           : {kept} ({kept / seen * 100:.1f}%)")
    if total.get("kept_despite_unstable"):
        print(f"    └ 불안정 포함 : {total['kept_despite_unstable']} (--ignore-heat-stab)")
    for k, msg in [("drop_incomplete", "10스텝 미완성"),
                   ("drop_nonpositive_gas", "가스저항 0 이하"),
                   ("drop_heater_unstable", "히터 온도 미도달")]:
        if total.get(k):
            print(f"  폐기 - {msg:<16}: {total[k]}")
    if total.get("quality_cols_missing"):
        print("주의: 품질 컬럼 일부가 없어 해당 필터를 생략함")

    # 폐기가 채택보다 많으면 스텝별 진단 자동 출력
    if diag_frames and total.get("drop_heater_unstable", 0) > kept:
        print(diagnose_heat_stab(pd.concat(diag_frames, ignore_index=True)))

    if not frames:
        sys.exit("\n살아남은 사이클이 없음 — 위 진단을 참고할 것")

    out = pd.concat(frames, ignore_index=True)
    out.to_csv(args.output_csv, index=False)
    print(f"\n세션(파일) 수     : {out['source_file'].nunique()}")
    print(f"총 {len(out)}개 사이클 → {args.output_csv}")
    if out["source_file"].nunique() < 2:
        print("경고: 세션이 1개뿐이면 GroupKFold 검증 불가. 세션 3개 이상 권장")


if __name__ == "__main__":
    main()
