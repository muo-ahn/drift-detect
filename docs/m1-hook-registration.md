# M1 hook 등록 — 사용자 레벨 `~/.claude/settings.json` 스니펫

> 이 파일은 **문서**다. `~/.claude/settings.json` 을 직접 수정하지 않는다
> (M1 구현 범위의 금지 사항). 등록은 사용자가 아래 조각을 자신의
> `~/.claude/settings.json` 의 `hooks.PostToolUse` 배열에 직접 병합한다.

## 왜 사용자 레벨인가

`docs/decisions/0002-m1-deployment.md` §M1 종료 근거. 프로젝트 레벨
`Core-System/.claude/settings.json` 은 gitignore 되어 있어 `git worktree add`
로 만든 checkout 에는 애초에 존재하지 않는다 — 거기 등록하면 대다수
worktree 에서 관측이 전혀 발화하지 않은 채 "데이터 없음"을 "오탐 0"으로
오독하게 된다.

## 등록 조각

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "/usr/bin/python3 /Users/saladlab/Dev/drift-detect/src/drift_detect/observer.py"
          }
        ]
      }
    ]
  }
}
```

- `matcher` 는 관측기가 실제로 판정 가능한 5개 도구로 제한한다. observer.py
  자체도 이 목록으로 한 번 더 필터링하므로(방어적 이중화), matcher 설정을
  깜빡해도 노이즈만 늘 뿐 오동작하지는 않는다.
- `command` 의 인터프리터는 반드시 `/usr/bin/python3`(시스템, 3.9.6)이어야
  한다 — pyenv/venv 등 다른 인터프리터를 쓰면 사용자별 site-packages 오염
  경로를 배제한다는 0002 의 전제가 깨진다.
- drift-detect 클론 위치가 다르면 `command` 의 절대경로와
  `src/drift_detect/loader.py` 의 `_DEFAULT_SRC` 를 함께 바꾸거나, 매번
  `DRIFT_DETECT_SRC` 환경변수로 덮어쓴다 (hook 의 `env` 필드 또는 셸 wrapper
  에서 설정).

## 확인

등록 후 아무 Edit/Write/Bash 를 한 번 실행하고, 그 저장소에서

```bash
tail -n 5 <repo>/.claude/state/audit/log.jsonl
```

한 줄이 새로 생겼는지 확인한다. 아무 일도 안 일어나면(무음) drift-detect
경로를 못 찾았거나 git 컨텍스트 판별에 실패한 것이다 — 둘 다 fail-open 이므로
세션은 정상 진행되지만, 관측 데이터가 비어 M1 의 유일한 산출물(오탐률 실측)이
비게 된다.
