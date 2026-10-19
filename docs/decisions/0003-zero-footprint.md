# 0003 — 대상 레포에 아무것도 커밋하지 않는다

- 상태: **확정** (2026-09-15)
- 계기: 사용자 요구 — "회사 작업 레포에는 AI Native 관련 작업을 커밋하고 싶지 않다"
- 관련: [0001](0001-repo-escalation-paths.md) 필수 조건 1 **개정**, [0002](0002-m1-deployment.md)

---

## 결정

drift-detect 는 대상 레포에 **git 추적되는 파일을 하나도 만들지 않는다.**

이는 도구의 성격에서도 옳다 — 개인 하네스이지 팀 제품이 아니다.
팀이 채택하기로 결정하기 전에는 팀 레포 이력에 남을 이유가 없다.

## 실측 — 원래도 거의 제로였다

| 산출물 | 대상 레포에서의 상태 |
|---|---|
| 계약 블록이 실제로 들어가는 `documents/tasks/{branch}/implementation-plan.md` | **무시됨** (`.gitignore:179` `**/documents/tasks/*`) |
| 관측 로그 `.claude/state/audit/log.jsonl` | **무시됨** (`.gitignore:251`, submodule `info/exclude:26`) |
| 레포 정책 `.claude/policy/repo-escalation.yaml` | 미추적 |
| **계획 템플릿 `documents/templates/task/implementation-plan.md`** | **추적중 — 유일한 커밋 대상이었다** |

즉 제로 풋프린트를 위해 되돌릴 것은 **템플릿 하나뿐**이다.

## 변경 사항

### 1. 계획 템플릿을 원복한다

`documents/templates/task/implementation-plan.md` 의 「작업 범위 (기계 판독)」 절을 되돌린다.
`feature/drift-contract-block` 브랜치는 폐기한다.

**대신 계약 블록 작성 지침은 개인 전역 지시문(`~/.claude/CLAUDE.md`)에 둔다.**
계획을 쓰는 주체는 세션 에이전트이고, 그 에이전트는 전역 지시문을 읽는다.
팀 레포의 템플릿을 고칠 필요가 없다.

### 2. [0001] 필수 조건 1 개정

> 원문: 레포 기본값은 **git 추적되는 별도 정책 파일**로 두고 …

이 조건의 목적은 **정책이 검토·버전 관리되는 것**이었지 *대상 레포에* 있는 것이 아니었다.
목적을 유지하면서 위치만 옮긴다.

**개정**: 레포 기본 escalation 정책은 **drift-detect 레포 안에 레포별로** 둔다.

```
drift-detect/policies/<repo-name>.yaml
```

- 버전 관리·리뷰 가능성은 그대로 유지된다 (오히려 drift-detect 의 ADR 옆에 놓여 맥락이 붙는다)
- 대상 레포에는 아무것도 남지 않는다
- 컴파일러는 `<drift-detect>/policies/<repo basename>.yaml` 을 찾는다.
  없으면 병합할 것이 없을 뿐이다 (fail-open 불변)
- `contract.meta.json` 의 정책 mtime·해시 추적 조건은 그대로 유효하다

### 3. 영향 없는 것

`.claude/state/` 는 이미 `.gitignore` 와 submodule `info/exclude` 양쪽으로 무시된다.
관측 로그는 처음부터 커밋 대상이 아니었다.

## 대가

**팀원은 이 체계의 존재를 알 수 없다.** 의도된 결과다 —
M2 승격(2주 실측 후 집행 전환) 이전에는 1인 실험이고,
팀 도입은 별도 결정이며 그때 다시 논의한다.

부수 효과로 `0002` 의 M2 승격안(H안: 대상 레포에 벤더링)이 **이 결정과 충돌한다.**
M2 시점에 재검토한다 — 팀 도입이 함께 결정되지 않는 한 H안은 채택할 수 없고,
그 경우 A안(외부 절대경로)을 유지하거나 다른 방식을 찾아야 한다.
