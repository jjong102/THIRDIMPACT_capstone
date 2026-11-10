#!/usr/bin/env python3
"""
session_id.py
학습 CSV 의 source_file(파일 이름)에서 "어느 녹화의, 몇 번째 조각인지"를 읽어낸다.
baseline_correct.py(기준선 짝짓기)와 open_set.py(녹화 단위 교차검증)가 쓴다.

지원하는 파일명
  1) 녹화 조각 (label_tool.py)   rec_20261105_163321_day4_rehearsal__seg3.csv
       → recording = rec_20261105_163321_day4_rehearsal, date = 20261105, seg = 3
  2) 녹화 통째                    rec_20261105_163321_day4_rehearsal.csv
       → recording = 파일명과 동일, seg = None
  3) 그 외                        → recording = 파일 이름(확장자 제외)

왜 필요한가
  - label_tool.py 는 녹화 1개를 여러 조각(__segN)으로 저장한다.
    조각을 서로 다른 "세션"으로 보면 같은 녹화의 쌍둥이 데이터가 train/test 에
    나뉘어 들어가 점수가 부풀려진다 → 녹화 단위로 묶어야 한다.
"""

import re
from datetime import datetime
from pathlib import Path

_REC = re.compile(r"^(?P<rec>rec_(?P<date>\d{8})_(?P<time>\d{6})(?:_[^/\\]*?)?)"
                  r"(?:__seg(?P<seg>\d+))?$")


def session_info(name) -> dict:
    """파일 이름 → {kind, recording, date, start, seq, seg}

    kind      : 'rec' | 'other'
    recording : 같은 녹화(=같은 전원 연속 측정)면 같은 값
    date      : 'YYYYMMDD' 또는 None
    start     : 녹화 시작 시각(datetime) 또는 None  (rec 만)
    seq       : 항상 None (예전 SD 로거 형식 호환용 칸)
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

    return {"kind": "other", "recording": stem, "date": None,
            "start": None, "seq": None, "seg": None}
