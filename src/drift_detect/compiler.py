"""M0 — 계약 컴파일러 본체. plan §5 블록 → contract dict (+ meta dict).

모든 실패 경로는 예외를 던지지 않고 "계약 없음(no_contract)" 으로 폴백한다
(docs/spec/plan-contract-v1.md §6, fail-open). 이 모듈은 파일을 쓰지 않는다 —
쓰기는 CLI(compile.py) 책임이다.
"""

import hashlib
import subprocess
import time
from pathlib import Path

from drift_detect.block_extractor import BlockExtractionError, extract_contract_blocks
from drift_detect.implicit_allow import implicit_allow_patterns
from drift_detect.pathspec import PathValidationError, validate_path
from drift_detect.yaml_mini import ContractParseError, parse_contract_yaml

SUPPORTED_CONTRACT_VERSION = 1

# 레포 기본 escalation 정책 파일 위치 (docs/decisions/0001-repo-escalation-paths.md).
# git 추적 대상이며, 저작 스키마(계약 블록)와 무관하게 레포 전체에 적용되는 기본값이다.
REPO_ESCALATION_POLICY_REL_PATH = Path(".claude", "policy", "repo-escalation.yaml")


def _no_contract(reason, branch=None, plan_path=None, warnings=None):
    return {
        "status": "no_contract",
        "reason": reason,
        "branch": branch,
        "plan_path": plan_path,
        # 침묵 실패 방지 (docs/spec/plan-contract-v1.md §6) — fail-open 이어도
        # 사람이 사후에 확인할 수 있는 흔적을 남긴다. 없으면 빈 리스트.
        "warnings": warnings or [],
    }


def _rel_or_abs(path, repo_root):
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def detect_branch(repo_root):
    """git 으로 현재 브랜치명을 얻는다. 실패(비-git 저장소 등) 시 None."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    branch = result.stdout.strip()
    return branch or None


def default_plan_path(repo_root, branch):
    """브랜치명에서 결정론적으로 도출되는 기본 계획 문서 경로.

    브랜치명의 '/' 는 디렉터리 중첩 그대로 반영된다: 'feat/a/b' →
    documents/tasks/feat/a/b/implementation-plan.md
    """
    parts = branch.split("/")
    return Path(repo_root, "documents", "tasks", *parts, "implementation-plan.md")


def _build_meta(plan_path, repo_root, policy_meta=None):
    data = plan_path.read_bytes()
    meta = {
        "source_path": _rel_or_abs(plan_path, repo_root),
        "mtime": plan_path.stat().st_mtime,
        "sha256": hashlib.sha256(data).hexdigest(),
        "compiled_at": time.time(),
        # 정책 파일 추적 (docs/decisions/0001 필수 조건 4) — 누락 시 정책 파일만
        # 고쳤을 때 contract.json 캐시가 갱신되지 않는 결함이 된다. 정책 파일이
        # 없거나 무효하면 전부 None — fail-open, 캐시 무효화 대상이 없을 뿐이다.
        "policy_path": None,
        "policy_mtime": None,
        "policy_sha256": None,
    }
    if policy_meta is not None:
        meta["policy_path"] = policy_meta["path"]
        meta["policy_mtime"] = policy_meta["mtime"]
        meta["policy_sha256"] = policy_meta["sha256"]
    return meta


def _load_repo_escalation_policy(repo_root, repo_name):
    """레포 기본 escalation 정책 파일을 읽는다 (docs/decisions/0001).

    저작 스키마(계약 블록)와 같은 최소 YAML 형식을 그대로 재사용한다 — 새 파서를
    만들지 않는다 (결정문 필수 조건 1). `scope.allow` 목록을 escalation 대상
    경로로 쓴다. `deny` 는 이 정책 파일에서 의미가 없으므로 무시한다.

    fail-open 이되 침묵하지 않는다 (스펙 §6) — 실패마다 사람이 고칠 수 있는
    경고를 세 번째 반환값에 남긴다. 개별 경로 무효는 그 경로만 버리고 나머지는
    살린다 (전부를 버리지 않는다). 반환: (patterns: list[str]|None,
    meta: dict|None, warnings: list[dict]).
    """
    policy_path = repo_root / REPO_ESCALATION_POLICY_REL_PATH
    if not policy_path.is_file():
        return None, None, []

    rel_path = _rel_or_abs(policy_path, repo_root)

    try:
        data = policy_path.read_bytes()
        text = data.decode("utf-8")
        parsed = parse_contract_yaml(text)
    except (OSError, UnicodeDecodeError, ContractParseError) as e:
        return (
            None,
            None,
            [
                {
                    "code": "policy_parse_error",
                    "message": f"레포 기본 escalation 정책 파일 파싱 실패 ({rel_path}): {e}",
                }
            ],
        )

    if parsed["contract_version"] != SUPPORTED_CONTRACT_VERSION:
        return (
            None,
            None,
            [
                {
                    "code": "policy_unsupported_version",
                    "message": (
                        f"레포 기본 escalation 정책 파일({rel_path})의 "
                        f"contract_version={parsed['contract_version']} 은 지원 대상이 아님 "
                        f"(v{SUPPORTED_CONTRACT_VERSION} 전용)"
                    ),
                }
            ],
        )

    candidate_patterns = parsed["scope"]["allow"]
    patterns = []
    warnings = []
    for p in candidate_patterns:
        try:
            validate_path(p, repo_name=repo_name, repo_root=repo_root)
        except PathValidationError as e:
            warnings.append(
                {
                    "code": "policy_invalid_path",
                    "message": (
                        f"레포 기본 escalation 정책 파일({rel_path})의 경로 무시됨: {e}"
                    ),
                }
            )
        else:
            patterns.append(p)

    if not patterns:
        return None, None, warnings

    meta = {
        "path": rel_path,
        "mtime": policy_path.stat().st_mtime,
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    return patterns, meta, warnings


def compile_contract(repo_root, branch=None, plan_path=None):
    """계약을 컴파일한다.

    반환: (contract: dict, meta: dict|None) 의 2-tuple.
    contract["status"] 는 "ok" 또는 "no_contract". 예외를 던지지 않는다.
    """
    # 심볼릭 링크를 resolve() 하지 않는다 — 임시 디렉터리(예: macOS /var → /private/var)에서
    # repo_root 와 명시적으로 넘어온 plan_path 가 다른 표기로 어긋나 relative_to() 가
    # 깨지는 것을 피한다. repo_root 는 호출자가 이미 의도한 경로 그대로 신뢰한다.
    repo_root = Path(repo_root)
    repo_name = repo_root.name

    if branch is None:
        branch = detect_branch(repo_root)
    if not branch:
        return _no_contract("브랜치를 확인할 수 없음 (git 저장소가 아니거나 detached HEAD)"), None

    if plan_path is None:
        plan_path = default_plan_path(repo_root, branch)
    else:
        plan_path = Path(plan_path)
        if not plan_path.is_absolute():
            plan_path = repo_root / plan_path

    plan_rel = _rel_or_abs(plan_path, repo_root)

    if not plan_path.is_file():
        return _no_contract("계획 문서 없음", branch=branch, plan_path=plan_rel), None

    policy_patterns, policy_meta, policy_warnings = _load_repo_escalation_policy(
        repo_root, repo_name
    )
    meta = _build_meta(plan_path, repo_root, policy_meta=policy_meta)
    text = plan_path.read_text(encoding="utf-8")

    try:
        blocks = extract_contract_blocks(text)
    except BlockExtractionError as e:
        return (
            _no_contract(
                f"펜스 구조 오류: {e}",
                branch=branch,
                plan_path=plan_rel,
                warnings=policy_warnings,
            ),
            meta,
        )

    if len(blocks) == 0:
        return (
            _no_contract(
                "yaml contract 블록 없음",
                branch=branch,
                plan_path=plan_rel,
                warnings=policy_warnings,
            ),
            meta,
        )
    if len(blocks) >= 2:
        dup_reason = f"yaml contract 블록이 {len(blocks)}개 존재 (최대 1개, 파싱 오류)"
        return (
            _no_contract(
                dup_reason,
                branch=branch,
                plan_path=plan_rel,
                warnings=policy_warnings
                + [{"code": "multiple_contract_blocks", "message": dup_reason}],
            ),
            meta,
        )

    try:
        parsed = parse_contract_yaml(blocks[0])
    except ContractParseError as e:
        return (
            _no_contract(
                f"파싱 오류: {e}",
                branch=branch,
                plan_path=plan_rel,
                warnings=policy_warnings,
            ),
            meta,
        )

    if parsed["contract_version"] != SUPPORTED_CONTRACT_VERSION:
        return (
            _no_contract(
                f"지원하지 않는 contract_version: {parsed['contract_version']} "
                f"— 이 파서는 v{SUPPORTED_CONTRACT_VERSION} 전용 (스펙 §3)",
                branch=branch,
                plan_path=plan_rel,
                warnings=policy_warnings,
            ),
            meta,
        )

    explicit_allow = parsed["scope"]["allow"]
    explicit_deny = parsed["scope"]["deny"]

    try:
        for p in explicit_allow:
            validate_path(p, repo_name=repo_name, repo_root=repo_root)
        for p in explicit_deny:
            validate_path(p, repo_name=repo_name, repo_root=repo_root)
    except PathValidationError as e:
        # 스펙 §6 "fail-open 은 침묵이 아니다" — no_contract 폴백 동작 자체는
        # 그대로 두되(부분 필터링 없음), 최빈 오류가 흔적 없이 사라지지 않도록
        # warnings 에 남긴다. CLI 는 warnings 를 stdout 에도 노출한다.
        invalid_path_warning = {
            "code": "contract_invalid_path",
            "message": f"계약 블록 경로 규격 위반 ({plan_rel}): {e}",
        }
        return (
            _no_contract(
                f"경로 규격 위반: {e}",
                branch=branch,
                plan_path=plan_rel,
                warnings=policy_warnings + [invalid_path_warning],
            ),
            meta,
        )

    implicit_allow = implicit_allow_patterns(branch)

    contract = {
        "status": "ok",
        "contract_version": SUPPORTED_CONTRACT_VERSION,
        "branch": branch,
        "plan_path": plan_rel,
        "scope": {
            "allow": explicit_allow,
            "deny": explicit_deny,
        },
        # 선언 없이 항상 허용되는 경로 (스펙 §4). deny 우선순위는 이 목록에도 동일 적용된다 —
        # 스펙이 명시하지 않은 지점이라 기본값을 "일관 적용"으로 잡았다 (구현 결정, §7 미결과는 별개).
        "implicit_allow": implicit_allow,
        # 레포 기본값 병합 지점 (docs/decisions/0001). deny 가 아니다 — 차단이 아니라
        # 판정 소집이다. 유효한 explicit allow 가 있을 때만(=이 "ok" 분기에서만) 병합한다 —
        # 레포 기본값 단독으로 "계약 있음"을 만들지 않는다 (필수 조건 5).
        # 각 항목에 병합 출처를 남겨 verdict.reason 이 나중에 구분 표기할 수 있게 한다
        # (필수 조건 3). M0 는 계약 블록에 escalate_paths 를 저작할 수 없으므로
        # (스펙 §3 "저작 스키마가 아닌 필드") 출처는 항상 "repo_default" 다.
        "escalate_paths": [
            {"pattern": p, "source": "repo_default"} for p in (policy_patterns or [])
        ],
        # 침묵 실패 방지 (스펙 §6) — 레포 기본 정책 파일 처리 중 발생한 fail-open 흔적.
        "warnings": policy_warnings,
    }
    return contract, meta
