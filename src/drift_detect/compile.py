"""CLI 진입점.

    python -m drift_detect.compile --repo <path> [--branch <name>] [--plan <path>] [--out-dir <path>]

계약이 없거나 파싱에 실패해도 정상 종료(exit 0)한다 — 에러가 아니다
(docs/spec/plan-contract-v1.md §6, fail-open). --repo 자체가 잘못된 경우만
사용법 오류로 취급해 0이 아닌 코드로 종료한다.
"""

import argparse
import json
import sys
from pathlib import Path

from drift_detect.compiler import compile_contract


def _default_out_dir(repo_root):
    return Path(repo_root) / ".claude" / "state"


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m drift_detect.compile",
        description="implementation-plan.md 의 yaml contract 블록을 contract.json 으로 컴파일한다.",
    )
    parser.add_argument("--repo", required=True, help="레포 루트 경로")
    parser.add_argument("--branch", default=None, help="브랜치명 (생략 시 git 에서 조회)")
    parser.add_argument("--plan", default=None, help="계획 문서 경로 (생략 시 브랜치 기반 기본 경로)")
    parser.add_argument(
        "--out-dir",
        default=None,
        help="contract.json / contract.meta.json 출력 디렉터리 (생략 시 <repo>/.claude/state)",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(args.repo)
    if not repo_root.is_dir():
        print(f"오류: --repo 경로가 디렉터리가 아님: {repo_root}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir) if args.out_dir else _default_out_dir(repo_root)

    contract, meta = compile_contract(
        repo_root=repo_root,
        branch=args.branch,
        plan_path=args.plan,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "contract.json").write_text(
        json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "contract.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if contract["status"] == "no_contract":
        print(f"계약 없음: {contract['reason']}")
    else:
        n_allow = len(contract["scope"]["allow"]) + len(contract["implicit_allow"])
        n_deny = len(contract["scope"]["deny"])
        print(f"계약 컴파일 완료: branch={contract['branch']} allow={n_allow} deny={n_deny}")

    # 침묵 실패 방지 (docs/spec/plan-contract-v1.md §6) — fail-open 은 정상과
    # 같은 모양(exit 0)이어도, 경고가 있으면 stdout 에서 반드시 드러나야 한다.
    for warning in contract.get("warnings", []):
        print(f"경고 [{warning['code']}]: {warning['message']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
