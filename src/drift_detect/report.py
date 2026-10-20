"""M1 종료 게이트 집계 도구 (design.md §3 성공 기준, §8 M1).

`.claude/state/audit/log.jsonl` (observer.py C2 가 append 하는 판정 로그)을 읽어
술어 발화율·경로별 노이즈·일자별 추이·브랜치별 요약을 낸다.

**읽기 전용이다.** 판정도 차단도 수정도 하지 않는다 — 로그 파일에 쓰지 않고,
회전시키지 않는다.

가장 중요한 출력은 수치가 아니라 경고다: design.md §8 M1 「데이터 부재 위험」에
따르면 `has_contract:true` 관측이 0건이면 그 상태는 "오탐 0" 과 구분되지 않는다.
이 도구는 그 경고를 다른 모든 집계보다 먼저, 크게 낸다.
"""

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

_LOG_RELATIVE_PATH = (".claude", "state", "audit", "log.jsonl")
_PREDICATE_CODES = (
    "allow_miss",
    "deny_hit",
    "escalate_hit",
    "test_unverified",
    "clean",
)
_DEFAULT_TOP_N = 10
_SEC_PER_DAY = 86400.0


def _log_path(repo_root):
    return Path(repo_root).joinpath(*_LOG_RELATIVE_PATH)


def load_records(repo_root, since_days=None):
    """log.jsonl 한 파일을 읽어 유효 레코드만 걸러 반환한다.

    반환: (records, malformed_count, log_exists). malformed 줄(비 JSON, JSON
    이지만 object 가 아님)은 건너뛰되 개수는 보고한다 — 조용히 삼키면 "로그가
    비었다"와 "로그가 깨졌다"를 구분할 수 없다.
    """
    log_path = _log_path(repo_root)
    if not log_path.is_file():
        return [], 0, False

    cutoff = None
    if since_days is not None:
        cutoff = time.time() - since_days * _SEC_PER_DAY

    records = []
    malformed = 0
    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(record, dict):
                malformed += 1
                continue
            if cutoff is not None:
                ts = record.get("ts")
                if not isinstance(ts, (int, float)) or ts < cutoff:
                    continue
            records.append(record)
    return records, malformed, True


def aggregate(records):
    """레코드 리스트에서 집계 지표를 뽑는 순수 함수 — I/O 없음."""
    total = len(records)
    with_contract = [r for r in records if r.get("has_contract") is True]

    verdict_counts = Counter(r.get("verdict", "unknown") for r in records)
    contract_verdict_counts = Counter(
        r.get("verdict", "unknown") for r in with_contract
    )

    # 술어별로 가장 많이 발화한 경로 (clean 은 노이즈원 추적 대상이 아니므로 제외)
    top_targets = defaultdict(Counter)
    for r in records:
        verdict = r.get("verdict")
        target = r.get("target")
        if verdict and verdict != "clean" and target:
            top_targets[verdict][target] += 1

    # 일자별 발화 추이 (UTC 날짜 단위. clean 포함 — 전체 관측량 자체도 추이다)
    daily = defaultdict(Counter)
    for r in records:
        ts = r.get("ts")
        verdict = r.get("verdict", "unknown")
        if not isinstance(ts, (int, float)):
            continue
        day = time.strftime("%Y-%m-%d", time.gmtime(ts))
        daily[day][verdict] += 1

    # 브랜치별 요약
    by_branch = defaultdict(Counter)
    for r in records:
        branch = r.get("branch") or "(unknown)"
        verdict = r.get("verdict", "unknown")
        by_branch[branch][verdict] += 1

    # 정책 deny 로만 나온 deny_hit (계약 유무 무관 — 계약 없는 작업에서도 발화한다)
    policy_deny_hits = sum(
        1
        for r in records
        if r.get("verdict") == "deny_hit" and r.get("policy_deny") is True
    )

    return {
        "total": total,
        "with_contract_total": len(with_contract),
        "policy_deny_hits": policy_deny_hits,
        "verdict_counts": dict(verdict_counts),
        "contract_verdict_counts": dict(contract_verdict_counts),
        "top_targets": {code: counter.most_common() for code, counter in top_targets.items()},
        "daily": {day: dict(counts) for day, counts in sorted(daily.items())},
        "by_branch": {branch: dict(counts) for branch, counts in by_branch.items()},
    }


def build_report(repo_paths, since_days=None):
    """여러 레포의 log.jsonl 을 합쳐 하나의 보고서를 만든다.

    레포별 상태(로그 존재 여부·malformed 줄 수·건수)는 별도로 보존한다 —
    합산만 하면 "레포 A 는 관측 0" 같은 부재 신호가 합계에 묻힌다.
    """
    repo_statuses = []
    combined_records = []
    for repo_path in repo_paths:
        records, malformed, log_exists = load_records(repo_path, since_days=since_days)
        combined_records.extend(records)
        repo_statuses.append(
            {
                "repo_path": str(repo_path),
                "log_exists": log_exists,
                "malformed_lines": malformed,
                "records": len(records),
            }
        )

    return {
        "generated_at": time.time(),
        "since_days": since_days,
        "repos": repo_statuses,
        "metrics": aggregate(combined_records),
    }


def render_text(report, top_n=_DEFAULT_TOP_N):
    """사람이 읽는 텍스트 렌더링. 상위 N 으로 잘라 간결하게 유지한다."""
    metrics = report["metrics"]
    total = metrics["total"]
    with_contract_total = metrics["with_contract_total"]
    lines = []

    lines.append("=== drift-detect M1 관측 보고 ===")
    since_days = report.get("since_days")
    lines.append(f"기간: 최근 {since_days}일" if since_days else "기간: 전체")
    lines.append("")

    lines.append("--- 레포별 로그 상태 ---")
    for rr in report["repos"]:
        if not rr["log_exists"]:
            status = "로그 없음(관측 0)"
        else:
            status = f"{rr['records']}건"
            if rr["malformed_lines"]:
                status += f" (malformed {rr['malformed_lines']}건 스킵)"
        lines.append(f"  {rr['repo_path']}: {status}")
    lines.append("")

    lines.append(f"전체 관측: {total}건 / has_contract:true 관측: {with_contract_total}건")
    if with_contract_total == 0:
        lines.append("")
        lines.append("#" * 60)
        lines.append("# 경고: has_contract:true 관측 0건")
        lines.append("# 관측 0 은 오탐 0 이 아니다 (design.md §8 M1 데이터 부재 위험).")
        lines.append("# 아래 수치를 종료 판정 근거로 쓰지 마라.")
        lines.append("# 기간을 연장하거나 simulate.py 로 과거 브랜치를 소급 재생해 보충하라.")
        lines.append("#" * 60)
    lines.append("")

    lines.append("--- 술어별 발화 건수/비율 (전체 관측 기준) ---")
    for code in _PREDICATE_CODES:
        count = metrics["verdict_counts"].get(code, 0)
        ratio = (count / total * 100) if total else 0.0
        lines.append(f"  {code:16s} {count:6d}건  ({ratio:5.1f}%)")
    other = total - sum(metrics["verdict_counts"].get(c, 0) for c in _PREDICATE_CODES)
    if other:
        lines.append(f"  {'(그 외/unknown)':16s} {other:6d}건")
    lines.append("")

    lines.append(
        f"정책 deny 로 나온 deny_hit: {metrics.get('policy_deny_hits', 0)}건 "
        "(계획 계약 없이도 판정되는 항목, policy_deny:true)"
    )
    lines.append("")

    lines.append("--- 계약 있는 작업에서의 술어 발화율 (진짜 오탐률 후보) ---")
    if with_contract_total == 0:
        lines.append("  (데이터 없음 — 위 경고 참조)")
    else:
        for code in _PREDICATE_CODES:
            count = metrics["contract_verdict_counts"].get(code, 0)
            ratio = count / with_contract_total * 100
            lines.append(f"  {code:16s} {count:6d}건  ({ratio:5.1f}%)")
    lines.append("")

    lines.append(f"--- 술어별 발화 경로 상위 {top_n} ---")
    any_top = False
    for code in _PREDICATE_CODES:
        items = metrics["top_targets"].get(code)
        if not items:
            continue
        any_top = True
        lines.append(f"  [{code}]")
        for target, count in items[:top_n]:
            lines.append(f"    {count:5d}  {target}")
    if not any_top:
        lines.append("  (발화 없음)")
    lines.append("")

    lines.append("--- 일자별 발화 추이 (UTC) ---")
    if not metrics["daily"]:
        lines.append("  (관측 없음)")
    else:
        for day, counts in metrics["daily"].items():
            day_total = sum(counts.values())
            detail = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            lines.append(f"  {day}  총 {day_total:4d}건  ({detail})")
    lines.append("")

    lines.append(f"--- 브랜치별 요약 (상위 {top_n}, 발화량 순) ---")
    if not metrics["by_branch"]:
        lines.append("  (관측 없음)")
    else:
        ranked = sorted(
            metrics["by_branch"].items(), key=lambda kv: -sum(kv[1].values())
        )
        for branch, counts in ranked[:top_n]:
            branch_total = sum(counts.values())
            detail = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            lines.append(f"  {branch_total:5d}건  {branch}  ({detail})")

    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python3 -m drift_detect.report",
        description="M1 감사 로그(audit/log.jsonl) 집계 — 종료 게이트 판정 근거 (읽기 전용).",
    )
    parser.add_argument(
        "--repo",
        action="append",
        dest="repos",
        required=True,
        metavar="PATH",
        help="레포 루트 경로. 여러 번 지정하면 합산 집계한다.",
    )
    parser.add_argument(
        "--since",
        type=float,
        default=None,
        metavar="DAYS",
        help="최근 N일 관측만 집계한다 (기본: 전체 기간).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=_DEFAULT_TOP_N,
        metavar="N",
        help="경로/브랜치 상위 N (기본 10).",
    )
    parser.add_argument("--json", action="store_true", help="사람이 읽는 텍스트 대신 JSON 출력.")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_paths = []
    for repo in args.repos:
        p = Path(repo)
        if not p.is_dir():
            print(f"오류: --repo 경로가 디렉터리가 아님: {repo}", file=sys.stderr)
            return 2
        repo_paths.append(p)

    report = build_report(repo_paths, since_days=args.since)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report, top_n=args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
