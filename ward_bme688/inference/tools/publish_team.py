#!/usr/bin/env python3
"""
publish_team.py
개발 폴더(이 git 저장소)의 **커밋된 내용**을 팀 저장소의 ward_bme688/inference/ 로 그대로 올린다.

  개발 폴더 C:\\2026capston (git, main)  ──커밋──▶  팀 저장소 ward_bme688/inference/  (브랜치 ward-inference → PR)

- 팀 저장소를 체크아웃하지 않는다(그 저장소에는 Windows 에서 못 만드는 파일 이름이 있다).
  팀 저장소 폴더에서 git 명령만으로 트리를 만들어 커밋한다.
- 커밋 안 된 변경이 있으면 멈춘다 → 올라가는 것 = 개발 폴더 git 에 기록된 것.
- 공개 저장소라 기본으로 빼는 경로: CLAUDE.md, docs/ (내부 메모·장치 정보). --with-docs 면 docs/ 를 올리되
  특허 문서(docs/특허_아이디어.md, docs/patent/)는 항상 빼고, 문서 안 `<!-- 비공개 시작 -->`~`<!-- 비공개 끝 -->` 구간은
  지운 사본을 올린다(개발 폴더 문서는 그대로). CLAUDE.md 는 늘 뺀다(Jetson 주소·MAC 등).
- 올릴 .md 에 특허 단어(특허·셔터·래칫·플랩·변리사·공지예외·출원)가 남아 있으면 멈춘다.
- secrets.h 가 섞이면 멈춘다. 팀 커밋 메시지 끝의 "Dev-Commit: <sha>" 로 지난번 이후 개발 커밋만 요약한다.

사용:
  python publish_team.py --dry-run     # 무엇이 바뀌는지만
  python publish_team.py               # 커밋 + push
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

DEV = Path(__file__).resolve().parent.parent
TEAM = Path(os.environ.get("WARD_TEAM_REPO",
                           Path.home() / "Desktop" / "capstone_code" / "2026ESWContest_free_THIRDIMPACT"))
PREFIX = "ward_bme688/inference"
BRANCH = "ward-inference"
PRIVATE = ["CLAUDE.md", "docs"]                       # 기본으로 빼는 경로
ALWAYS_PRIVATE = ["CLAUDE.md", "docs/특허_아이디어.md", "docs/patent"]   # --with-docs 여도 빼는 경로
PRIVATE_START, PRIVATE_END = "<!-- 비공개 시작 -->", "<!-- 비공개 끝 -->"
PATENT_WORDS = re.compile(r"특허|셔터|래칫|플랩|변리사|공지예외|출원")
COAUTHOR = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


def git(repo, *args, env=None, check=True):
    # core.quotepath=false: 한글 파일 이름을 "\352..." 로 감싸지 않고 그대로 (11/15 — 감싸진 이름이
    # .md 로 안 보여 비공개 구간 지우기를 건너뛰고 특허 문장이 공개 저장소에 올라간 사고가 있었다)
    r = subprocess.run(["git", "-C", str(repo), "-c", "core.protectNTFS=false", "-c", "core.quotepath=false", *args],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    if check and r.returncode:
        sys.exit(f"git {' '.join(args)} 실패:\n{r.stderr.strip()}")
    return r.stdout.strip()


def strip_private(files, env):
    """올릴 .md 에서 비공개 구간을 지운 blob 으로 바꾸고, 특허 단어가 남은 줄을 돌려준다."""
    leaks = []
    for f in files:
        if not f.endswith(".md"):
            continue
        text = git(TEAM, "cat-file", "blob", f":{f}", env=env)
        out, hide = [], False
        for line in text.splitlines():
            key = line.strip()
            if key == PRIVATE_START:
                hide = True
                continue
            if key == PRIVATE_END:
                hide = False
                continue
            if not hide:
                out.append(line)
        if hide:
            leaks.append(f"{f}: '{PRIVATE_START}' 가 닫히지 않음")
        new = "\n".join(out)
        text = "\n".join(text.splitlines())
        for n, line in enumerate(out, 1):
            if PATENT_WORDS.search(line):
                leaks.append(f"{f}:{n}: {line.strip()[:80]}")
        if new != text:
            r = subprocess.run(["git", "-C", str(TEAM), "hash-object", "-w", "--stdin"], input=new + "\n",
                               capture_output=True, text=True, encoding="utf-8")
            blob = r.stdout.strip()
            mode = git(TEAM, "ls-files", "-s", f, env=env).split()[0]
            git(TEAM, "update-index", "--cacheinfo", f"{mode},{blob},{f}", env=env)
    return leaks


def main():
    ap = argparse.ArgumentParser(description="개발 폴더 → 팀 저장소 ward_bme688/inference")
    ap.add_argument("--dry-run", action="store_true", help="바뀌는 파일만 보여 주고 끝")
    ap.add_argument("--no-push", action="store_true", help="팀 저장소에 커밋만 하고 push 는 안 함")
    ap.add_argument("--with-docs", action="store_true",
                    help="docs/ 도 올림 — 특허 문서·비공개 구간은 빼고 (CLAUDE.md 는 늘 뺌)")
    args = ap.parse_args()

    if git(DEV, "status", "--porcelain"):
        sys.exit("개발 폴더에 커밋 안 된 변경이 있습니다. 먼저 git commit 하세요.\n" + git(DEV, "status", "--short"))
    head = git(DEV, "rev-parse", "HEAD")
    if not (TEAM / ".git").exists():
        sys.exit(f"팀 저장소가 없습니다: {TEAM}  (환경변수 WARD_TEAM_REPO 로 위치 지정)")

    print(f"[팀 저장소] {TEAM}  브랜치 {BRANCH}")
    git(TEAM, "fetch", "-q", "origin")
    git(TEAM, "fetch", "-q", str(DEV), head)                       # 개발 커밋 개체를 팀 저장소로 가져옴
    base = f"origin/{BRANCH}"

    with tempfile.TemporaryDirectory() as td:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(td) / "index")}
        git(TEAM, "read-tree", base, env=env)
        git(TEAM, "rm", "-r", "-q", "--cached", "--ignore-unmatch", PREFIX, env=env)
        git(TEAM, "read-tree", f"--prefix={PREFIX}/", f"{head}^{{tree}}", env=env)
        for p in (ALWAYS_PRIVATE if args.with_docs else PRIVATE):
            git(TEAM, "rm", "-r", "-q", "--cached", "--ignore-unmatch", f"{PREFIX}/{p}", env=env)
        files = git(TEAM, "ls-files", PREFIX, env=env).splitlines()
        leaks = strip_private(files, env)
        if leaks:
            sys.exit("특허 단어가 남은 문서가 있습니다 — 중단 (비공개 표시로 감싸세요):\n" + "\n".join(leaks))
        bad = [f for f in files if re.search(r"(^|/)secrets\.h$", f)]
        if bad:
            sys.exit(f"secrets.h 가 들어감 — 중단: {bad}")
        tree = git(TEAM, "write-tree", env=env)
        # 마지막 확인: 올라갈 트리의 .md 를 다시 읽어 특허 단어·비공개 표시가 하나도 없는지 (경로 처리와 무관하게)
        left = []
        for f in git(TEAM, "ls-tree", "-r", "--name-only", tree, "--", PREFIX).splitlines():
            if f.endswith(".md") or f.endswith(".txt"):
                for n, line in enumerate(git(TEAM, "cat-file", "blob", f"{tree}:{f}").splitlines(), 1):
                    if PATENT_WORDS.search(line) or PRIVATE_START in line:
                        left.append(f"{f}:{n}: {line.strip()[:80]}")
        if left:
            sys.exit("올라갈 내용에 특허 문장이 남아 있음 — 중단:\n" + "\n".join(left))

    old_tree = git(TEAM, "rev-parse", f"{base}^{{tree}}")
    diff = git(TEAM, "diff", "--stat", old_tree, tree, "--", PREFIX)
    if tree == old_tree:
        print("바뀐 것이 없습니다.")
        return
    print(diff)
    if args.dry_run:
        print("\n(--dry-run: 커밋 안 함)")
        return

    last = re.findall(r"^Dev-Commit: ([0-9a-f]{7,40})", git(TEAM, "log", "-1", "--format=%B", base), re.M)
    rng = f"{last[0]}..{head}" if last and git(DEV, "cat-file", "-t", last[0], check=False) == "commit" else head
    subjects = git(DEV, "log", "--format=- %s", rng).splitlines()[:30]
    msg = (f"ward_bme688/inference: 개발 폴더 동기화 ({head[:7]})\n\n" + "\n".join(subjects)
           + f"\n\nDev-Commit: {head}\n\n{COAUTHOR}\n")
    commit = subprocess.run(["git", "-C", str(TEAM), "commit-tree", tree, "-p", base, "-F", "-"],
                            input=msg, capture_output=True, text=True, encoding="utf-8")
    if commit.returncode:
        sys.exit(commit.stderr)
    sha = commit.stdout.strip()
    git(TEAM, "update-ref", f"refs/heads/{BRANCH}", sha)
    print(f"\n[커밋] {sha[:7]}  ({len(subjects)}개 개발 커밋 요약)")
    if args.no_push:
        print("(--no-push: push 안 함)")
        return
    out = subprocess.run(["git", "-C", str(TEAM), "push", "origin", f"{BRANCH}:{BRANCH}"],
                         capture_output=True, text=True, encoding="utf-8")
    print(out.stderr.strip().splitlines()[-1] if out.stderr.strip() else out.stdout.strip())
    if out.returncode:
        sys.exit("push 실패")


if __name__ == "__main__":
    main()
