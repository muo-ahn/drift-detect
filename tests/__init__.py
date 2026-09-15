"""테스트 부트스트랩: src/ 를 sys.path 에 올린다.

`python -m unittest discover` 가 tests 패키지를 임포트할 때 이 __init__ 이
먼저 실행되므로, 각 테스트 모듈은 별도 경로 조작 없이 `from drift_detect...`
를 그대로 최상단 import 로 쓸 수 있다.
"""

import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
