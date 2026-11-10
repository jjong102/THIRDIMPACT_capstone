#!/usr/bin/env python3
"""
csv_to_bmerawdata.py
로거가 만든 LOG_xxxx_Label.csv 를 BME AI-Studio 가 읽는 .bmerawdata 로 변환한다.
같은 이름의 .bmelabelinfo 도 함께 만든다.

=== 왜 필요한가 ===
AI-Studio 는 최대 4클래스까지 학습할 수 있다(데이터시트 Table 5).
Air / Citrus / Floral / Woody 는 딱 4개이므로 벤더 툴체인으로도 학습이 가능하다.
자체 파이썬 파이프라인과 결과를 비교하면 교차 검증이 되고,
보고서에도 "자체 구현 vs 벤더 툴체인" 비교 항목이 생긴다.

=== 히터 프로파일 ===
기존 .bmerawdata 를 열어보니 heater_354 가
  [320,5] [100,2] [100,10] [100,30] [200,5] [200,5] [200,5] [320,5] [320,5] [320,5]
  timeBase 140
로, 지금 로거 펌웨어의 tempProf / mulProf / MEAS_DUR 과 정확히 일치한다.
따라서 8개 센서 전부를 heater_354 로 선언한다.
(원본 파일은 탐색 목적이라 센서마다 다른 프로파일을 썼지만, 우리 펌웨어는 전부 동일하다)

=== 사용법 ===
  # 폴더 하나를 통째로 (파일명 끝의 라벨을 자동 인식)
  python csv_to_bmerawdata.py data6 --out aistudio

  # 라벨을 직접 지정
  python csv_to_bmerawdata.py LOG_0036_Citrus.csv --label Citrus --out aistudio

  # 클래스 번호를 고정하고 싶을 때 (기본: Air=1 Citrus=2 Floral=3 Woody=4)
  python csv_to_bmerawdata.py data6 --out aistudio --tags Air=1,Citrus=2,Floral=3,Woody=4

만들어진 폴더를 AI-Studio 에서 Import 하면 된다.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import pandas as pd

# 기존 파일에서 읽어낸 이 보드의 값들 (그대로 써야 AI-Studio 가 같은 보드로 인식한다)
BOARD_ID = "88572139E920"
FIRMWARE = "2.1.5"
APP_VERSION = "3.2.0"
SENSOR_IDS = {0: 90596175, 1: 90596431, 2: 90592589, 3: 90595661,
              4: 90582093, 5: 90607181, 6: 90610513, 7: 90601040}

# 지금 로거 펌웨어의 히터 프로파일 (= 원본의 heater_354)
HEATER_ID = "heater_354"
HEATER_VECTORS = [[320, 5], [100, 2], [100, 10], [100, 30], [200, 5],
                  [200, 5], [200, 5], [320, 5], [320, 5], [320, 5]]
TIME_BASE = 140

DEFAULT_TAGS = {"Air": 1, "Citrus": 2, "Floral": 3, "Woody": 4}

DATA_COLUMNS = [
    ("sensor_index", "Sensor Index", "", "integer"),
    ("sensor_id", "Sensor ID", "", "integer"),
    ("timestamp_since_poweron", "Time Since PowerOn", "Milliseconds", "integer"),
    ("real_time_clock", "Real time clock",
     "Unix Timestamp: seconds since Jan 01 1970. (UTC); 0 = missing", "integer"),
    ("temperature", "Temperature", "DegreesCelcius", "float"),
    ("pressure", "Pressure", "Hectopascals", "float"),
    ("relative_humidity", "Relative Humidity", "Percent", "float"),
    ("resistance_gassensor", "Resistance Gassensor", "Ohms", "float"),
    ("heater_profile_step_index", "Heater Profile Step Index", "", "integer"),
    ("scanning_enabled", "Scanning Mode Enabled", "", "boolean"),
    ("scanning_cycle_index", "Scanning Cycle Index", "", "integer"),
    ("label_tag", "Label Tag", "", "integer"),
    ("error_code", "Error Code", "", "integer"),
]

CSV_ALIASES = {
    "time": ["TimeStamp(ms)", "TimeStamp", "timestamp"],
    "sensor": ["Sensor Index", "sensor_index"],
    "temp": ["Temperature(deg C)", "Temperature", "temperature"],
    "pres": ["Pressure(Pa)", "Pressure", "pressure"],
    "hum": ["Humidity(%)", "Humidity", "humidity"],
    "gas": ["Gas Resistance(ohm)", "Gas Resistance", "gas_resistance"],
    "step": ["Gas Index", "gas_index"],
    "valid": ["Gas Valid", "gas_valid"],
}


def seed(n=16):
    """원본이 쓰던 16자 소문자+숫자 시드를 흉내낸다."""
    import random
    import string
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def label_from_name(path: Path):
    """LOG_0036_Citrus.csv → 'Citrus'"""
    m = re.match(r"LOG_\d+_(.+)", path.stem)
    return m.group(1) if m else None


def build_config():
    return {
        "configHeader": {
            "dateCreated_ISO": time.strftime("%Y-%m-%dT%H:%M:%S.000Z",
                                             time.gmtime()),
            "appVersion": APP_VERSION,
            "boardType": "board_8",
            "boardMode": "heater_profile_exploration",
            "boardLayout": "grouped",
        },
        "configBody": {
            "heaterProfiles": [{
                "id": HEATER_ID,
                "timeBase": TIME_BASE,
                "temperatureTimeVectors": HEATER_VECTORS,
            }],
            "dutyCycleProfiles": [{
                "id": "duty_1",
                "numberScanningCycles": 1,
                "numberSleepingCycles": 0,
            }],
            # 우리 펌웨어는 8센서 모두 같은 프로파일을 쓴다
            "sensorConfigurations": [
                {"sensorIndex": i, "heaterProfile": HEATER_ID,
                 "dutyCycleProfile": "duty_1"} for i in range(8)
            ],
        },
    }


def convert(csv_path: Path, label: str, tag: int, out_dir: Path):
    df = pd.read_csv(csv_path)
    df.columns = [c.strip() for c in df.columns]
    col = {k: next((a for a in al if a in df.columns), None)
           for k, al in CSV_ALIASES.items()}
    need = ("time", "sensor", "temp", "hum", "gas", "step")
    miss = [k for k in need if col[k] is None]
    if miss:
        raise KeyError(f"{csv_path.name}: 컬럼 없음 {miss}")

    w = pd.DataFrame({
        "t": pd.to_numeric(df[col["time"]], errors="coerce"),
        "s": pd.to_numeric(df[col["sensor"]], errors="coerce"),
        "step": pd.to_numeric(df[col["step"]], errors="coerce"),
        "gas": pd.to_numeric(df[col["gas"]], errors="coerce"),
        "temp": pd.to_numeric(df[col["temp"]], errors="coerce"),
        "hum": pd.to_numeric(df[col["hum"]], errors="coerce"),
    })
    # 압력: 로거는 Pa, AI-Studio 는 hPa
    w["pres"] = (pd.to_numeric(df[col["pres"]], errors="coerce") / 100.0
                 if col["pres"] else 1013.25)

    # 더미 슬롯은 실제 측정이 아니므로 제외 (파서와 동일한 규칙)
    if col["valid"]:
        w = w[pd.to_numeric(df[col["valid"]], errors="coerce").fillna(1) != 0]
    w = w.dropna(subset=["t", "s", "step", "gas"])
    w = w[w["gas"] > 0]
    w["s"] = w["s"].astype(int)
    w["step"] = w["step"].astype(int)
    w = w.sort_values("t").reset_index(drop=True)
    if w.empty:
        raise ValueError(f"{csv_path.name}: 유효한 행이 없음")

    # 센서별 사이클 번호 (step 이 줄어들 때 새 사이클)
    w["cyc"] = 0
    for s, g in w.groupby("s"):
        idx = g.index
        cid = (g["step"] < g["step"].shift(fill_value=-1)).cumsum() + 1
        w.loc[idx, "cyc"] = cid.values

    rtc0 = int(csv_path.stat().st_mtime) - int(w["t"].max() / 1000)

    block = []
    for r in w.itertuples():
        block.append([
            int(r.s),
            SENSOR_IDS.get(int(r.s), 90000000 + int(r.s)),
            int(r.t),
            rtc0 + int(r.t / 1000),
            float(r.temp),
            float(r.pres),
            float(r.hum),
            float(r.gas),
            int(r.step),
            1,                      # scanning_enabled
            int(r.cyc),
            int(tag),               # label_tag ← 여기가 클래스 라벨
            0,                      # error_code
        ])

    doc = build_config()
    doc["rawDataHeader"] = {
        "counterPowerOnOff": 1,
        "seedPowerOnOff": seed(),
        "counterFileLimit": 1,
        "dateCreated": str(rtc0),
        "dateCreated_ISO": time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                         time.gmtime(rtc0)),
        "firmwareVersion": FIRMWARE,
        "boardId": BOARD_ID,
    }
    doc["rawDataBody"] = {
        "dataColumns": [
            {"name": n, "unit": u, "format": f, "key": k, "colId": i + 1}
            for i, (k, n, u, f) in enumerate(DATA_COLUMNS)
        ],
        "dataBlock": block,
    }

    base = f"{csv_path.stem}_Board_{BOARD_ID}_PowerOnOff_1_" \
           f"{doc['rawDataHeader']['seedPowerOnOff']}_File_1"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{base}.bmerawdata").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return base, doc["rawDataHeader"], len(block)


def write_labelinfo(out_dir: Path, base: str, header: dict, tags: dict):
    info = {
        "labelInfoHeader": {
            "counterPowerOnOff": header["counterPowerOnOff"],
            "seedPowerOnOff": header["seedPowerOnOff"],
            "dateCreated": header["dateCreated"],
            "dateCreated_ISO": header["dateCreated_ISO"],
            "firmwareVersion": header["firmwareVersion"],
            "boardId": header["boardId"],
        },
        "labelInformation": [
            {"labelTag": 0, "labelName": "Initial",
             "labelDescription": "Standard label for no label has been set"},
        ] + [
            {"labelTag": v, "labelName": k,
             "labelDescription": f"Scent class: {k}"}
            for k, v in sorted(tags.items(), key=lambda x: x[1])
        ],
    }
    (out_dir / f"{base}.bmelabelinfo").write_text(
        json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="CSV 파일 또는 폴더")
    ap.add_argument("--out", default="aistudio", help="출력 폴더")
    ap.add_argument("--label", help="라벨 (파일 하나만 변환할 때)")
    ap.add_argument("--tags", help="라벨=번호 목록. 예: Air=1,Citrus=2,Floral=3,Woody=4")
    args = ap.parse_args()

    tags = dict(DEFAULT_TAGS)
    if args.tags:
        tags = {}
        for kv in args.tags.split(","):
            k, v = kv.split("=")
            tags[k.strip()] = int(v)

    src = Path(args.src)
    files = sorted(src.rglob("*.csv")) if src.is_dir() else [src]
    if not files:
        sys.exit(f"{src} 에 CSV 가 없습니다")

    out = Path(args.out)
    print(f"히터 프로파일: {HEATER_ID} (timeBase {TIME_BASE})")
    print(f"라벨 번호: " + "  ".join(f"{k}={v}" for k, v in
                                   sorted(tags.items(), key=lambda x: x[1])))
    print()

    n_ok = 0
    for f in files:
        lab = args.label or label_from_name(f)
        if not lab:
            print(f"[SKIP] {f.name}: 라벨을 알 수 없음 "
                  "(파일명을 LOG_0036_Citrus.csv 형태로 하거나 --label 사용)")
            continue
        if lab not in tags:
            print(f"[SKIP] {f.name}: '{lab}' 은 --tags 에 없음")
            continue
        try:
            base, header, n = convert(f, lab, tags[lab], out)
            write_labelinfo(out, base, header, tags)
            print(f"[OK] {f.name:<26} → {lab:<7} tag={tags[lab]}  {n}행")
            n_ok += 1
        except Exception as e:
            print(f"[FAIL] {f.name}: {e}")

    print(f"\n{n_ok}개 파일 변환 → {out}/")
    print("\nAI-Studio 사용법:")
    print("  1. AI-Studio 실행 → New Project → Specify Classes")
    print("  2. 클래스 이름을 라벨과 똑같이 입력 (Air / Citrus / Floral / Woody)")
    print(f"  3. Import Data → {out} 폴더의 .bmerawdata 전부 선택")
    print("  4. 각 파일의 Label Tag 를 해당 클래스에 배정")
    print("  5. Train (ADAM, batch 32, 256 rounds 등)")


if __name__ == "__main__":
    main()
