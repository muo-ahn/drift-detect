import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from drift_detect import observer


def _init_git_repo(root):
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "a@b.c"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    (root / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "init"], check=True)


def _write_plan(repo_root, branch, allow, deny=None):
    parts = branch.split("/")
    plan_dir = Path(repo_root, "documents", "tasks", *parts)
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / "implementation-plan.md"
    lines = ["contract_version: 1", "scope:", "  allow:"]
    lines += [f"    - {p}" for p in allow]
    if deny:
        lines.append("  deny:")
        lines += [f"    - {p}" for p in deny]
    block = "```yaml contract\n" + "\n".join(lines) + "\n```\n"
    body = (
        "# 실행 계획\n\n## 5. 주요 파일 변경 예상\n\n"
        "### 작업 범위 (기계 판독)\n\n" + block
    )
    plan_path.write_text(body, encoding="utf-8")
    return plan_path


class ObserverTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="drift-detect-observer-"))
        self.repo_root = self.tmp / "Some-Repo"
        self.repo_root.mkdir()
        _init_git_repo(self.repo_root)
        subprocess.run(
            ["git", "-C", str(self.repo_root), "checkout", "-q", "-b", "feature/core-1"],
            check=True,
        )
        # 0003 zero-footprint 이후 레포 기본 escalation 정책은 대상 레포 밖,
        # drift-detect 쪽 정책 디렉터리에 있다. 실제 drift-detect/policies/ 의
        # 상태에 테스트가 우연히 좌우되지 않도록 격리한다.
        self.policy_dir = self.tmp / "policies"
        self._policy_env_patch = mock.patch.dict(
            os.environ, {"DRIFT_DETECT_POLICY_DIR": str(self.policy_dir)}
        )
        self._policy_env_patch.start()
        self.addCleanup(self._policy_env_patch.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_stdin(self, payload_obj):
        """main() 을 stdin/exit 을 가로채 실행하고 (exit_code, stdout) 반환."""
        stdin_text = (
            payload_obj if isinstance(payload_obj, str) else json.dumps(payload_obj)
        )
        with mock.patch.object(sys, "stdin", io.StringIO(stdin_text)):
            with mock.patch.object(sys, "stdout", new_callable=io.StringIO) as out:
                with self.assertRaises(SystemExit) as cm:
                    observer.main()
        return cm.exception.code, out.getvalue()

    def _log_lines(self):
        log_path = self.repo_root / ".claude" / "state" / "audit" / "log.jsonl"
        if not log_path.is_file():
            return []
        return [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line
        ]


class TestMaliciousInputResilience(ObserverTestBase):
    def test_empty_stdin_exits_zero_and_silent(self):
        code, out = self._run_stdin("")
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertEqual(self._log_lines(), [])

    def test_invalid_json_exits_zero_and_silent(self):
        code, out = self._run_stdin("{not valid json::")
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_missing_keys_exits_zero_and_silent(self):
        code, out = self._run_stdin({})
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_payload_not_a_dict_exits_zero(self):
        code, out = self._run_stdin(json.dumps([1, 2, 3]))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_nonexistent_repo_path_exits_zero_and_silent(self):
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/no/such/file.py"},
            "cwd": "/no/such/repo/path/at/all",
        }
        code, out = self._run_stdin(payload)
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_tool_input_wrong_type_does_not_crash(self):
        payload = {
            "tool_name": "Edit",
            "tool_input": "not-a-dict",
            "cwd": str(self.repo_root),
        }
        code, out = self._run_stdin(payload)
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_irrelevant_tool_name_is_noop(self):
        payload = {
            "tool_name": "Read",
            "tool_input": {"file_path": "core/shop/views.py"},
            "cwd": str(self.repo_root),
        }
        code, out = self._run_stdin(payload)
        self.assertEqual(code, 0)
        self.assertEqual(self._log_lines(), [])

    def test_drift_detect_unavailable_is_silent_noop(self):
        with mock.patch.object(observer, "compile_contract", None):
            payload = {
                "tool_name": "Edit",
                "tool_input": {"file_path": "core/shop/views.py"},
                "cwd": str(self.repo_root),
            }
            code, out = self._run_stdin(payload)
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertEqual(self._log_lines(), [])


class TestPredicateLogging(ObserverTestBase):
    def test_allow_miss_logged(self):
        _write_plan(self.repo_root, "feature/core-1", allow=["core/shop/**"])
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.repo_root / "core/common/utils.py")},
            "cwd": str(self.repo_root),
        }
        code, _ = self._run_stdin(payload)
        self.assertEqual(code, 0)
        lines = self._log_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["verdict"], "allow_miss")
        self.assertEqual(lines[0]["target"], "core/common/utils.py")
        self.assertTrue(lines[0]["has_contract"])
        self.assertEqual(lines[0]["branch"], "feature/core-1")

    def test_deny_hit_logged(self):
        _write_plan(
            self.repo_root,
            "feature/core-1",
            allow=["core/**"],
            deny=["core/common/**"],
        )
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(self.repo_root / "core/common/utils.py"),
                "content": "x",
            },
            "cwd": str(self.repo_root),
        }
        self._run_stdin(payload)
        lines = self._log_lines()
        self.assertEqual(lines[0]["verdict"], "deny_hit")
        self.assertEqual(lines[0]["matched"], "core/common/**")
        # 파일 내용(content)이 로그에 새면 안 된다.
        self.assertNotIn("x", json.dumps(lines[0]))

    def test_escalate_hit_logged(self):
        self.policy_dir.mkdir(parents=True)
        (self.policy_dir / f"{self.repo_root.name}.yaml").write_text(
            "contract_version: 1\nscope:\n  allow:\n    - core/core/config/**\n",
            encoding="utf-8",
        )
        _write_plan(self.repo_root, "feature/core-1", allow=["core/core/config/**"])
        payload = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(self.repo_root / "core/core/config/aws.py")
            },
            "cwd": str(self.repo_root),
        }
        self._run_stdin(payload)
        lines = self._log_lines()
        self.assertEqual(lines[0]["verdict"], "escalate_hit")

    def test_no_plan_document_logs_clean_without_contract(self):
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.repo_root / "core/shop/views.py")},
            "cwd": str(self.repo_root),
        }
        self._run_stdin(payload)
        lines = self._log_lines()
        self.assertEqual(lines[0]["verdict"], "clean")
        self.assertFalse(lines[0]["has_contract"])

    def test_bash_test_unverified_logged(self):
        worktree_cwd = str(self.repo_root / ".claude" / "worktrees" / "core-1" / "core")
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "make test-fast ARGS=shop.tests"},
            "cwd": worktree_cwd,
        }
        # git 컨텍스트는 실제 레포 루트를 가리켜야 하므로, cwd 를 레포 안 실제
        # 디렉터리로 만들되 이름에 worktree 힌트를 포함시킨다.
        real_cwd = self.repo_root / ".claude" / "worktrees" / "core-1" / "core"
        real_cwd.mkdir(parents=True)
        payload["cwd"] = str(real_cwd)
        self._run_stdin(payload)
        lines = self._log_lines()
        self.assertEqual(lines[0]["verdict"], "test_unverified")
        self.assertEqual(lines[0]["target"], "make test-fast ARGS=shop.tests")

    def test_command_summary_is_truncated(self):
        real_cwd = self.repo_root / ".claude" / "worktrees" / "core-1" / "core"
        real_cwd.mkdir(parents=True)
        long_command = "make test-fast ARGS=" + "x" * 500
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": long_command},
            "cwd": str(real_cwd),
        }
        self._run_stdin(payload)
        lines = self._log_lines()
        self.assertLessEqual(len(lines[0]["target"]), 161)
        self.assertTrue(lines[0]["target"].endswith("…"))

    def test_target_outside_repo_is_not_evaluated_but_does_not_crash(self):
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/etc/hosts"},
            "cwd": str(self.repo_root),
        }
        code, out = self._run_stdin(payload)
        self.assertEqual(code, 0)
        lines = self._log_lines()
        self.assertEqual(lines[0]["verdict"], "clean")
        self.assertIsNone(lines[0]["target"])


class TestContractCache(ObserverTestBase):
    def test_second_call_reuses_cached_contract_without_recompiling(self):
        _write_plan(self.repo_root, "feature/core-1", allow=["core/shop/**"])
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.repo_root / "core/shop/views.py")},
            "cwd": str(self.repo_root),
        }
        self._run_stdin(payload)
        state_dir = self.repo_root / ".claude" / "state"
        self.assertTrue((state_dir / "contract.json").is_file())
        self.assertTrue((state_dir / "contract.meta.json").is_file())

        with mock.patch.object(
            observer, "compile_contract", wraps=observer.compile_contract
        ) as spy:
            self._run_stdin(payload)
            spy.assert_not_called()

    def test_plan_change_invalidates_cache(self):
        _write_plan(self.repo_root, "feature/core-1", allow=["core/shop/**"])
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(self.repo_root / "core/common/utils.py")},
            "cwd": str(self.repo_root),
        }
        self._run_stdin(payload)
        self.assertEqual(self._log_lines()[-1]["verdict"], "allow_miss")

        _write_plan(self.repo_root, "feature/core-1", allow=["core/common/**"])
        self._run_stdin(payload)
        self.assertEqual(self._log_lines()[-1]["verdict"], "clean")


if __name__ == "__main__":
    unittest.main()
