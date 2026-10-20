"""M0 — 계약 컴파일러 본체. plan §5 블록 → contract dict (+ meta dict).

모든 실패 경로는 예외를 던지지 않고 "계약 없음(no_contract)" 으로 폴백한다
(docs/spec/plan-contract-v1.md §6, fail-open). 이 모듈은 파일을 쓰지 않는다 —
쓰기는 CLI(compile.py) 책임이다.
"""

import hashlib
import os
import subprocess
import time
from pathlib import Path

from drift_detect.block_extractor import BlockExtractionError, extract_contract_blocks
from drift_detect.implicit_allow import implicit_allow_patterns
from drift_detect.pathspec import PathValidationError, validate_path
from drift_detect.yaml_mini import ContractParseError, parse_contract_yaml

SUPPORTED_CONTRACT_VERSION = 1

# 레포 기본 escalation 정책 파일 위치 (docs/decisions/0001-repo-escalation-paths.md,
# 위치는 docs/decisions/0003-zero-footprint.md 로 개정). 대상 레포에는 아무 파일도
# 만들지 않는다 — 정책은 drift-detect 소스 트리 안, 레포별로 둔다:
#   <정책 디렉터리>/<repo basename>.yaml
# 정책 디렉터리 기본값은 이 파일(__file__) 기준으로 drift-detect 루트를 거슬러
# 올라가 계산한다 — 대상 레포 경로를 절대 쓰지 않는다. DRIFT_DETECT_POLICY_DIR
# 환경변수로 디렉터리 자체를 덮어쓸 수 있다(테스트·이식성, 0003 필수 조건).
_POLICY_DIR_ENV_VAR = "DRIFT_DETECT_POLICY_DIR"


def _default_policy_dir():
    """drift-detect 소스 트리 기준 정책 디렉터리 기본값.

    이 파일은 <drift-detect>/src/drift_detect/compiler.py 에 있으므로
    parents[2] 가 <drift-detect> 루트다.
    """
    return Path(__file__).resolve().parents[2] / "policies"


def _policy_dir():
    override = os.environ.get(_POLICY_DIR_ENV_VAR)
    if override:
        return Path(override)
    return _default_policy_dir()


_GITDIR_PREFIX = "gitdir:"
_GIT_FILE_MAX_BYTES = 4096


def _repo_name_from_gitdir(gitdir):
    """gitdir 경로에서 메인 레포 이름을 얻는다. 모르는 형태면 None.

    - `<main>/.git/worktrees/<wt>`            → `<main>` 의 basename
    - `<...>/.git/modules/<name>/worktrees/<wt>` → `<name>` (submodule 의 worktree)
    - `<...>/.git/modules/<name>`             → `<name>` (submodule 메인 체크아웃)
    중첩 submodule(`modules/a/modules/b/...`)은 마지막 모듈명이다.
    """
    container = gitdir.parent
    if container.name == "worktrees":
        main_git_dir = container.parent
        if main_git_dir.name == ".git":
            return main_git_dir.parent.name or None
        if main_git_dir.parent.name == "modules":
            return main_git_dir.name or None
        return None
    if container.name == "modules":
        return gitdir.name or None
    return None


def resolve_repo_name(repo_root):
    """정책 파일 조회에 쓰는 레포 이름. worktree 면 메인 레포의 basename.

    worktree 의 `.git` 은 `gitdir: <main>/.git/worktrees/<name>` 한 줄짜리 파일이다.
    이 경로에서 메인 레포를 거슬러 올라가 그 basename 을 쓴다(submodule 이면
    `.git/modules/<name>` 의 `<name>`) — 그렇지 않으면
    `.claude/worktrees/core-1788` 처럼 worktree 마다 이름이 달라 정책 파일을
    영영 찾지 못한다. subprocess 를 쓰지 않는다(observer 가 매 호출마다 돈다).

    일반 레포(`.git` 디렉터리), 읽기·파싱 실패, 형태가 다른 gitdir 은 전부 종전
    동작(repo_root 의 basename)으로 폴백한다.
    """
    root = Path(repo_root)
    git_entry = root / ".git"
    try:
        if git_entry.is_file():
            with git_entry.open("r", encoding="utf-8") as fh:
                first_line = fh.read(_GIT_FILE_MAX_BYTES).splitlines()[0]
            if first_line.startswith(_GITDIR_PREFIX):
                gitdir = Path(first_line[len(_GITDIR_PREFIX):].strip())
                if not gitdir.is_absolute():
                    gitdir = root / gitdir
                gitdir = Path(os.path.normpath(gitdir))  # 상대경로의 '..' 제거
                name = _repo_name_from_gitdir(gitdir)
                if name:
                    return name
    except (OSError, UnicodeDecodeError, IndexError):
        pass
    return root.name


def resolve_policy_path(repo_root, repo_name=None):
    """레포 기본 정책 파일의 경로를 계산한다.

    repo_name 을 생략하면 `resolve_repo_name(repo_root)` 를 쓴다(worktree 면 메인
    레포 이름) — 이 경로로는 항상 구분자·상위 참조 없는 단일 세그먼트가 나온다.
    repo_name 을 명시적으로 넘긴 경우까지 포함해, 경로 구분자('/', '\\')가 섞였거나
    값이 비었거나 `.`/`..` 면 파일명으로 신뢰하지 않고 None 을 반환한다 — basename
    으로 슬쩍 축약하지 않고 통째로 거부한다 (경로 탈출 방지, 요청 조건 6).

    파일명 매칭은 정확 일치(`Core-System.yaml`)가 우선이고, 없으면 소문자 일치
    (`core-system.yaml`)를 본다. 둘 다 없으면 정확 일치 경로를 반환한다 — 존재
    여부는 호출부가 검사한다.
    """
    if repo_name is None:
        repo_name = resolve_repo_name(repo_root)
    if not repo_name or "/" in repo_name or "\\" in repo_name or repo_name in (".", ".."):
        return None
    policy_dir = _policy_dir()
    exact = policy_dir / f"{repo_name}.yaml"
    if exact.is_file():
        return exact
    lowered = policy_dir / f"{repo_name.lower()}.yaml"
    if lowered.is_file():
        return lowered
    return exact


def _no_contract(reason, branch=None, plan_path=None, warnings=None):
    # policy_deny 는 compile_contract 가 모든 no_contract 에 덧붙인다(계획 계약이 없어도
    # 레포 정책 deny 만으로 deny_hit 를 판정하기 위함).
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


def _valid_policy_paths(candidates, repo_root, repo_name, rel_path):
    """정책 경로 후보를 규격 검증해 (유효 목록, 경고 목록) 으로 가른다."""
    valid = []
    warnings = []
    for p in candidates:
        try:
            validate_path(p, repo_name=repo_name, repo_root=repo_root)
        except PathValidationError as e:
            warnings.append(
                {
                    "code": "policy_invalid_path",
                    "message": f"레포 기본 정책 파일({rel_path})의 경로 무시됨: {e}",
                }
            )
        else:
            valid.append(p)
    return valid, warnings


def _load_repo_escalation_policy(repo_root, repo_name):
    """레포 기본 정책 파일을 읽는다 (docs/decisions/0001, 위치는 0003 개정).

    저작 스키마(계약 블록)와 같은 최소 YAML 형식을 그대로 재사용한다 — 새 파서를
    만들지 않는다 (결정문 필수 조건 1). `scope.allow` 목록은 escalation 대상
    경로로, `scope.deny` 목록은 계획 계약 유무와 무관한 deny 로 쓴다
    (docs/decisions/0004-policy-deny-without-contract.md). deny 만 있는 정책
    파일도 유효하다(`scope.allow` 는 이 파일에서 선택).

    정책 파일은 대상 레포가 아니라 drift-detect 소스 트리 안, 레포별로 존재한다
    (`resolve_policy_path`). 없으면 병합할 것이 없을 뿐이다 (fail-open).

    fail-open 이되 침묵하지 않는다 (스펙 §6) — 실패마다 사람이 고칠 수 있는
    경고를 네 번째 반환값에 남긴다. 개별 경로 무효는 그 경로만 버리고 나머지는
    살린다 (전부를 버리지 않는다). 반환: (allow: list[str]|None,
    deny: list[str], meta: dict|None, warnings: list[dict]).
    """
    policy_path = resolve_policy_path(repo_root, repo_name=repo_name)
    if policy_path is None or not policy_path.is_file():
        return None, [], None, []

    rel_path = str(policy_path)

    try:
        data = policy_path.read_bytes()
        text = data.decode("utf-8")
        parsed = parse_contract_yaml(text, require_allow=False)
    except (OSError, UnicodeDecodeError, ContractParseError) as e:
        return (
            None,
            [],
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
            [],
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

    allow_patterns = _valid_policy_paths(
        parsed["scope"]["allow"], repo_root, repo_name, rel_path
    )
    deny_patterns = _valid_policy_paths(
        parsed["scope"]["deny"], repo_root, repo_name, rel_path
    )
    warnings = allow_patterns[1] + deny_patterns[1]
    allow_patterns = allow_patterns[0]
    deny_patterns = deny_patterns[0]

    if not allow_patterns and not deny_patterns:
        return None, [], None, warnings

    meta = {
        "path": rel_path,
        "mtime": policy_path.stat().st_mtime,
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    return allow_patterns, deny_patterns, meta, warnings


def compile_contract(repo_root, branch=None, plan_path=None):
    """계약을 컴파일한다.

    반환: (contract: dict, meta: dict|None) 의 2-tuple.
    contract["status"] 는 "ok" 또는 "no_contract". 예외를 던지지 않는다.

    모든 반환 계약에 `policy_deny`(레포 정책의 deny 목록)가 실린다 — status 가
    no_contract 여도 deny_hit 는 이 목록만으로 판정된다. 계획 계약의 `scope.deny`
    와 합치는 것은 술어 쪽이다(predicates.deny_hit).
    """
    # 심볼릭 링크를 resolve() 하지 않는다 — 임시 디렉터리(예: macOS /var → /private/var)에서
    # repo_root 와 명시적으로 넘어온 plan_path 가 다른 표기로 어긋나 relative_to() 가
    # 깨지는 것을 피한다. repo_root 는 호출자가 이미 의도한 경로 그대로 신뢰한다.
    repo_root = Path(repo_root)
    repo_name = resolve_repo_name(repo_root)

    policy = _load_repo_escalation_policy(repo_root, repo_name)
    contract, meta = _compile_plan_contract(repo_root, repo_name, branch, plan_path, policy)

    contract["policy_deny"] = list(policy[1])
    if contract["status"] != "ok":
        # 계획 계약 경로가 정책 경고를 싣지 못하고 일찍 반환한 경우에도 흔적을 남긴다.
        for warning in policy[3]:
            if warning not in contract["warnings"]:
                contract["warnings"].append(warning)
    return contract, meta


def _compile_plan_contract(repo_root, repo_name, branch, plan_path, policy):
    """계획 문서 → 계약. 레포 정책은 호출자가 미리 읽어 넘긴다."""
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

    policy_patterns, _policy_deny, policy_meta, policy_warnings = policy
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
