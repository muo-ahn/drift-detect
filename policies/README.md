# 레포 기본 escalation 정책

배경: [docs/decisions/0001-repo-escalation-paths.md](../docs/decisions/0001-repo-escalation-paths.md),
위치 개정: [docs/decisions/0003-zero-footprint.md](../docs/decisions/0003-zero-footprint.md).

대상 레포의 계약 블록과 무관하게, 특정 레포에서 항상 escalation 소집 대상으로
취급할 경로를 여기서 레포별로 선언한다. **대상 레포에는 아무 파일도 만들지
않는다** — 이 디렉터리 자체가 그 목적이다.

## 파일명 규칙

```
policies/<repo basename>.yaml
```

`<repo basename>` 은 컴파일 대상 레포 디렉터리 이름 그대로다 (예: 레포가
`/Users/saladlab/Dev/Alpha-Apps-Dev/Core-System` 이면 `policies/Core-System.yaml`).
`compiler.resolve_policy_path()` 가 이 규칙으로 경로를 계산한다.

- **worktree**: `.git` 이 `gitdir: <main>/.git/worktrees/<name>` 파일이면 메인
  레포의 basename 을 쓴다 (`.claude/worktrees/core-1788` 에서도 `Core-System.yaml`).
  파싱에 실패하면 종전대로 디렉터리 이름이다.
- **대소문자**: 정확 일치(`Core-System.yaml`)를 먼저 보고, 없으면 소문자 일치
  (`core-system.yaml`)를 본다.

정책 디렉터리 자체의 기본 위치는 이 파일이 있는 곳(`<drift-detect>/policies`)이며,
`DRIFT_DETECT_POLICY_DIR` 환경변수로 통째로 덮어쓸 수 있다 (테스트·이식성 용도).

## 파일이 없으면

병합할 것이 없을 뿐이다 — fail-open. 에러도 경고도 아니다.

## 형식

계약 블록(`documents/templates/task/implementation-plan.md` 의 「작업 범위
(기계 판독)」 절)과 같은 최소 YAML 서브셋을 그대로 재사용한다. 별도 파서가
없다 — 새 형식을 만들지 않는다는 것이 [0001] 필수 조건 1의 의도다.

```yaml
contract_version: 1
scope:
  allow:
    - core/common/**
    - core/core/config/**
```

- `contract_version` 은 현재 `1` 만 지원한다. 다르면 `policy_unsupported_version`
  경고와 함께 무시된다 (fail-open).
- `scope.allow` 의 각 경로가 escalation 대상이 된다. 컴파일된 `contract.json` 의
  `escalate_paths` 에 `{"pattern": <path>, "source": "repo_default"}` 형태로
  병합된다 — `deny` 에는 병합되지 않는다. 차단이 아니라 판정 소집이기 때문이다.
- `scope.deny` 는 **계획 계약 유무와 무관한 deny** 다
  ([0004](../docs/decisions/0004-policy-deny-without-contract.md)). 형식은 계약과
  같은 glob(`*` 한 세그먼트, `**` 0개 이상)이다. 계획 계약이 있으면 계약의
  `scope.deny` 와 합집합이고(deny 가 allow 를 이긴다), 계약이 없으면 이 목록만으로
  `deny_hit` 를 판정한다 (`allow_miss`·`escalate_hit` 는 여전히 계약 필요).
  `scope.allow` 는 선택이다 — deny 만 있는 파일도 유효하다.
- 개별 경로가 [pathspec 규격](../docs/spec/plan-contract-v1.md)을 어기면(예:
  선행 `/`, 가짜 루트) 그 경로만 버려지고 `policy_invalid_path` 경고가 남는다.
  나머지 유효한 경로는 살아남는다.
- 유효한 경로가 하나도 없으면(빈 파일, 전부 무효 등) 정책 자체가 없는 것으로
  취급된다.

## deny 예시

```yaml
contract_version: 1
scope:
  deny:
    - infra/prod/**
    - "**/migrations/versions/*.lock"
```

감사 로그에서 정책 deny 로만 나온 `deny_hit` 는 `policy_deny: true`,
`contract_source: policy`(계획 계약 없음) 로 구분되고, `report` 에 별도 줄로 집계된다.

## 언제 만드나

첫 정책 `core-system.yaml` 을 2026-10-19 에 만들었다 (deny 전용: infra, 설정,
Dockerfile·compose, migration·workflows, .github). 근거는 M1 실측이다 — 과거
Edit/Write 2,512건 중 발화 예상은 3건(`core/core/config/**`)이었다. 다른 레포의
정책은 그 레포의 M1 실측 후 결정한다 ([0001] 필수 구현 조건 7).
