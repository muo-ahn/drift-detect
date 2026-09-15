"""M1 오프라인 시뮬레이터 — 설치 전에 오탐률을 추정한다 (구현 범위 §4).

git 이력에서 과거 작업(브랜치)을 재생해, 그 범위에서 변경된 파일 목록을 뽑고
현재 디스크에 있는 그 브랜치의 계약(§compile_contract)에 대조한다.

계약 소스(`documents/tasks/**`)는 이 레포에서 보통 git 미추적이므로(gitignore),
커밋 시점 blob 이 아니라 "지금 그 경로에 있는 파일"을 읽는다 — compile_contract
의 평소 동작과 동일하다. 즉 브랜치 이름으로 계약을 찾을 뿐, 커밋 범위는 오직
"무엇이 바뀌었는가"를 뽑는 데만 쓰인다.

git 명령은 읽기 전용만 쓴다(diff). 쓰기 명령 금지.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from drift_detect.compiler import compile_contract
from drift_detect.predicates import evaluate

_GIT_TIMEOUT_SEC = 30


class SimulationError(Exception):
    """git 호출 실패 등, 시뮬레이션을 진행할 수 없는 상태."""


def _git(repo_root, *args):
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SEC,
    )
    if result.returncode != 0:
        raise SimulationError(f"git {' '.join(args)} 실패: {result.stderr.strip()}")
    return result.stdout


def changed_files(repo_root, branch, commit_range=None, base="dev"):
    """읽기 전용으로 변경 파일 목록을 얻는다.

    commit_range 가 있으면 그대로 `git diff --name-only <range>` 에 넘긴다.
    없으면 `git diff --name-only <base>...<branch>` (병합 기준점부터의
    대칭차) 로 그 브랜치 전체가 건드린 파일을 뽑는다.
    """
    diff_arg = commit_range if commit_range else f"{base}...{branch}"
    out = _git(repo_root, "diff", "--name-only", diff_arg)
    return [line for line in out.splitlines() if line]


def simulate(repo_root, branch, commit_range=None, base="dev"):
    """시뮬레이션 1회 실행.

    반환: {"branch", "has_contract", "reason", "files_checked",
    "predicate_hits": {code: [path, ...]}}.

    predicate_hits 는 evaluate() 를 그대로 재사용한다(observer.py 와 동일
    우선순위) — 여기서 매칭 로직을 다시 구현하면 두 곳이 조용히 어긋날 수
    있다.
    """
    repo_root = Path(repo_root)
    files = changed_files(repo_root, branch, commit_range=commit_range, base=base)

    contract, _meta = compile_contract(repo_root, branch=branch)
    has_contract = contract.get("status") == "ok"

    hits = {}
    if has_contract:
        for f in files:
            result = evaluate(f, contract)
            if result["code"] != "clean":
                hits.setdefault(result["code"], []).append(f)

    return {
        "branch": branch,
        "has_contract": has_contract,
        "reason": None if has_contract else contract.get("reason"),
        "files_checked": len(files),
        "predicate_hits": hits,
    }


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m drift_detect.simulate",
        description="git 이력을 재생해 M1 술어의 오탐 후보를 오프라인으로 추정한다.",
    )
    parser.add_argument("--repo", required=True, help="레포 루트 경로")
    parser.add_argument("--branch", required=True, help="시뮬레이션할 브랜치명 (계약 조회에 사용)")
    parser.add_argument("--base", default="dev", help="비교 기준 브랜치 (기본 dev)")
    parser.add_argument(
        "--commit-range",
        default=None,
        help="git diff 범위(예: A..B). 지정 시 변경 파일 목록만 이 범위로 대체하고 --branch/--base 는 그대로 계약 조회에 쓰인다",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(args.repo)
    if not repo_root.is_dir():
        print(f"오류: --repo 경로가 디렉터리가 아님: {repo_root}", file=sys.stderr)
        return 2

    try:
        result = simulate(
            repo_root,
            branch=args.branch,
            commit_range=args.commit_range,
            base=args.base,
        )
    except SimulationError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
