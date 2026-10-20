# 0004 — 계획 계약이 없어도 레포 정책 deny 로 deny_hit 판정

상태: 채택 (2026-10-19). 관련: [0001](0001-repo-escalation-paths.md), [0003](0003-zero-footprint.md)

## 문제

`deny_hit`·`allow_miss`·`escalate_hit` 가 모두 계획 계약(status ok)을 요구했다.
계약 없는 작업(M1 실측에서 다수)에서는 사람이 승인 없이 건드리면 안 되는 경로도
무판정이었다. 또 레포 이름을 `repo_root` basename 으로 잡아, worktree
(`.claude/worktrees/core-1788`)에서는 `policies/<repo>.yaml` 을 찾지 못했다.

## 결정

1. 레포 식별: worktree 의 `.git` 파일(`gitdir: <main>/.git/worktrees/<name>`)을 파싱해
   메인 레포 basename 을 쓴다. subprocess 없음. 실패 시 종전 동작.
2. 정책 파일명: 정확 일치 우선, 없으면 소문자 일치.
3. 정책 `scope.deny` 를 지원한다. 계약 있음 → 계약 deny 와 합집합. 계약 없음 →
   정책 deny 만으로 `deny_hit`. `allow_miss`·`escalate_hit` 는 작업별 범위가 없으므로
   계약 없으면 계속 무판정.
4. 로그: `has_contract` 의미(계획 계약 존재)는 그대로. `contract_source`
   (`plan`|`policy`|`none`) 와 `policy_deny`(deny_hit 가 정책 deny 에서 나왔는가)를 추가.
   `report` 는 `policy_deny:true` 인 `deny_hit` 를 별도 줄로 센다.

무계약 비차단 불변식은 유지된다 — M1 은 기록만 한다. 정책 파일이 없으면 동작은 종전과 같다.
