import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from drift_detect import simulate


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _init_repo_with_history(root):
    root.mkdir()
    _git(root, "init", "-q", "-b", "dev")
    _git(root, "config", "user.email", "a@b.c")
    _git(root, "config", "user.name", "t")
    (root / "core").mkdir()
    (root / "core" / "shop").mkdir()
    (root / "core" / "common").mkdir()
    (root / "core" / "shop" / "views.py").write_text("v1", encoding="utf-8")
    (root / "core" / "common" / "utils.py").write_text("u1", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    _git(root, "commit", "-q", "-m", "base")


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
    body = "# plan\n\n## 5\n\n### 작업 범위 (기계 판독)\n\n" + block
    plan_path.write_text(body, encoding="utf-8")


class TestChangedFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="drift-detect-sim-"))
        self.repo_root = self.tmp / "repo"
        _init_repo_with_history(self.repo_root)
        _git(self.repo_root, "checkout", "-q", "-b", "feature/core-9")
        (self.repo_root / "core" / "shop" / "views.py").write_text("v2", encoding="utf-8")
        (self.repo_root / "core" / "common" / "utils.py").write_text("u2", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo_root), "add", "."], check=True)
        _git(self.repo_root, "commit", "-q", "-m", "feature work")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_branch_vs_base_lists_changed_files(self):
        files = simulate.changed_files(self.repo_root, "feature/core-9", base="dev")
        self.assertEqual(
            sorted(files), ["core/common/utils.py", "core/shop/views.py"]
        )

    def test_commit_range_overrides_branch_base(self):
        rev = subprocess.run(
            ["git", "-C", str(self.repo_root), "rev-parse", "dev"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        files = simulate.changed_files(
            self.repo_root, "feature/core-9", commit_range=f"{rev}..feature/core-9"
        )
        self.assertEqual(
            sorted(files), ["core/common/utils.py", "core/shop/views.py"]
        )

    def test_invalid_range_raises_simulation_error(self):
        with self.assertRaises(simulate.SimulationError):
            simulate.changed_files(
                self.repo_root, "feature/core-9", commit_range="not-a-ref..also-not"
            )


class TestSimulate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="drift-detect-sim-"))
        self.repo_root = self.tmp / "repo"
        _init_repo_with_history(self.repo_root)
        _git(self.repo_root, "checkout", "-q", "-b", "feature/core-9")
        (self.repo_root / "core" / "shop" / "views.py").write_text("v2", encoding="utf-8")
        (self.repo_root / "core" / "common" / "utils.py").write_text("u2", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo_root), "add", "."], check=True)
        _git(self.repo_root, "commit", "-q", "-m", "feature work")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_plan_document_reports_no_contract(self):
        result = simulate.simulate(self.repo_root, "feature/core-9", base="dev")
        self.assertFalse(result["has_contract"])
        self.assertIsNotNone(result["reason"])
        self.assertEqual(result["predicate_hits"], {})
        self.assertEqual(result["files_checked"], 2)

    def test_contract_flags_out_of_scope_file(self):
        _write_plan(self.repo_root, "feature/core-9", allow=["core/shop/**"])
        result = simulate.simulate(self.repo_root, "feature/core-9", base="dev")
        self.assertTrue(result["has_contract"])
        self.assertIn("allow_miss", result["predicate_hits"])
        self.assertIn(
            "core/common/utils.py", result["predicate_hits"]["allow_miss"]
        )
        self.assertNotIn(
            "core/shop/views.py",
            result["predicate_hits"].get("allow_miss", []),
        )

    def test_contract_deny_takes_priority(self):
        _write_plan(
            self.repo_root,
            "feature/core-9",
            allow=["core/**"],
            deny=["core/common/**"],
        )
        result = simulate.simulate(self.repo_root, "feature/core-9", base="dev")
        self.assertIn("deny_hit", result["predicate_hits"])
        self.assertIn("core/common/utils.py", result["predicate_hits"]["deny_hit"])
        self.assertNotIn("allow_miss", result["predicate_hits"])


class TestCLI(unittest.TestCase):
    def test_nonexistent_repo_returns_2(self):
        code = simulate.main(["--repo", "/no/such/path", "--branch", "x"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
