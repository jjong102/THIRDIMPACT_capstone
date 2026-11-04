#!/usr/bin/env python3
"""
firmware_parity.py
와드 펌웨어의 판정 코드(firmware/ward_mqtt/ward_infer.h)가 파이썬 판정기(ward_controller.py)와
**같은 판정을 내는지** PC 에서 확인한다. 펌웨어에 올리기 전에, 그리고 둘 중 하나를 고친 뒤에 꼭 돌린다.

  1. 모델(json) → model.h (export_model.py 와 같은 변환)
  2. ward_infer.h + model.h + 작은 시험 프로그램을 gcc 로 컴파일 (임시 폴더)
  3. 같은 녹화 CSV 를 C 프로그램과 파이썬 Controller 에 각각 흘림
  4. 라운드마다의 판정·청정기 권고와 ON/OFF 시점을 비교
  5. 무선 모델(글자 형식) 읽기 시험: 정상 모델은 받아들여 판정이 같고, 망가진 모델은 거절하는지

사용:
  python firmware_parity.py                      # training/recordings 의 녹화 전부, 모델 = 기본 모델 경로
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
from export_model import export, model_text               # noqa: E402
from ward_controller import Controller                    # noqa: E402

INFER_H = WARD_FW / "ward_infer.h"

# argv: csv rebase [model.txt]  — model.txt 를 주면 wi_model_parse 로 읽어 그 모델로 판정
HARNESS = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ward_infer.h"
int main(int argc, char **argv) {
  FILE *f; char line[512]; wi_state_t *S = malloc(sizeof(wi_state_t));
  wi_params_t P = wi_default_params();
  if (argc > 2) P.rebase = (float)atof(argv[2]);
  wi_init(S, P);
  if (argc > 3) {
    static char txt[WI_MODEL_TEXT_MAX + 8]; char err[64]; size_t n; wi_model_t M;
    FILE *mf = fopen(argv[3], "rb"); if (!mf) return 3;
    n = fread(txt, 1, sizeof txt, mf); fclose(mf);
    if (!wi_model_parse(txt, n, &M, err, sizeof err)) { printf("PARSE_FAIL %s\n", err); return 2; }
    S->M = M;
    printf("PARSE_OK %s %d\n", S->M.id, S->M.n);
  }
  f = fopen(argv[1], "r");
  if (!f) return 1;
  while (fgets(line, sizeof line, f)) {
    double t; int s, k, valid; float T, Pp, H, g; unsigned mi, idac; char st[16];
    if (line[0] == '#' || line[0] == 'T') continue;
    if (sscanf(line, "%lf,%d,%f,%f,%f,%f,%d,%u,%u,%15[^,],%d", &t, &s, &T, &Pp, &H, &g, &k, &mi, &idac, st, &valid) != 11) continue;
    if (wi_feed(S, t, s, g, k, valid)) {
      wi_decision_t *D = &S->last;
      printf("%.3f,%s,%d,%d,%.4f,%d,%d,%d\n", D->sec, wi_label_name(S, D->top), D->k, D->n, D->mag, D->purifier, D->event, D->rebased);
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


def _decisions(stdout):
    seq = []
    for ln in stdout.splitlines():
        if ln.startswith("PARSE_"):
            continue
        p = ln.split(",")
        seq.append((float(p[0]), p[1], p[5] == "1"))
    return seq


def build(model, on, td):
    shutil.copy(INFER_H, td / "ward_infer.h")
    export(model, set(on), td / "model.h")
    (td / "harness.c").write_text(HARNESS, encoding="utf-8")
    exe = td / "harness.exe"
    r = subprocess.run(["gcc", "-O2", "-std=c99", "-Wall", "-o", str(exe), str(td / "harness.c"), "-lm"],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError("컴파일 실패:\n" + r.stderr)
    return exe, r.stderr.strip()


def parser_selftest(model, on, exe, csv, td, rebase):
    """무선 모델 글자 형식: 정상 → 받아들이고 기본 모델과 판정 동일 / 망가진 것 → 거절."""
    txt = model_text(model, set(on))
    good = td / "good.txt"
    good.write_text(txt, encoding="ascii", newline="\n")
    base = subprocess.run([str(exe), str(csv), str(rebase)], capture_output=True, text=True).stdout
    via = subprocess.run([str(exe), str(csv), str(rebase), str(good)], capture_output=True, text=True)
    ok_parse = via.stdout.startswith("PARSE_OK")
    same = _decisions(base) == _decisions(via.stdout)
    import zlib

    def recrc(t):                     # 내용을 고친 뒤 체크섬만 다시 맞춤 → 내용 검사가 잡는지 본다
        body = t.rsplit("crc ", 1)[0]
        return body + f"crc {zlib.crc32(body.encode('ascii')) & 0xFFFFFFFF:08x}\n"

    lines = txt.splitlines(keepends=True)
    ci = next(i for i, ln in enumerate(lines) if ln.startswith("class "))
    f = lines[ci].split()
    f_bad_mu = f[:4] + [f"{float(v) * 3:.7g}" for v in f[4:]]
    f_bad_ang = f[:3] + ["200"] + f[4:]
    bad_cases = {
        "체크섬 틀림": txt.replace("tau ", "tau 9", 1),
        "방향 길이 틀림": recrc("".join(lines[:ci] + [" ".join(f_bad_mu) + "\n"] + lines[ci + 1:])),
        "각도 범위 밖": recrc("".join(lines[:ci] + [" ".join(f_bad_ang) + "\n"] + lines[ci + 1:])),
        "crc 줄 없음": txt.rsplit("crc ", 1)[0],
        "빈 내용": "",
    }
    rejected = {}
    for name, t in bad_cases.items():
        pth = td / "bad.txt"
        pth.write_text(t, encoding="ascii", newline="\n")
        r = subprocess.run([str(exe), str(csv), str(rebase), str(pth)], capture_output=True, text=True)
        rejected[name] = (r.returncode == 2, r.stdout.strip().splitlines()[0] if r.stdout.strip() else "")
    return ok_parse and same and all(v[0] for v in rejected.values()), ok_parse, same, rejected


def run(files=None, model_path=MODEL, rebase=180.0, verbose=True):
    """반환: dict(round_match, rounds, on_py, on_c, on_matched, parser_ok)"""
    if shutil.which("gcc") is None:
        raise RuntimeError("gcc 가 없습니다 (MinGW 설치 필요).")
    model = json.loads(Path(model_path).read_text(encoding="utf-8"))
    on = model.get("on_labels", ["Coffee", "Vinegar"])
    files = files or sorted(p for p in RECORDINGS.glob("*.csv") if "discard" not in p.name)
    say = print if verbose else (lambda *a, **k: None)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        exe, warn = build(model, on, td)
        if warn:
            say("[gcc 경고]\n" + warn)
        say(f"[모델] {Path(model_path).name}  클래스 {', '.join(sorted(model['classes']))}  출동 {', '.join(on)}\n")
        tot_n = tot_same = tot_on_p = tot_on_c = tot_on_m = 0
        for csv in files:
            out = subprocess.run([str(exe), str(csv), str(rebase)], capture_output=True, text=True).stdout
            c_seq = _decisions(out)
            py = python_log(model, csv, rebase)
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
            say(f"{csv.name:45s} 판정 파이썬 {len(py):4d} / C {len(c_seq):4d}  짝 {n:4d}  일치 {100*same/max(n,1):5.1f}%  "
                f"출동 파이썬 {len(ons_p)} / C {len(ons_c)} (1분 안 일치 {matched})")
            if matched != len(ons_p) or len(ons_p) != len(ons_c):
                say(f"    파이썬 {tp}\n    C      {tc}")
        say(f"\n전체: 라운드 판정 일치 {100*tot_same/max(tot_n,1):.1f}% ({tot_same}/{tot_n}) · "
            f"출동 파이썬 {tot_on_p} / C {tot_on_c}, 1분 안 일치 {tot_on_m}")

        all_ok, ok_parse, same, rejected = parser_selftest(model, on, exe, files[-1], td, rebase)
        say(f"\n[무선 모델 읽기 시험] 정상 모델 {'받아들임' if ok_parse else '✗ 거절'} · "
            f"기본 모델과 판정 {'같음' if same else '✗ 다름'}")
        for name, (rej, msg) in rejected.items():
            say(f"    {name:12s} → {'거절 ✓' if rej else '✗ 받아들임!'}  {msg}")
        return dict(round_match=tot_same / max(tot_n, 1), rounds=tot_n, on_py=tot_on_p, on_c=tot_on_c,
                    on_matched=tot_on_m, parser_ok=all_ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="*", type=Path)
    ap.add_argument("--model", type=Path, default=MODEL)
    ap.add_argument("--rebase", type=float, default=180.0)
    args = ap.parse_args()
    try:
        run(args.csv or None, args.model, args.rebase)
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
