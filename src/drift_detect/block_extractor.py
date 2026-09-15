"""마크다운에서 `yaml contract` 펜스 블록을 추출한다.

- 블록이 0개 → 계약 없음 (에러 아님, 빈 리스트 반환)
- 블록이 2개 이상 → 파싱 오류 (호출자가 "계약 없음"으로 폴백시킨다)
- 미종료 펜스(닫는 줄이 끝까지 없음) → BlockExtractionError

fence info string 은 스펙(§2)대로 `yaml contract` 정확히 일치할 때만
계약 블록으로 취급한다. 다른 info string 을 가진 코드 펜스(문서 안의
예시 블록 등)는 내용을 건너뛰되, 그 안에 "```" 가 더 있어도 최상위
파서 상태를 어지럽히지 않도록 여는/닫는 펜스 길이를 추적한다.
"""

import re

_FENCE_OPEN_RE = re.compile(r"^([`~]{3,})(.*)$")
_FENCE_CLOSE_RE = re.compile(r"^([`~]{3,})\s*$")

_CONTRACT_INFO = "yaml contract"


class BlockExtractionError(Exception):
    """펜스 블록 추출 중 구조적 오류 (예: 미종료 펜스)."""


def extract_contract_blocks(text):
    """텍스트에서 `yaml contract` 최상위 펜스 블록들의 본문을 리스트로 반환한다."""
    blocks = []
    open_fence = None  # {"char", "length", "is_contract", "body"}

    for line in text.splitlines():
        if open_fence is None:
            m = _FENCE_OPEN_RE.match(line.strip())
            if not m:
                continue
            fence_chars = m.group(1)
            info = m.group(2).strip()
            open_fence = {
                "char": fence_chars[0],
                "length": len(fence_chars),
                "is_contract": info == _CONTRACT_INFO,
                "body": [],
            }
            continue

        stripped = line.strip()
        close_m = _FENCE_CLOSE_RE.match(stripped)
        if (
            close_m
            and close_m.group(1)[0] == open_fence["char"]
            and len(close_m.group(1)) >= open_fence["length"]
        ):
            if open_fence["is_contract"]:
                blocks.append("\n".join(open_fence["body"]))
            open_fence = None
            continue

        if open_fence["is_contract"]:
            open_fence["body"].append(line)

    if open_fence is not None:
        raise BlockExtractionError("미종료 코드 펜스 (닫는 펜스를 찾지 못함)")

    return blocks
