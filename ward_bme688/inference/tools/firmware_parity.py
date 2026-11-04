#!/usr/bin/env python3
"""
firmware_parity.py
와드 펌웨어의 판정 코드(inference/firmware/ward_mqtt/ward_infer.h)가 파이썬 판정기(ward_controller.py)와
**같은 판정을 내는지** PC 에서 확인한다. 펌웨어에 올리기 전에, 그리고 둘 중 하나를 고친 뒤에 꼭 돌린다.

  1. 모델(json) → model.h (export_model.py 와 같은 변환)
  2. ward_infer.h + model.h + 작은 시험 프로그램을 gcc 로 컴파일 (임시 폴더)
  3. 같은 녹화 CSV 를 C 프로그램과 파이썬 Controller 에 각각 흘림
  4. 라운드마다의 판정·청정기 권고와 ON/OFF 시점을 비교

사용:
  python firmware_parity.py                      # training/recordings 의 녹화 전부, 모델 = inference/openset_model.json
  python firmware_parity.py ..\\training\\recordings\\rec_....csv
필요: gcc (MinGW).
"""

import argparse
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import MODEL, RECORDINGS, WARD_FW              # noqa: E402
from export_model import export                           # noqa: E402
from ward_controller import Controller                    # noqa: E402

INFER_H = WARD_FW / "ward_infer.h"

HARNESS = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ward_infer.h"
int main(int argc, char **argv) {
  FILE *f = fopen(argv[1], "r"); char line[512]; wi_state_t *S = malloc(sizeof(wi_state_t));
  wi_params_t P = wi_default_params();
  if (argc > 2) P.rebase = (float)atof(argv[2]);
  wi_init(S, P);
  if (!f) return 1;
  while (fgets(line, sizeof line, f)) {
    double t; int s, k, valid; float T, Pp, H, g; unsigned mi, idac; char st[16];
    if (line[0] == '#' || line[0] == 'T') continue;
    if (sscanf(line, "%lf,%d,%f,%f,%f,%f,%d,%u,%u,%15[^,],%d", &t, &s, &T, &Pp, &H, &g, &k, &mi, &idac, st, &valid) != 11) continue;
    if (wi_feed(S, t, s, g, k, valid)) {
      wi_decision_t *D = &S->last;
      printf("%.3f,%s,%d,%d,%.4f,%d,%d,%d\n", D->sec, wi_label_name(D->top), D->k, D->n, D->mag, D->purifier, D->event, D->rebased);
    }
  }
  return 0;
}
"""


def python_log(model, csv, rebase):
    ns = argparse.Namespace(on=None, on_cycles=3, off_cycles=12, min_on=180.0, warmup=60.0, flat=0.05,
                            base_at=None, track=0.02, rebase=rebase, rebase_weak=False, spray_lock=180.0,
                            on_cmd="ON000", verbose=False)
    c = Controller(model, ns, send=False)
    with contextlib.redirect_stdout(io.StringIO()):
        for ln in csv.read_text(encoding="utf-8", errors="replace").splitlines():
            c.feed(ln.strip())
    return [(sec, top, on) for sec, top, k, n, mag, on in c.log]


def transitions(seq):
    out, prev = [], False
    for sec, _top, on in seq:
        if on != prev:
            out.append((round(sec / 60, 1), "ON" if on else "OFF"))
        prev = on
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="*", type=Path)
    ap.add_argument("--model", type=Path, default=MODEL)
    ap.add_argument("--rebase", type=float, default=180.0)
    args = ap.parse_args()
    if shutil.which("gcc") is None:
        sys.exit("gcc 가 없습니다 (MinGW 설치 필요).")
    model = json.loads(args.model.read_text(encoding="utf-8"))
    on = model.get("on_labels", ["Coffee", "Vinegar"])
    files = args.csv or sorted(p for p in RECORDINGS.glob("*.csv") if "discard" not in p.name)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        shutil.copy(INFER_H, td / "ward_infer.h")
        export(model, set(on), td / "model.h")
        (td / "harness.c").write_text(HARNESS, encoding="utf-8")
        exe = td / "harness.exe"
        r = subprocess.run(["gcc", "-O2", "-std=c99", "-Wall", "-o", str(exe), str(td / "harness.c"), "-lm"],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit("컴파일 실패:\n" + r.stderr)
        if r.stderr.strip():
            print("[gcc 경고]\n" + r.stderr)
        print(f"[모델] {args.model.name}  클래스 {', '.join(sorted(model['classes']))}  출동 {', '.join(on)}\n")
        tot_n = tot_same = tot_on_p = tot_on_c = tot_on_m = 0
        for csv in files:
            out = subprocess.run([str(exe), str(csv), str(args.rebase)], capture_output=True, text=True).stdout
            c_seq = []
            for ln in out.splitlines():
                p = ln.split(",")
                c_seq.append((float(p[0]), p[1], p[5] == "1"))
            py = python_log(model, csv, args.rebase)
            # 가장 가까운 C 판정(±5초 = 판정 주기의 절반)과 짝지어 비교.
            # 센서 8개 사이클을 묶는 방식이 파이썬(전체 기록으로 위상 계산)과 C(라운드)가 달라 판정 시각이
            # 몇 초 어긋날 수 있다 — 판정 내용이 같은지를 본다.
            j = same = n = 0
            for sec, top, on_ in py:
                while j + 1 < len(c_seq) and abs(c_seq[j + 1][0] - sec) <= abs(c_seq[j][0] - sec):
                    j += 1
                if c_seq and abs(c_seq[j][0] - sec) <= 5:
                    n += 1
                    same += (c_seq[j][1] == top and c_seq[j][2] == on_)
            tot_n += n
            tot_same += same
            tp, tc = transitions(py), transitions(c_seq)
            ons_p = [t for t, e in tp if e == "ON"]
            ons_c = [t for t, e in tc if e == "ON"]
            matched = sum(1 for t in ons_p if any(abs(t - u) <= 1.0 for u in ons_c))
            tot_on_p += len(ons_p)
            tot_on_c += len(ons_c)
            tot_on_m += matched
            print(f"{csv.name:45s} 판정 파이썬 {len(py):4d} / C {len(c_seq):4d}  짝 {n:4d}  일치 {100*same/max(n,1):5.1f}%  "
                  f"출동 파이썬 {len(ons_p)} / C {len(ons_c)} (1분 안 일치 {matched})")
            if matched != len(ons_p) or len(ons_p) != len(ons_c):
                print(f"    파이썬 {tp}\n    C      {tc}")
        print(f"\n전체: 라운드 판정 일치 {100*tot_same/max(tot_n,1):.1f}% ({tot_same}/{tot_n}) · "
              f"출동 파이썬 {tot_on_p} / C {tot_on_c}, 1분 안 일치 {tot_on_m}")


if __name__ == "__main__":
    main()
