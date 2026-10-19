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
- `scope.deny` 는 이 파일에서 의미가 없다. 쓰여 있어도 무시된다.
- 개별 경로가 [pathspec 규격](../docs/spec/plan-contract-v1.md)을 어기면(예:
  선행 `/`, 가짜 루트) 그 경로만 버려지고 `policy_invalid_path` 경고가 남는다.
  나머지 유효한 경로는 살아남는다.
- 유효한 경로가 하나도 없으면(빈 파일, 전부 무효 등) 정책 자체가 없는 것으로
  취급된다.

## 언제 만드나

지금은 만들지 않는다. 초기 목록은 M1 그림자 모드 실측 후 결정한다
([0001] 필수 구현 조건 7). 이 디렉터리와 README 만 M0 단계 산출물이다.
