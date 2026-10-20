import io
import json
import shutil
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from drift_detect import report


def _write_log(repo_root, lines):
    """레코드(dict) 또는 raw 문자열 리스트를 log.jsonl 에 그대로 기록."""
    log_path = repo_root / ".claude" / "state" / "audit" / "log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as fh:
        for item in lines:
            if isinstance(item, str):
                fh.write(item + "\n")
            else:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    return log_path


def _record(verdict, target=None, branch="feature/core-1", has_contract=True, ts=None, tool="Edit"):
    return {
        "ts": ts if ts is not None else time.time(),
        "repo": "Some-Repo",
        "branch": branch,
        "tool": tool,
        "target": target,
        "verdict": verdict,
        "matched": None,
        "has_contract": has_contract,
    }


class ReportTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="drift-detect-report-"))
        self.repo_root = self.tmp / "repo"
        self.repo_root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestLoadRecords(ReportTestBase):
    def test_no_log_file_reports_absent(self):
        records, malformed, log_exists = report.load_records(self.repo_root)
        self.assertEqual(records, [])
        self.assertEqual(malformed, 0)
        self.assertFalse(log_exists)

    def test_reads_valid_lines_and_counts_malformed(self):
        _write_log(
            self.repo_root,
            [
                _record("clean"),
                "not json {{{",
                "[1, 2, 3]",  # valid JSON, not an object
                _record("allow_miss", target="core/shop/views.py"),
            ],
        )
        records, malformed, log_exists = report.load_records(self.repo_root)
        self.assertTrue(log_exists)
        self.assertEqual(len(records), 2)
        self.assertEqual(malformed, 2)

    def test_since_days_filters_old_records(self):
        now = time.time()
        old = now - (10 * 86400)
        _write_log(
            self.repo_root,
            [
                _record("allow_miss", target="a.py", ts=old),
                _record("deny_hit", target="b.py", ts=now),
            ],
        )
        records, _malformed, _exists = report.load_records(self.repo_root, since_days=1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["target"], "b.py")

    def test_blank_lines_are_skipped_silently(self):
        _write_log(self.repo_root, [_record("clean"), "", "   "])
        records, malformed, _exists = report.load_records(self.repo_root)
        self.assertEqual(len(records), 1)
        self.assertEqual(malformed, 0)


class TestAggregate(unittest.TestCase):
    def test_empty_records_all_zero(self):
        metrics = report.aggregate([])
        self.assertEqual(metrics["total"], 0)
        self.assertEqual(metrics["with_contract_total"], 0)
        self.assertEqual(metrics["verdict_counts"], {})
        self.assertEqual(metrics["top_targets"], {})
        self.assertEqual(metrics["daily"], {})
        self.assertEqual(metrics["by_branch"], {})

    def test_verdict_counts_and_contract_scoping(self):
        records = [
            _record("allow_miss", target="a.py", has_contract=True),
            _record("allow_miss", target="a.py", has_contract=True),
            _record("clean", has_contract=False),
            _record("deny_hit", target="b.py", has_contract=True),
        ]
        metrics = report.aggregate(records)
        self.assertEqual(metrics["total"], 4)
        self.assertEqual(metrics["with_contract_total"], 3)
        self.assertEqual(metrics["verdict_counts"]["allow_miss"], 2)
        self.assertEqual(metrics["verdict_counts"]["clean"], 1)
        # clean has_contract=False 는 계약 스코프 집계에서 빠진다
        self.assertNotIn("clean", metrics["contract_verdict_counts"])
        self.assertEqual(metrics["contract_verdict_counts"]["allow_miss"], 2)

    def test_top_targets_excludes_clean_and_counts_hits(self):
        records = [
            _record("allow_miss", target="core/shop/views.py"),
            _record("allow_miss", target="core/shop/views.py"),
            _record("allow_miss", target="core/common/utils.py"),
            _record("clean", target="core/should/not/appear.py"),
        ]
        metrics = report.aggregate(records)
        top = metrics["top_targets"]["allow_miss"]
        self.assertEqual(top[0], ("core/shop/views.py", 2))
        self.assertNotIn("clean", metrics["top_targets"])

    def test_daily_and_branch_grouping(self):
        day1 = time.mktime(time.strptime("2026-01-01", "%Y-%m-%d"))
        day2 = time.mktime(time.strptime("2026-01-02", "%Y-%m-%d"))
        records = [
            _record("allow_miss", target="a.py", ts=day1, branch="feature/x"),
            _record("deny_hit", target="b.py", ts=day2, branch="feature/y"),
            _record("deny_hit", target="c.py", ts=day2, branch="feature/y"),
        ]
        metrics = report.aggregate(records)
        self.assertEqual(len(metrics["daily"]), 2)
        self.assertEqual(metrics["by_branch"]["feature/y"]["deny_hit"], 2)
        self.assertEqual(metrics["by_branch"]["feature/x"]["allow_miss"], 1)

    def test_missing_branch_falls_back_to_unknown_bucket(self):
        rec = _record("clean")
        rec["branch"] = None
        metrics = report.aggregate([rec])
        self.assertIn("(unknown)", metrics["by_branch"])


class TestPolicyDenyCount(unittest.TestCase):
    def test_counts_only_policy_deny_hits(self):
        policy = dict(_record("deny_hit", "infra/a.tf", has_contract=False), policy_deny=True)
        plan_deny = dict(_record("deny_hit", "core/legacy/a.py"), policy_deny=False)
        old_style = _record("deny_hit", "core/x.py")  # 필드 없는 구 로그
        flag_but_clean = dict(_record("clean", "core/y.py"), policy_deny=True)
        metrics = report.aggregate([policy, plan_deny, old_style, flag_but_clean])
        self.assertEqual(metrics["policy_deny_hits"], 1)

    def test_render_has_policy_deny_line(self):
        rep = {
            "generated_at": 0,
            "since_days": None,
            "repos": [],
            "metrics": report.aggregate(
                [dict(_record("deny_hit", "infra/a", has_contract=False), policy_deny=True)]
            ),
        }
        self.assertIn("정책 deny 로 나온 deny_hit: 1건", report.render_text(rep))


class TestBuildReportNoContractWarning(ReportTestBase):
    def test_zero_has_contract_true_is_flagged_in_text(self):
        _write_log(self.repo_root, [_record("clean", has_contract=False)])
        rep = report.build_report([self.repo_root])
        self.assertEqual(rep["metrics"]["with_contract_total"], 0)
        text = report.render_text(rep)
        self.assertIn("경고", text)
        self.assertIn("has_contract:true 관측 0건", text)

    def test_nonzero_has_contract_true_has_no_warning_banner(self):
        _write_log(
            self.repo_root,
            [_record("allow_miss", target="a.py", has_contract=True)],
        )
        rep = report.build_report([self.repo_root])
        text = report.render_text(rep)
        self.assertNotIn("경고: has_contract:true 관측 0건", text)

    def test_no_log_at_all_still_produces_report_without_crash(self):
        rep = report.build_report([self.repo_root])
        self.assertEqual(rep["metrics"]["total"], 0)
        self.assertFalse(rep["repos"][0]["log_exists"])
        text = report.render_text(rep)
        self.assertIn("로그 없음", text)


class TestMultiRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="drift-detect-report-multi-"))
        self.repo_a = self.tmp / "repo-a"
        self.repo_b = self.tmp / "repo-b"
        self.repo_a.mkdir()
        self.repo_b.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_combines_records_across_repos(self):
        _write_log(self.repo_a, [_record("allow_miss", target="a.py")])
        _write_log(self.repo_b, [_record("deny_hit", target="b.py")])
        rep = report.build_report([self.repo_a, self.repo_b])
        self.assertEqual(rep["metrics"]["total"], 2)
        self.assertEqual(len(rep["repos"]), 2)


class TestCLI(ReportTestBase):
    def test_nonexistent_repo_returns_2(self):
        code = report.main(["--repo", "/no/such/path"])
        self.assertEqual(code, 2)

    def test_json_output_is_valid_json(self):
        _write_log(self.repo_root, [_record("allow_miss", target="a.py")])
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = report.main(["--repo", str(self.repo_root), "--json"])
        self.assertEqual(code, 0)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(parsed["metrics"]["total"], 1)

    def test_text_output_runs_without_error(self):
        _write_log(self.repo_root, [_record("clean")])
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = report.main(["--repo", str(self.repo_root), "--top", "3"])
        self.assertEqual(code, 0)
        self.assertIn("drift-detect M1 관측 보고", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
