"""암묵 allow (docs/spec/plan-contract-v1.md §4) — 선언 없이도 항상 허용되는 경로."""


def implicit_allow_patterns(branch):
    """현재 브랜치 기준 암묵 allow pathspec 리스트를 반환한다.

    브랜치명의 '/' 는 documents/tasks/ 하위 디렉터리 중첩으로 그대로 반영된다
    (예: 'feat/a/b' → 'documents/tasks/feat/a/b/**').

    테스트 파일은 암묵 allow에 넣지 않는다 (스펙 §4 — TDD 상 "어느 테스트를
    쓸 것인가"는 계획이 답해야 할 질문이라 의도적으로 제외).
    """
    return [
        f"documents/tasks/{branch}/**",
        "**/migrations/**",
    ]
