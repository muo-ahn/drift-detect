import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from drift_detect.compiler import compile_contract, default_plan_path


class _IsolatedPolicyDirMixin:
    """모든 컴파일러 테스트를 실제 drift-detect/policies/ 로부터 격리한다.

    DRIFT_DETECT_POLICY_DIR 을 존재하지 않는 tmp 하위 경로로 돌려, 정책 파일이
    실제로 존재하는지 여부(0003 개정 이후 대상 레포 밖의 실제 drift-detect
    레포 상태)에 테스트가 우연히 좌우되지 않게 한다. 정책 병합 자체를 검증하는
    테스트(TestRepoEscalationPolicy)는 이 디렉터리 안에 직접 파일을 써서 쓴다.

    호출 순서 때문에 setUp 을 오버라이드하지 않는다 — self.tmp 는 각 테스트
    클래스의 setUp 에서 먼저 만들어지므로, 그 뒤에 이 메서드를 명시적으로
    불러 격리를 건다.
    """

    def _isolate_policy_dir(self):
        self.policy_dir = Path(self.tmp) / "policies"
        patcher = mock.patch.dict(
            os.environ, {"DRIFT_DETECT_POLICY_DIR": str(self.policy_dir)}
        )
        patcher.start()
        self.addCleanup(patcher.stop)


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


class TestCompileContract(_IsolatedPolicyDirMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="drift-detect-test-")
        self.repo_root = Path(self.tmp) / "Some-Repo"
        self.repo_root.mkdir()
        self._isolate_policy_dir()

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


class TestRepoEscalationPolicy(_IsolatedPolicyDirMixin, unittest.TestCase):
    """docs/decisions/0001-repo-escalation-paths.md 필수 구현 조건 (M0).

    위치는 docs/decisions/0003-zero-footprint.md 로 개정됨 — 정책 파일은
    대상 레포가 아니라 drift-detect 쪽 정책 디렉터리(레포별 <repo>.yaml)에 있다.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="drift-detect-test-")
        self.repo_root = Path(self.tmp) / "Some-Repo"
        self.repo_root.mkdir()
        self._isolate_policy_dir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_policy(self, body):
        self.policy_dir.mkdir(parents=True, exist_ok=True)
        (self.policy_dir / f"{self.repo_root.name}.yaml").write_text(
            body, encoding="utf-8"
        )

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
        self.assertIn(f"{self.repo_root.name}.yaml", contract["warnings"][0]["message"])

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

    def test_policy_lives_outside_target_repo(self):
        # 0003 zero-footprint — 정책 파일 경로가 대상 레포 트리 밖(정책
        # 디렉터리)을 가리켜야 하고, 대상 레포 안에는 아무것도 생기지 않는다.
        self._write_policy("contract_version: 1\nscope:\n  allow:\n    - core/common/**\n")
        branch = self._write_contract_plan()

        contract, meta = compile_contract(self.repo_root, branch=branch)

        self.assertEqual(contract["status"], "ok")
        policy_path = Path(meta["policy_path"])
        self.assertTrue(policy_path.is_absolute())
        self.assertEqual(policy_path.parent, self.policy_dir)
        # 대상 레포 서브트리 밖에 있어야 한다 (repo_root.iterdir 로도 확인 가능하지만
        # relative_to 실패로 "서브트리가 아님"을 직접 검증한다).
        with self.assertRaises(ValueError):
            policy_path.relative_to(self.repo_root)
        self.assertFalse((self.repo_root / ".claude" / "policy").exists())

    def test_policy_edit_changes_meta_sha256(self):
        # [0001] 필수 조건 4 — 정책 파일을 고치면 contract.meta.json 의
        # policy_sha256 이 바뀌어야 캐시 무효화가 성립한다.
        self._write_policy("contract_version: 1\nscope:\n  allow:\n    - core/common/**\n")
        branch = self._write_contract_plan()
        _contract1, meta1 = compile_contract(self.repo_root, branch=branch)

        self._write_policy(
            "contract_version: 1\nscope:\n  allow:\n    - core/common/**\n    - core/core/config/**\n"
        )
        _contract2, meta2 = compile_contract(self.repo_root, branch=branch)

        self.assertNotEqual(meta1["policy_sha256"], meta2["policy_sha256"])

    def test_policy_dir_env_var_override(self):
        # 요청 조건 2 — DRIFT_DETECT_POLICY_DIR 로 정책 디렉터리 자체를 덮어쓸 수 있다.
        other_dir = Path(self.tmp) / "other-policies"
        other_dir.mkdir()
        (other_dir / f"{self.repo_root.name}.yaml").write_text(
            "contract_version: 1\nscope:\n  allow:\n    - core/common/**\n",
            encoding="utf-8",
        )
        branch = self._write_contract_plan()

        with mock.patch.dict(os.environ, {"DRIFT_DETECT_POLICY_DIR": str(other_dir)}):
            contract, meta = compile_contract(self.repo_root, branch=branch)

        self.assertEqual(
            contract["escalate_paths"],
            [{"pattern": "core/common/**", "source": "repo_default"}],
        )
        self.assertEqual(Path(meta["policy_path"]).parent, other_dir)

    def test_repo_name_path_escape_is_rejected(self):
        # 요청 조건 6 — 경로 구분자·상위 참조가 섞인 레포 이름은 통째로 거부된다
        # (basename 으로 슬쩍 축약해 통과시키지 않는다 — 경로 탈출 방지).
        from drift_detect.compiler import resolve_policy_path

        self.assertIsNone(resolve_policy_path(self.repo_root, repo_name="../../etc"))
        self.assertIsNone(resolve_policy_path(self.repo_root, repo_name=".."))
        self.assertIsNone(resolve_policy_path(self.repo_root, repo_name=""))
        self.assertIsNone(resolve_policy_path(self.repo_root, repo_name="a/b/Some-Repo"))
        self.assertIsNone(resolve_policy_path(self.repo_root, repo_name="a\\b"))
        # repo_name 생략 시 repo_root.name 은 이미 구분자 없는 단일 세그먼트이므로 정상 동작.
        path = resolve_policy_path(self.repo_root)
        self.assertEqual(path.parent, self.policy_dir)
        self.assertEqual(path.name, "Some-Repo.yaml")


if __name__ == "__main__":
    unittest.main()
