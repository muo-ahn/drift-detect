import shutil
import tempfile
import unittest
from pathlib import Path

from drift_detect.compiler import compile_contract, default_plan_path


def _write_plan(repo_root, branch, contract_block=None, prose_only=False):
    parts = branch.split("/")
    plan_dir = Path(repo_root, "documents", "tasks", *parts)
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / "implementation-plan.md"

    body = "# 실행 계획\n\n## 5. 주요 파일 변경 예상\n\n### 신규 생성 파일\n- core/shop/views.py\n\n"
    if not prose_only:
        body += "### 작업 범위 (기계 판독)\n\n"
        body += contract_block
    plan_path.write_text(body, encoding="utf-8")
    return plan_path


class TestCompileContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="drift-detect-test-")
        self.repo_root = Path(self.tmp) / "Some-Repo"
        self.repo_root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_contract_compiles_ok(self):
        block = (
            "```yaml contract\n"
            "contract_version: 1\n"
            "scope:\n"
            "  allow:\n"
            "    - core/shop/**\n"
            "    - core/core/config/aws.py\n"
            "  deny:\n"
            "    - core/common/**\n"
            "```\n"
        )
        _write_plan(self.repo_root, "feature/core-1", contract_block=block)

        contract, meta = compile_contract(self.repo_root, branch="feature/core-1")

        self.assertEqual(contract["status"], "ok")
        self.assertEqual(
            contract["scope"]["allow"], ["core/shop/**", "core/core/config/aws.py"]
        )
        self.assertEqual(contract["scope"]["deny"], ["core/common/**"])
        self.assertIn(
            "documents/tasks/feature/core-1/**", contract["implicit_allow"]
        )
        self.assertIn("**/migrations/**", contract["implicit_allow"])
        self.assertEqual(contract["warnings"], [])
        self.assertIsNotNone(meta)
        self.assertIn("sha256", meta)
        self.assertIn("mtime", meta)

    def test_no_plan_file_is_no_contract(self):
        contract, meta = compile_contract(self.repo_root, branch="feature/none")
        self.assertEqual(contract["status"], "no_contract")
        self.assertEqual(contract["warnings"], [])
        self.assertIsNone(meta)

    def test_plan_without_block_is_no_contract(self):
        _write_plan(self.repo_root, "feature/prose-only", prose_only=True)
        contract, meta = compile_contract(self.repo_root, branch="feature/prose-only")
        self.assertEqual(contract["status"], "no_contract")
        self.assertIn("블록 없음", contract["reason"])
        self.assertIsNotNone(meta)  # 파일은 존재하므로 meta 는 계산된다

    def test_two_contract_blocks_is_no_contract(self):
        branch = "feature/dup"
        parts = branch.split("/")
        plan_dir = Path(self.repo_root, "documents", "tasks", *parts)
        plan_dir.mkdir(parents=True)
        plan_path = plan_dir / "implementation-plan.md"
        plan_path.write_text(
            "```yaml contract\ncontract_version: 1\nscope:\n  allow:\n    - a/**\n```\n"
            "```yaml contract\ncontract_version: 1\nscope:\n  allow:\n    - b/**\n```\n",
            encoding="utf-8",
        )
        contract, _meta = compile_contract(self.repo_root, branch=branch)
        self.assertEqual(contract["status"], "no_contract")
        self.assertIn("2개", contract["reason"])
        # 침묵 실패 방지 (스펙 §6) — 폴백 사유가 reason 에만 있지 않고 warnings 로도 노출.
        codes = [w["code"] for w in contract["warnings"]]
        self.assertIn("multiple_contract_blocks", codes)

    def test_fake_root_path_is_no_contract(self):
        repo_root = Path(self.tmp) / "Core-System"
        repo_root.mkdir()
        block = (
            "```yaml contract\n"
            "contract_version: 1\n"
            "scope:\n"
            "  allow:\n"
            "    - Core-System/core/shop/**\n"
            "```\n"
        )
        _write_plan(repo_root, "feature/core-2", contract_block=block)
        contract, _meta = compile_contract(repo_root, branch="feature/core-2")
        self.assertEqual(contract["status"], "no_contract")
        self.assertIn("가짜 루트", contract["reason"])
        # 침묵 실패 방지 (스펙 §6) — 최빈 오류인 가짜 루트도 warnings 로 관측 가능해야 한다.
        codes = [w["code"] for w in contract["warnings"]]
        self.assertIn("contract_invalid_path", codes)

    def test_fake_root_detected_regardless_of_repo_dirname(self):
        # 결함 1 회귀 — worktree 는 `.claude/worktrees/core-1701/` 처럼 레포 이름과
        # 무관한 디렉터리 이름을 쓴다. repo_name 일치만으로는 가짜 루트가 전부
        # 무사통과했다 (실측: "core-1701", "core-system" 통과, "Core-System" 만 거부).
        # 구조 기반 탐지가 디렉터리 이름과 무관하게 걸려야 한다.
        for dirname in ("Core-System", "core-1701", "core-system"):
            with self.subTest(dirname=dirname):
                # 각 케이스마다 별도 tmp 루트를 쓴다 — macOS(APFS) 기본값은 대소문자
                # 비구분 파일시스템이라 같은 부모 아래 "Core-System" 과 "core-system" 을
                # 만들면 경로가 충돌한다 (테스트 픽스처 문제, 탐지 로직과 무관).
                case_tmp = tempfile.mkdtemp(prefix="drift-detect-test-")
                self.addCleanup(shutil.rmtree, case_tmp, ignore_errors=True)
                repo_root = Path(case_tmp) / dirname
                (repo_root / "core" / "shop").mkdir(parents=True)
                block = (
                    "```yaml contract\n"
                    "contract_version: 1\n"
                    "scope:\n"
                    "  allow:\n"
                    "    - Core-System/core/shop/**\n"
                    "```\n"
                )
                _write_plan(repo_root, "feature/fakeroot", contract_block=block)

                contract, _meta = compile_contract(repo_root, branch="feature/fakeroot")

                self.assertEqual(contract["status"], "no_contract")
                self.assertIn("가짜 루트", contract["reason"])
                codes = [w["code"] for w in contract["warnings"]]
                self.assertIn("contract_invalid_path", codes)

    def test_leading_slash_path_is_no_contract(self):
        block = (
            "```yaml contract\n"
            "contract_version: 1\n"
            "scope:\n"
            "  allow:\n"
            "    - /core/shop/**\n"
            "```\n"
        )
        _write_plan(self.repo_root, "feature/core-3", contract_block=block)
        contract, _meta = compile_contract(self.repo_root, branch="feature/core-3")
        self.assertEqual(contract["status"], "no_contract")
        self.assertTrue("선행" in contract["reason"] or "가짜 루트" in contract["reason"])

    def test_implicit_allow_nests_slash_branch_names(self):
        path = default_plan_path(self.repo_root, "feat/a/b")
        self.assertEqual(
            path,
            self.repo_root / "documents" / "tasks" / "feat" / "a" / "b" / "implementation-plan.md",
        )

    def test_unsupported_contract_version_is_no_contract(self):
        # 스펙 §3 — contract_version 이 1 이 아니면 no_contract 로 폴백 (fail-open, 차단 아님).
        block = (
            "```yaml contract\n"
            "contract_version: 2\n"
            "scope:\n"
            "  allow:\n"
            "    - core/shop/**\n"
            "```\n"
        )
        _write_plan(self.repo_root, "feature/v2", contract_block=block)
        contract, _meta = compile_contract(self.repo_root, branch="feature/v2")
        self.assertEqual(contract["status"], "no_contract")
        self.assertIn("v1", contract["reason"])

    def test_explicit_plan_path_override(self):
        custom = self.repo_root / "custom-plan.md"
        custom.write_text(
            "```yaml contract\ncontract_version: 1\nscope:\n  allow:\n    - core/shop/**\n```\n",
            encoding="utf-8",
        )
        contract, _meta = compile_contract(
            self.repo_root, branch="feature/x", plan_path=custom
        )
        self.assertEqual(contract["status"], "ok")
        self.assertEqual(contract["plan_path"], "custom-plan.md")


class TestRepoEscalationPolicy(unittest.TestCase):
    """docs/decisions/0001-repo-escalation-paths.md 필수 구현 조건 (M0)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="drift-detect-test-")
        self.repo_root = Path(self.tmp) / "Some-Repo"
        self.repo_root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_policy(self, body):
        policy_dir = self.repo_root / ".claude" / "policy"
        policy_dir.mkdir(parents=True, exist_ok=True)
        (policy_dir / "repo-escalation.yaml").write_text(body, encoding="utf-8")

    def _write_contract_plan(self, branch="feature/esc"):
        block = (
            "```yaml contract\n"
            "contract_version: 1\n"
            "scope:\n"
            "  allow:\n"
            "    - core/shop/**\n"
            "```\n"
        )
        _write_plan(self.repo_root, branch, contract_block=block)
        return branch

    def test_policy_and_contract_both_present_merges_escalate_paths(self):
        self._write_policy(
            "contract_version: 1\nscope:\n  allow:\n    - core/common/**\n    - core/core/config/**\n"
        )
        branch = self._write_contract_plan()

        contract, meta = compile_contract(self.repo_root, branch=branch)

        self.assertEqual(contract["status"], "ok")
        # deny 에는 병합되지 않는다 — 차단이 아니라 escalation 이다 (필수 조건 2).
        self.assertEqual(contract["scope"]["deny"], [])
        self.assertEqual(
            contract["escalate_paths"],
            [
                {"pattern": "core/common/**", "source": "repo_default"},
                {"pattern": "core/core/config/**", "source": "repo_default"},
            ],
        )
        # 캐시 무효화를 위한 정책 파일 추적 (필수 조건 4).
        self.assertIsNotNone(meta["policy_path"])
        self.assertIsNotNone(meta["policy_mtime"])
        self.assertIsNotNone(meta["policy_sha256"])
        self.assertEqual(contract["warnings"], [])

    def test_policy_absent_contract_present_no_escalate_paths(self):
        branch = self._write_contract_plan()

        contract, meta = compile_contract(self.repo_root, branch=branch)

        self.assertEqual(contract["status"], "ok")
        self.assertEqual(contract["escalate_paths"], [])
        self.assertEqual(contract["warnings"], [])
        self.assertIsNone(meta["policy_path"])
        self.assertIsNone(meta["policy_mtime"])
        self.assertIsNone(meta["policy_sha256"])

    def test_policy_present_no_contract_stays_no_contract(self):
        # 필수 조건 5 — 레포 기본값 단독으로 "계약 있음" 을 만들지 않는다.
        self._write_policy(
            "contract_version: 1\nscope:\n  allow:\n    - core/common/**\n"
        )

        contract, _meta = compile_contract(self.repo_root, branch="feature/no-plan")

        self.assertEqual(contract["status"], "no_contract")
        self.assertNotIn("escalate_paths", contract)
        self.assertEqual(contract["warnings"], [])

    def test_malformed_policy_file_is_fail_open(self):
        self._write_policy("contract_version: 1\nscope:\n  deny:\n    - a/**\n")  # allow 없음
        branch = self._write_contract_plan()

        contract, meta = compile_contract(self.repo_root, branch=branch)

        self.assertEqual(contract["status"], "ok")
        self.assertEqual(contract["escalate_paths"], [])
        self.assertIsNone(meta["policy_path"])
        # 침묵 실패 방지 (스펙 §6) — malformed 정책 파일이 흔적 없이 사라지지 않는다.
        self.assertEqual(len(contract["warnings"]), 1)
        self.assertEqual(contract["warnings"][0]["code"], "policy_parse_error")
        self.assertIn("repo-escalation.yaml", contract["warnings"][0]["message"])

    def test_policy_unsupported_version_is_fail_open_with_warning(self):
        self._write_policy(
            "contract_version: 2\nscope:\n  allow:\n    - core/common/**\n"
        )
        branch = self._write_contract_plan()

        contract, meta = compile_contract(self.repo_root, branch=branch)

        self.assertEqual(contract["status"], "ok")
        self.assertEqual(contract["escalate_paths"], [])
        self.assertIsNone(meta["policy_path"])
        codes = [w["code"] for w in contract["warnings"]]
        self.assertIn("policy_unsupported_version", codes)

    def test_policy_invalid_path_is_dropped_valid_ones_kept(self):
        # 유효한 항목은 살리고 무효 항목만 버린 뒤 경고 (요청 조건 2).
        self._write_policy(
            "contract_version: 1\n"
            "scope:\n"
            "  allow:\n"
            "    - core/common/**\n"
            "    - /core/core/config/**\n"  # 선행 '/' 위반 — 이 항목만 버려진다
        )
        branch = self._write_contract_plan()

        contract, meta = compile_contract(self.repo_root, branch=branch)

        self.assertEqual(contract["status"], "ok")
        self.assertEqual(
            contract["escalate_paths"],
            [{"pattern": "core/common/**", "source": "repo_default"}],
        )
        self.assertIsNotNone(meta["policy_path"])  # 유효 항목이 남아 정책 자체는 살아있음
        codes = [w["code"] for w in contract["warnings"]]
        self.assertIn("policy_invalid_path", codes)


if __name__ == "__main__":
    unittest.main()
