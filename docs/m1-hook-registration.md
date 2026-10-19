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

---

## M1 종료 판정 — 로그 읽는 법

2주 뒤 이 명령으로 집계한다. **`PYTHONPATH` 가 필요하다** (패키징하지 않았다).

```bash
cd ~/Dev/drift-detect
PYTHONPATH=src /usr/bin/python3 -m drift_detect.report \
  --repo /Users/saladlab/Dev/Alpha-Apps-Dev/Core-System
```

옵션: `--since <일수>` · `--top <N>` · `--json` · `--repo` 복수 지정 가능.

### 판정 순서 — 반드시 이 순서로 읽어라

1. **`has_contract:true` 관측 건수**를 먼저 본다.
   **0이면 경고 배너가 뜨고, 그 아래 수치는 종료 판정 근거가 될 수 없다.**
   계약이 달린 계획이 없었다는 뜻이지 오탐이 없었다는 뜻이 아니다
   (`design.md` §8 M1 「데이터 부재 위험」).
   → 기간을 연장하거나 `simulate.py` 로 과거 브랜치를 소급 재생해 보충한다.

2. **「계약 있는 작업에서의 술어 발화율」** 섹션을 본다. 전체 기준 비율이 아니다.
   계약 없는 관측이 분모에 섞이면 발화율이 희석된다.

3. **술어별 상위 경로**로 노이즈원을 특정한다. 한 경로가 발화를 독식하면
   그 술어를 고칠지 계약 작성 지침을 고칠지 판단한다.

4. **일자별 추이**로 경보 예산(사람 도달 ≤3회/일, `design.md` §3)을 넘겼는지 본다.

### M2 승격 조건

`design.md` §8 M2 — M1 에서 **오탐 0 으로 확인된 술어만** 차단으로 승격한다.
나머지는 그림자 모드를 유지한다. 전부 한꺼번에 켜지 않는다.
