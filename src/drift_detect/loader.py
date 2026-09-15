"""C1(컴파일러)·C2(술어 엔진) 진입점이 공유하는 sys.path 부트스트랩.

drift-detect 는 pip 설치되지 않고 외부 절대경로로 참조된다
(docs/decisions/0002-m1-deployment.md, M1 = A안). hook 진입점(observer.py)이
`python3 <절대경로>/observer.py` 로 직접 실행되면, 파이썬은 스크립트가 있는
디렉터리(`.../src/drift_detect`)만 sys.path 에 넣는다 — `drift_detect` 패키지
자체(그 부모 `.../src`)는 아직 import 할 수 없다. 그 간극을 메우는 것이 이
모듈의 유일한 책임이다.

이 파일은 의도적으로 drift_detect 패키지의 다른 모듈을 import 하지 않는다
(stdlib only) — 그래야 `drift_detect` 가 아직 import 불가능한 시점에도 이
모듈 자체는 (같은 디렉터리에 있는 sibling 파일로서) bare import 로 먼저
로드될 수 있다.
"""

import os
import sys
from pathlib import Path

# 하드코딩 지점 — 절대경로는 이 파일 한 곳에만 존재한다
# (docs/decisions/0002-m1-deployment.md 필수 구현 조건 2).
# 팀 배포 시(다른 사용자의 clone 위치가 다르면) 재검토 대상이다.
# DRIFT_DETECT_SRC 환경변수로 덮어쓸 수 있다.
_DEFAULT_SRC = "/Users/saladlab/Dev/drift-detect/src"


def ensure_on_path():
    """`drift_detect` 패키지를 import 가능한 상태로 만든다.

    성공하면 True, 경로가 없거나 유효한 패키지가 아니면 False.
    예외를 던지지 않는다 — 호출자는 항상 "계약 없음/무동작" 폴백으로
    이어져야 한다 (0002 필수 구현 조건 1과 같은 정신).
    """
    try:
        src = os.environ.get("DRIFT_DETECT_SRC", _DEFAULT_SRC)
        src_path = Path(src)
        if not (src_path / "drift_detect" / "__init__.py").is_file():
            return False
        src_str = str(src_path)
        if src_str not in sys.path:
            sys.path.insert(0, src_str)
        return True
    except Exception:
        return False
