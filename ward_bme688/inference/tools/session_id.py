#!/usr/bin/env python3
"""
session_id.py
학습 CSV 의 source_file(파일 이름)에서 "어느 녹화의, 몇 번째 조각인지"를 읽어낸다.
baseline_correct.py(기준선 짝짓기)와 train_scent.py(교차검증 그룹)가 함께 쓴다.

지원하는 파일명
  1) 시리얼 녹화 조각 (label_tool.py)   rec_20260929_165751_woody_day1__seg3.csv
       → recording = rec_20260929_165751_woody_day1, date = 20260929, seg = 3
  2) 시리얼 녹화 통째 (조각 안 냄)        rec_20260929_165751_woody_day1.csv
       → recording = 파일명과 동일, seg = None
  3) SD 로거 블록                       LOG_0031.csv / LOG_0031_Woody.csv
       → recording = LOG_0031, seq = 31, date = 알 수 없음(None)
  4) 그 외                              → recording = 파일 이름(확장자 제외)

왜 필요한가
  - label_tool.py 는 녹화 1개를 여러 조각(__segN)으로 저장한다.
    조각을 서로 다른 "세션"으로 보면 같은 녹화의 쌍둥이 데이터가 train/test 에
    나뉘어 들어가 점수가 부풀려진다 → 녹화 또는 날짜 단위로 묶어야 한다.
  - 예전 baseline_correct.py 는 파일명의 첫 숫자를 세션 번호로 썼는데,
    rec_ 형식에서는 그게 날짜(20260929)라서 같은 날 조각이 전부 한 세션으로 합쳐졌다.
"""

import re
from datetime import datetime
from pathlib import Path

_REC = re.compile(r"^(?P<rec>rec_(?P<date>\d{8})_(?P<time>\d{6})(?:_[^/\\]*?)?)"
                  r"(?:__seg(?P<seg>\d+))?$")
_LOG = re.compile(r"^LOG_(?P<seq>\d+)(?:_.*)?$", re.IGNORECASE)


def session_info(name) -> dict:
    """파일 이름 → {kind, recording, date, start, seq, seg}

    kind      : 'rec' | 'log' | 'other'
    recording : 같은 녹화(=같은 전원 연속 측정)면 같은 값
    date      : 'YYYYMMDD' 또는 None
    start     : 녹화 시작 시각(datetime) 또는 None  (rec 만)
    seq       : SD 로거 파일 번호 또는 None           (log 만)
    seg       : 조각 번호 또는 None
    """
    stem = Path(str(name)).name
    if stem.lower().endswith(".csv"):
        stem = stem[:-4]

    m = _REC.match(stem)
    if m:
        try:
            start = datetime.strptime(m["date"] + m["time"], "%Y%m%d%H%M%S")
        except ValueError:
            start = None
        return {"kind": "rec", "recording": m["rec"], "date": m["date"],
                "start": start, "seq": None,
                "seg": int(m["seg"]) if m["seg"] else None}

    m = _LOG.match(stem)
    if m:
        seq = int(m["seq"])
        return {"kind": "log", "recording": f"LOG_{seq:04d}", "date": None,
                "start": None, "seq": seq, "seg": None}

    return {"kind": "other", "recording": stem, "date": None,
            "start": None, "seq": None, "seg": None}


def group_key(name, mode: str) -> str:
    """교차검증 그룹 키.
    mode = 'file'      : 파일(조각) 하나 = 그룹 하나   (예전 동작, 조각끼리 누수 있음)
           'recording' : 녹화 하나 = 그룹 하나
           'date'      : 날짜 하나 = 그룹 하나         (날짜를 모르면 녹화 단위로 대체)
    """
    if mode == "file":
        return Path(str(name)).name
    info = session_info(name)
    if mode == "date" and info["date"]:
        return info["date"]
    return info["recording"]


if __name__ == "__main__":
    import sys
    for n in sys.argv[1:] or [
            "rec_20260929_165751_test1__seg3.csv",
            "rec_20260928_165700_woody_test.csv",
            "LOG_0031_Woody.csv", "features_misc.csv"]:
        i = session_info(n)
        print(f"{n:45s} → {i['kind']:5s} rec={i['recording']}  date={i['date']}  "
              f"seq={i['seq']}  seg={i['seg']}  | date-group={group_key(n, 'date')}")
