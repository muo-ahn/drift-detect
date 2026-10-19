import os
import tempfile
import unittest
from unittest import mock

from drift_detect import predicates


def _contract(allow=None, deny=None, implicit_allow=None, escalate_paths=None):
    return {
        "status": "ok",
        "scope": {"allow": allow or [], "deny": deny or []},
        "implicit_allow": implicit_allow or [],
        "escalate_paths": [
            {"pattern": p, "source": "repo_default"} for p in (escalate_paths or [])
        ],
    }


NO_CONTRACT = {"status": "no_contract", "reason": "계획 문서 없음"}


class TestAllowMiss(unittest.TestCase):
    def test_no_contract_returns_none(self):
        self.assertIsNone(predicates.allow_miss("core/shop/views.py", NO_CONTRACT))
        self.assertIsNone(predicates.allow_miss("core/shop/views.py", None))

    def test_target_in_allow_returns_none(self):
        contract = _contract(allow=["core/shop/**"])
        self.assertIsNone(predicates.allow_miss("core/shop/views.py", contract))

    def test_target_in_implicit_allow_returns_none(self):
        contract = _contract(allow=["core/shop/**"], implicit_allow=["**/migrations/**"])
        self.assertIsNone(
            predicates.allow_miss("core/shop/migrations/0001.py", contract)
        )

    def test_target_outside_allow_fires(self):
        contract = _contract(allow=["core/shop/**"])
        result = predicates.allow_miss("core/common/utils.py", contract)
        self.assertEqual(result["code"], "allow_miss")
        self.assertIsNone(result["matched"])


class TestDenyHit(unittest.TestCase):
    def test_no_contract_returns_none(self):
        self.assertIsNone(predicates.deny_hit("core/common/utils.py", NO_CONTRACT))

    def test_deny_match_fires(self):
        contract = _contract(allow=["core/**"], deny=["core/common/**"])
        result = predicates.deny_hit("core/common/utils.py", contract)
        self.assertEqual(result, {"code": "deny_hit", "matched": "core/common/**"})

    def test_no_deny_match_returns_none(self):
        contract = _contract(allow=["core/**"], deny=["core/common/**"])
        self.assertIsNone(predicates.deny_hit("core/shop/views.py", contract))


class TestEscalateHit(unittest.TestCase):
    def test_no_contract_returns_none(self):
        self.assertIsNone(predicates.escalate_hit("core/core/config/aws.py", NO_CONTRACT))

    def test_escalate_match_fires(self):
        contract = _contract(
            allow=["core/**"], escalate_paths=["core/core/config/**"]
        )
        result = predicates.escalate_hit("core/core/config/aws.py", contract)
        self.assertEqual(
            result, {"code": "escalate_hit", "matched": "core/core/config/**"}
        )

    def test_no_escalate_match_returns_none(self):
        contract = _contract(allow=["core/**"], escalate_paths=["core/common/**"])
        self.assertIsNone(predicates.escalate_hit("core/shop/views.py", contract))


class TestTestUnverified(unittest.TestCase):
    def test_no_command_returns_none(self):
        self.assertIsNone(predicates.test_unverified(None))
        self.assertIsNone(predicates.test_unverified(""))

    def test_non_test_command_returns_none(self):
        self.assertIsNone(
            predicates.test_unverified("ls -la", cwd="/repo/.claude/worktrees/x/core")
        )

    def test_test_command_outside_worktree_returns_none(self):
        result = predicates.test_unverified(
            "make test-fast ARGS=shop.tests", cwd="/repo/core"
        )
        self.assertIsNone(result)

    def test_worktree_test_command_missing_flags_fires(self):
        result = predicates.test_unverified(
            "make test-fast ARGS=shop.tests",
            cwd="/repo/.claude/worktrees/core-123/core",
        )
        self.assertEqual(result["code"], "test_unverified")

    def test_worktree_docker_exec_with_both_flags_returns_none(self):
        command = (
            "docker exec -w /code/worktrees/core-123/core "
            "-e DB_SCHEMA=core_wt_core_123 core-backend python manage.py test shop.tests"
        )
        result = predicates.test_unverified(
            command, cwd="/repo/.claude/worktrees/core-123/core"
        )
        self.assertIsNone(result)

    def test_worktree_docker_exec_missing_db_schema_fires(self):
        command = (
            "docker exec -w /code/worktrees/core-123/core "
            "core-backend python manage.py test shop.tests"
        )
        result = predicates.test_unverified(
            command, cwd="/repo/.claude/worktrees/core-123/core"
        )
        self.assertEqual(result["code"], "test_unverified")

    def test_worktree_hint_from_command_alone(self):
        # cwd 가 비어도 명령 문자열 자체에 worktree 경로가 보이면 잡는다.
        command = "docker exec core-backend python manage.py test shop.tests"
        result = predicates.test_unverified(command, cwd=None)
        self.assertIsNone(result)  # worktree 흔적이 전혀 없으므로 대상 아님

    def test_make_test_fast_in_worktree_with_shim_is_clean(self):
        # 실측 근거: 정상 사용 8건 중 오탐 1건이 바로 이 케이스였다. shim 이
        # 있으면 -w/DB_SCHEMA 는 Make 내부에서 조립되므로 명령줄에 안 보이는
        # 것이 정상 — clean 이어야 한다.
        with tempfile.TemporaryDirectory() as tmp:
            worktree_cwd = os.path.join(tmp, ".claude", "worktrees", "core-1", "core")
            os.makedirs(worktree_cwd)
            open(os.path.join(worktree_cwd, "GNUmakefile"), "w").close()
            result = predicates.test_unverified(
                'make test-fast ARGS="shop.tests"', cwd=worktree_cwd
            )
            self.assertIsNone(result)

    def test_make_test_fast_in_worktree_without_shim_fires(self):
        # shim 이 정말 없으면 구 Makefile 이 메인 레포를 대신 테스트하는 진짜
        # 무음 오검증이므로 여전히 발화해야 한다 (정탐).
        with tempfile.TemporaryDirectory() as tmp:
            worktree_cwd = os.path.join(tmp, ".claude", "worktrees", "core-1", "core")
            os.makedirs(worktree_cwd)
            result = predicates.test_unverified(
                'make test-fast ARGS="shop.tests"', cwd=worktree_cwd
            )
            self.assertEqual(result["code"], "test_unverified")
            self.assertEqual(result["matched"], "worktree_make_missing_shim")

    def test_make_dry_run_is_not_a_test_command(self):
        # `make -n test-fast` 는 dry-run — "make" 와 "test-fast" 사이에 -n 이
        # 끼어 있어 정규식이 매치하지 않는다. 발화하면 안 된다.
        with tempfile.TemporaryDirectory() as tmp:
            worktree_cwd = os.path.join(tmp, ".claude", "worktrees", "core-1", "core")
            os.makedirs(worktree_cwd)
            result = predicates.test_unverified(
                "make -n test-fast ARGS=shop.tests", cwd=worktree_cwd
            )
            self.assertIsNone(result)

    def test_make_wt_init_and_wt_clean_are_not_test_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            worktree_cwd = os.path.join(tmp, ".claude", "worktrees", "core-1", "core")
            os.makedirs(worktree_cwd)
            self.assertIsNone(
                predicates.test_unverified("make wt-init", cwd=worktree_cwd)
            )
            self.assertIsNone(
                predicates.test_unverified("make wt-clean", cwd=worktree_cwd)
            )

    def test_shim_check_failure_is_fail_open(self):
        # 파일시스템 접근(shim 존재 확인)이 실패하면 발화하지 않는다.
        with tempfile.TemporaryDirectory() as tmp:
            worktree_cwd = os.path.join(tmp, ".claude", "worktrees", "core-1", "core")
            os.makedirs(worktree_cwd)
            with mock.patch("os.path.isfile", side_effect=OSError("permission denied")):
                result = predicates.test_unverified(
                    'make test-fast ARGS="shop.tests"', cwd=worktree_cwd
                )
            self.assertIsNone(result)

    def test_shim_check_without_cwd_is_fail_open(self):
        # cwd 가 없어도(=확인 불가) 발화하지 않는다. worktree 힌트는 명령
        # 문자열 자체에서 얻는다.
        command = 'make test-fast ARGS="shop.tests" # /repo/.claude/worktrees/core-1/core'
        result = predicates.test_unverified(command, cwd=None)
        self.assertIsNone(result)


class TestMakeShimPresent(unittest.TestCase):
    def test_no_cwd_returns_none(self):
        self.assertIsNone(predicates._make_shim_present(None))
        self.assertIsNone(predicates._make_shim_present(""))

    def test_shim_present_returns_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "GNUmakefile"), "w").close()
            self.assertTrue(predicates._make_shim_present(tmp))

    def test_shim_absent_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(predicates._make_shim_present(tmp))

    def test_access_failure_returns_none(self):
        with mock.patch("os.path.isfile", side_effect=OSError("boom")):
            self.assertIsNone(predicates._make_shim_present("/some/worktree/core"))


class TestEvaluatePriority(unittest.TestCase):
    def test_clean_when_nothing_fires(self):
        contract = _contract(allow=["core/shop/**"])
        result = predicates.evaluate("core/shop/views.py", contract)
        self.assertEqual(result, {"code": "clean", "matched": None})

    def test_deny_beats_escalate_and_allow_miss(self):
        contract = _contract(
            allow=["core/**"],
            deny=["core/common/**"],
            escalate_paths=["core/common/**"],
        )
        result = predicates.evaluate("core/common/utils.py", contract)
        self.assertEqual(result["code"], "deny_hit")

    def test_escalate_beats_allow_miss(self):
        # escalate_paths 는 allow 목록과 무관하게 별도 필드이므로, 대상이 명시
        # allow 안에 있어도(=allow_miss 는 안 뜬다) escalate 는 뜰 수 있다.
        contract = _contract(
            allow=["core/core/config/**"], escalate_paths=["core/core/config/**"]
        )
        result = predicates.evaluate("core/core/config/aws.py", contract)
        self.assertEqual(result["code"], "escalate_hit")

    def test_no_target_falls_back_to_test_unverified(self):
        contract = _contract(allow=["core/shop/**"])
        result = predicates.evaluate(
            None,
            contract,
            command="make test-fast ARGS=shop.tests",
            cwd="/repo/.claude/worktrees/core-1/core",
        )
        self.assertEqual(result["code"], "test_unverified")

    def test_no_target_no_command_is_clean(self):
        result = predicates.evaluate(None, NO_CONTRACT, command=None, cwd=None)
        self.assertEqual(result, {"code": "clean", "matched": None})


if __name__ == "__main__":
    unittest.main()
