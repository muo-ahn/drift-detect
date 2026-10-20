"""contract 블록 전용 최소 YAML 파서 (범용 YAML 파서 아님).

허용 스키마 (plan-contract v1, docs/spec/plan-contract-v1.md §3):

    contract_version: <int>
    scope:
      allow:
        - <path>
      deny:
        - <path>

이 형태를 벗어나면 ContractParseError. `contract_version` 이후 최상위의 `scope`
이외 키, 그리고 `scope` 아래 `allow`/`deny` 이외의 키는 위치(스칼라/리스트/중첩
매핑, `scope` 앞/뒤 어디든)와 무관하게 조용히 무시한다 (전방 호환 — 스펙 §3,
v1 파서는 모르는 키를 무시).
"""

import re

_VERSION_RE = re.compile(r"^contract_version:\s*(-?\d+)\s*$")
_KEY_RE = re.compile(r"^(allow|deny):\s*$")
_ITEM_RE = re.compile(r"^-\s+(\S.*)$")


class ContractParseError(Exception):
    """계약 블록이 v1 스키마를 벗어남 (사람이 읽을 수 있는 메시지 포함)."""


def _indented_lines(text):
    """빈 줄을 제거하고 (들여쓰기 칸 수, 내용) 쌍의 리스트로 반환한다."""
    result = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        result.append((indent, raw.strip()))
    return result


def parse_contract_yaml(text, require_allow=True):
    """contract 블록 본문 문자열을 파싱해 dict 로 반환한다.

    require_allow=False 는 레포 정책 파일용이다 — deny 만 있는 파일을 허용한다.

    반환: {"contract_version": int, "scope": {"allow": [str, ...], "deny": [str, ...]}}
    """
    lines = _indented_lines(text)
    if not lines:
        raise ContractParseError("빈 계약 블록")

    idx = 0
    indent, line = lines[idx]
    version_m = _VERSION_RE.match(line)
    if indent != 0 or not version_m:
        raise ContractParseError(
            "contract_version 필드가 없거나 형식이 올바르지 않음 (예: 'contract_version: 1')"
        )
    contract_version = int(version_m.group(1))
    idx += 1

    # scope: 를 찾을 때까지 최상위 미지 키를 건너뛴다 (전방 호환 — 스펙 §3).
    # 스칼라/리스트/중첩 매핑 어느 형태든, scope 보다 앞이든 뒤든 무시 대상이다.
    found_scope = False
    while idx < len(lines):
        indent, line = lines[idx]
        if indent != 0:
            raise ContractParseError(f"최상위 들여쓰기가 올바르지 않음: {line!r}")
        if line == "scope:":
            found_scope = True
            idx += 1
            break
        # 미지 최상위 키 — 이 줄과 그 자식 줄(들여쓰기 > 0)을 함께 건너뛴다.
        idx += 1
        while idx < len(lines) and lines[idx][0] > 0:
            idx += 1

    if not found_scope:
        raise ContractParseError("scope 필드가 없거나 형식이 올바르지 않음 (예: 'scope:')")

    scope_indent = None
    allow_items = None
    deny_items = []

    while idx < len(lines):
        indent, line = lines[idx]
        if indent <= 0:
            break
        if scope_indent is None:
            scope_indent = indent
        if indent != scope_indent:
            raise ContractParseError(f"scope 하위 들여쓰기가 일관되지 않음: {line!r}")

        key_m = _KEY_RE.match(line)
        if not key_m:
            # 미지 키 — 전방 호환을 위해 조용히 건너뛴다 (자식 라인도 함께 스킵).
            idx += 1
            while idx < len(lines) and lines[idx][0] > indent:
                idx += 1
            continue

        key = key_m.group(1)
        idx += 1
        items = []
        while idx < len(lines) and lines[idx][0] > indent:
            _item_indent, item_line = lines[idx]
            item_m = _ITEM_RE.match(item_line)
            if not item_m:
                raise ContractParseError(
                    f"{key} 항목 형식 오류 ('- <path>' 형태여야 함): {item_line!r}"
                )
            items.append(item_m.group(1).strip())
            idx += 1

        if key == "allow":
            allow_items = items
        else:
            deny_items = items

    if require_allow and (allow_items is None or len(allow_items) == 0):
        raise ContractParseError("scope.allow 는 1개 이상 필요")

    return {
        "contract_version": contract_version,
        "scope": {"allow": allow_items or [], "deny": deny_items},
    }
