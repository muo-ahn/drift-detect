"""경로 검증 + glob 매칭 (docs/spec/plan-contract-v1.md §4).

- 레포 루트 상대 POSIX 경로, 선행 `./` `/` 금지, 가짜 루트 금지
- `*` = 한 세그먼트, `**` = 0개 이상 세그먼트
- `deny` 가 `allow` 를 이긴다 (PathMatcher.is_allowed)

가짜 루트 탐지는 레포 이름 일치 하나에만 기대지 않는다. 레포 이름 일치는
worktree 등 레포 디렉터리 이름이 원본과 달라지는 환경(예: `.claude/worktrees/core-1701/`)에서
무력화되므로, 구조 기반 탐지(_check_structural_fake_root)를 함께 적용한다.
"""

import re
from pathlib import Path

_FAKE_ROOT_PLACEHOLDER = "[project_root]"

# 실측에서 나온 알려진 플레이스홀더 표기 (docs/spec/plan-contract-v1.md §4).
_KNOWN_PLACEHOLDERS = frozenset(
    {"[project_root]", "<project_root>", "{project_root}"}
)


class PathValidationError(Exception):
    """경로 규격 위반. 사람이 읽을 수 있는 진단 메시지를 담는다."""


def _looks_like_known_placeholder(segment):
    return segment.lower() in _KNOWN_PLACEHOLDERS


def _wildcard_free_prefix(path):
    """glob 와일드카드가 없는 선두 세그먼트들만 반환한다 (구조 기반 탐지용).

    와일드카드가 섞인 경로는 판정 근거가 되는 접두부만 남기고 그 뒤는 버린다.
    """
    prefix = []
    for seg in path.split("/"):
        if seg == "" or "*" in seg:
            break
        prefix.append(seg)
    return prefix


def _check_structural_fake_root(path, repo_root):
    """구조 기반 가짜 루트 탐지 (docs/spec/plan-contract-v1.md §4 결정).

    선두 세그먼트가 레포 루트에 실재하지 않는데, 그것을 뗀 나머지의 선두
    세그먼트는 레포 루트에 실재하면 가짜 루트로 본다. 레포 디렉터리 이름과
    무관하게 동작하므로 worktree 등에서 레포 이름이 바뀌어도 탐지가 유지된다.

    판단 근거(세그먼트 2개)가 부족하거나 파일시스템 접근이 불가하면
    거부하지 않는다 (fail-open — §6).
    """
    prefix = _wildcard_free_prefix(path)
    if len(prefix) < 2:
        return

    try:
        root = Path(repo_root)
        if not root.is_dir():
            return
        first_exists = (root / prefix[0]).exists()
        second_exists = (root / prefix[1]).exists()
    except OSError:
        return

    if not first_exists and second_exists:
        raise PathValidationError(
            f"가짜 루트 의심: {path!r} — 최상위 세그먼트 {prefix[0]!r} 는 레포 루트에 없고, "
            f"그 다음 세그먼트 {prefix[1]!r} 는 레포 루트에 실재함 "
            f"(예: '{prefix[0]}/{prefix[1]}/...' (X) → '{prefix[1]}/...' (O))"
        )


def validate_path(path, repo_name=None, repo_root=None):
    """레포 루트 상대 POSIX 경로 규격을 검증한다. 위반 시 PathValidationError.

    repo_name: 레포 디렉터리 이름(예: "Core-System"). 대소문자 무시로 "레포 이름을
    경로 앞에 붙이는" 가짜 루트를 탐지하는 데 쓴다.
    repo_root: 레포 루트 절대 경로. 주어지면 구조 기반 가짜 루트 탐지를 추가로
    수행한다 — repo_name 이 실제 디렉터리 이름과 다른 환경(worktree 등)에서도
    가짜 루트를 잡아낸다. 판단 불가 시 거부하지 않는다 (fail-open).
    """
    if path is None or not path.strip():
        raise PathValidationError("빈 경로")

    if "\\" in path:
        raise PathValidationError(f"경로 구분자는 '/' 여야 함 (POSIX): {path!r}")

    if path.startswith("/"):
        raise PathValidationError(f"선행 '/' 금지 (레포 루트 상대 경로여야 함): {path!r}")

    if path.startswith("./"):
        raise PathValidationError(f"선행 './' 금지 (레포 루트 상대 경로여야 함): {path!r}")

    first_segment = path.split("/", 1)[0]

    if _looks_like_known_placeholder(first_segment):
        raise PathValidationError(
            f"가짜 루트 금지: {path!r} — {first_segment!r} 같은 플레이스홀더를 붙이지 말고 "
            f"레포 루트 상대 경로로 쓸 것"
        )

    if repo_name and first_segment.lower() == repo_name.lower():
        raise PathValidationError(
            f"가짜 루트 금지: {path!r} — 레포 이름({repo_name})을 경로 앞에 붙이지 말 것 "
            f"(예: '{repo_name}/core/shop/**' (X) → 'core/shop/**' (O))"
        )

    if repo_root is not None:
        _check_structural_fake_root(path, repo_root)


def _compile_pattern(pattern):
    """pathspec 패턴을 정규식으로 변환한다. `*`=한 세그먼트, `**`=0개 이상 세그먼트."""
    segs = pattern.split("/")
    n = len(segs)
    pieces = []
    kinds = []  # "star2" | "normal"

    for i, seg in enumerate(segs):
        if seg == "**":
            if n == 1:
                pieces.append(".*")
            elif i == 0:
                pieces.append("(?:.*/)?")
            elif i == n - 1:
                pieces.append("(?:/.*)?")
            else:
                pieces.append("/(?:.*/)?")
            kinds.append("star2")
        else:
            piece = "[^/]+" if seg == "*" else re.escape(seg)
            pieces.append(piece)
            kinds.append("normal")

    out = []
    for i, (piece, kind) in enumerate(zip(pieces, kinds)):
        # ** 조각은 자신의 슬래시를 이미 포함하므로, 정상-정상 인접에서만 '/' 를 끼워 넣는다.
        if i > 0 and kinds[i - 1] == "normal" and kind == "normal":
            out.append("/")
        out.append(piece)

    return re.compile("^" + "".join(out) + "$")


class PathMatcher:
    """allow/deny pathspec 리스트를 컴파일해 매칭한다. deny 가 allow 를 이긴다."""

    def __init__(self, allow_patterns, deny_patterns=None):
        self._allow = [_compile_pattern(p) for p in allow_patterns]
        self._deny = [_compile_pattern(p) for p in (deny_patterns or [])]

    def is_allowed(self, path):
        if any(p.match(path) for p in self._deny):
            return False
        return any(p.match(path) for p in self._allow)


def matches_any(path, patterns):
    """path 가 patterns(pathspec 문자열 리스트) 중 하나에라도 매치하면 True.

    M1 술어 엔진(predicates.py)·시뮬레이터(simulate.py)가 공유하는 매칭 지점을
    하나로 모은다 — 각자 정규식을 다시 컴파일하면 이 모듈의 pathspec 의미론과
    조용히 어긋날 수 있다 (M0 에서 이미 한 번 겪은 종류의 drift).
    """
    return any(_compile_pattern(p).match(path) for p in patterns)
