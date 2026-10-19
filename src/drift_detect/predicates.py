"""M1 술어 엔진 — C2 (design.md §5). 정확히 4개, 늘리지 않는다.

| 코드 | 조건 | 계약 필요 |
|---|---|---|
| allow_miss | Edit/Write 대상이 scope.allow(+implicit_allow) 밖 | 예 |
| deny_hit | 대상이 scope.deny 에 매치 | 예 |
| escalate_hit | 대상이 escalate_paths 에 매치 | 예 |
| test_unverified | worktree 테스트 명령이 shim/플래그로 정상 라우팅되지 않음 | 아니오 |

모든 술어는 순수 함수다 — 입력을 받아 판정(dict 또는 None)을 반환할 뿐,
파일을 쓰거나 프로세스 종료 코드를 바꾸지 않는다. 부작용은 observer.py 의 몫.

계약 의존 3개는 contract 가 없거나 status != "ok" 이면 항상 None(무판정)이다 —
무계약 상태에서 Tier 0 판정을 만들지 않는다(design.md §4, §6: 무계약 비차단
불변식).
"""

import os
import re

from drift_detect.pathspec import matches_any

# worktree 안에서 실행되는 테스트 명령의 표지 (대상 레포 CLAUDE.md
# "worktree 에서의 테스트 실행 (무음 오검증 차단)" 절 근거).
_MAKE_TEST_HINT = re.compile(r"\bmake\s+test-fast\b")
_MANAGE_TEST_HINT = re.compile(r"\bmanage\.py\s+test\b")
# cwd 또는 명령 문자열에 worktree 경로가 보이는가 (`.claude/worktrees/` 등).
_WORKTREE_HINT = re.compile(r"/(?:\.claude/)?worktrees/")
_WORKER_FLAG = re.compile(r"-w\s+/code/worktrees/")
_DB_SCHEMA_FLAG = re.compile(r"DB_SCHEMA=core_wt_")
# 대상 레포 CLAUDE.md 가 규정하는 shim 파일명 — GNU make 는 Makefile 보다
# GNUmakefile 을 먼저 읽으므로, 이 파일이 worktree 최상위에 있으면 메인 트리
# Makefile 을 include 해 -w/DB_SCHEMA 를 내부에서 조립한다.
_SHIM_FILENAME = "GNUmakefile"

_PREDICATE_ORDER = ("deny_hit", "escalate_hit", "allow_miss")


def _has_contract(contract):
    return bool(contract) and contract.get("status") == "ok"


def allow_miss(target, contract):
    """target 이 scope.allow(명시)+implicit_allow 어디에도 매치하지 않는가."""
    if not _has_contract(contract):
        return None
    patterns = contract["scope"]["allow"] + contract["implicit_allow"]
    if matches_any(target, patterns):
        return None
    return {"code": "allow_miss", "matched": None}


def deny_hit(target, contract):
    """target 이 scope.deny 의 어느 패턴에 매치하는가."""
    if not _has_contract(contract):
        return None
    for pattern in contract["scope"]["deny"]:
        if matches_any(target, [pattern]):
            return {"code": "deny_hit", "matched": pattern}
    return None


def escalate_hit(target, contract):
    """target 이 escalate_paths(레포 기본 정책 병합분) 의 어느 패턴에 매치하는가."""
    if not _has_contract(contract):
        return None
    for entry in contract["escalate_paths"]:
        if matches_any(target, [entry["pattern"]]):
            return {"code": "escalate_hit", "matched": entry["pattern"]}
    return None


def _make_shim_present(cwd):
    """cwd 최상위에 GNUmakefile shim 이 있는가.

    반환: True(있음) / False(없음, 정상적으로 확인됨) / None(확인 불가 —
    cwd 미제공, 권한 오류 등 파일시스템 접근 실패). 호출부는 None 을
    "판정 불가"로 취급해 발화하지 않는다(fail-open) — shim 부재를 단정할
    근거가 없는 상태에서 오탐을 만들지 않기 위함이다.
    """
    if not cwd:
        return None
    try:
        return os.path.isfile(os.path.join(cwd, _SHIM_FILENAME))
    except (OSError, ValueError):
        return None


def test_unverified(command, cwd=None):
    """worktree 안에서 도는 테스트 명령이 정상 경로로 라우팅되지 않는가.

    계약이 필요 없다 — 계약 유무와 무관하게 항상 평가된다.

    실측 결과와 수정 근거 (호스트 사전 실측, 설치 전):
    정상 사용 8건 중 오탐 1건 — `make test-fast ARGS="shop.tests"` (worktree,
    GNUmakefile shim 있음) 가 test_unverified 로 오판정됐다. `-w`/`DB_SCHEMA`
    는 Make 내부에서 조립되므로 명령줄에 보이지 않는 것이 정상이며, 이를
    "플래그 부재"로 잡으면 정탐은 거의 0·오탐은 거의 100%가 된다. worktree
    39개 전부(39/39)에 `.claude/hooks/worktree_make_shim.sh` (PreToolUse) 가
    매 Bash 호출마다 자동 복구하는 GNUmakefile shim 이 존재함을 확인했다
    (대상 레포 CLAUDE.md "worktree 에서의 테스트 실행 (무음 오검증 차단)" 절).

    그래서 판정 기준을 "명령줄에 플래그가 있는가" 에서 "이 명령이 올바른
    경로로 라우팅되는가" 로 바꿨다. `make test-fast` 계열은 cwd 에
    GNUmakefile shim 이 있으면 clean(shim 이 메인 트리 Makefile 을 include
    해 -w/DB_SCHEMA 를 붙여준다), 없으면 구 Makefile 이 메인 레포를 대신
    테스트하는 진짜 무음 오검증이므로 발화한다. `docker exec` 등으로
    manage.py test 를 직접 호출해 shim 자체를 우회하는 경로는 종전대로
    명령줄의 `-w`/`DB_SCHEMA=` 존재 여부로 판단한다.
    """
    if not command:
        return None

    is_make_test = bool(_MAKE_TEST_HINT.search(command))
    is_manage_test = bool(_MANAGE_TEST_HINT.search(command))
    if not (is_make_test or is_manage_test):
        return None

    in_worktree = bool(_WORKTREE_HINT.search(cwd or "")) or bool(
        _WORKTREE_HINT.search(command)
    )
    if not in_worktree:
        return None

    if is_make_test:
        shim_present = _make_shim_present(cwd)
        if shim_present is None or shim_present:
            return None
        return {"code": "test_unverified", "matched": "worktree_make_missing_shim"}

    if _WORKER_FLAG.search(command) and _DB_SCHEMA_FLAG.search(command):
        return None
    return {"code": "test_unverified", "matched": "worktree_test_missing_flags"}


_FUNCS = {
    "deny_hit": deny_hit,
    "escalate_hit": escalate_hit,
    "allow_miss": allow_miss,
}


def evaluate(target, contract, command=None, cwd=None):
    """단일 이벤트에 대해 우선순위 순으로 술어를 돌려 첫 매치를 반환한다.

    우선순위: deny_hit > escalate_hit > allow_miss > test_unverified > clean.
    계약 의존 판정이 test_unverified 보다 우선한다 — 계약 위반이 더 확정적인
    신호이기 때문이다(design.md §2 오탐/미탐 비대칭: 확실한 것부터 본다).

    target 이 있으면 계약 의존 3개만 평가한다(파일 경로 술어이므로).
    target 이 없으면(예: Bash) test_unverified 만 평가한다.
    반환: {"code": str, "matched": str|None}. 아무것도 안 걸리면
    {"code": "clean", "matched": None}.
    """
    if target:
        for code in _PREDICATE_ORDER:
            result = _FUNCS[code](target, contract)
            if result is not None:
                return result
        return {"code": "clean", "matched": None}

    result = test_unverified(command, cwd=cwd)
    if result is not None:
        return result
    return {"code": "clean", "matched": None}
