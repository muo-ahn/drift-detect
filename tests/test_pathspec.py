import shutil
import tempfile
import unittest
from pathlib import Path

from drift_detect.pathspec import PathMatcher, PathValidationError, validate_path


class TestValidatePath(unittest.TestCase):
    def test_valid_path_ok(self):
        validate_path("core/shop/urls.py")  # 예외 없이 통과해야 함

    def test_leading_slash_rejected(self):
        with self.assertRaises(PathValidationError):
            validate_path("/core/shop/urls.py")

    def test_leading_dot_slash_rejected(self):
        with self.assertRaises(PathValidationError):
            validate_path("./core/shop/urls.py")

    def test_fake_root_repo_name_rejected(self):
        with self.assertRaises(PathValidationError):
            validate_path("Core-System/core/shop/**", repo_name="Core-System")

    def test_fake_root_repo_name_case_insensitive(self):
        with self.assertRaises(PathValidationError):
            validate_path("core-system/core/shop/**", repo_name="Core-System")

    def test_fake_root_project_root_placeholder_rejected(self):
        with self.assertRaises(PathValidationError):
            validate_path("[project_root]/core/shop/**")

    def test_fake_root_angle_bracket_placeholder_rejected(self):
        with self.assertRaises(PathValidationError):
            validate_path("<project_root>/core/shop/**")

    def test_fake_root_curly_brace_placeholder_rejected(self):
        with self.assertRaises(PathValidationError):
            validate_path("{project_root}/core/shop/**")

    def test_backslash_rejected(self):
        with self.assertRaises(PathValidationError):
            validate_path("core\\shop\\urls.py")

    def test_empty_path_rejected(self):
        with self.assertRaises(PathValidationError):
            validate_path("")


class TestStructuralFakeRootDetection(unittest.TestCase):
    """레포 디렉터리 이름에 의존하지 않는 가짜 루트 탐지 (결함 1 회귀).

    worktree 는 `.claude/worktrees/core-1701/` 처럼 레포 이름과 무관한 디렉터리
    이름을 쓰므로, repo_name 일치만으로는 가짜 루트가 전부 무사통과했다.
    """

    def setUp(self):
        self._tmp_dirs = []

    def tearDown(self):
        for tmp in self._tmp_dirs:
            shutil.rmtree(tmp, ignore_errors=True)

    def _make_repo(self, dirname):
        # 각 호출마다 별도 tmp 루트를 쓴다 — macOS(APFS) 기본값은 대소문자
        # 비구분 파일시스템이라, 같은 부모 아래 "Core-System" 과 "core-system" 을
        # 만들면 같은 경로로 충돌한다(테스트 픽스처의 문제이지 탐지 로직의 문제가
        # 아니다).
        tmp = tempfile.mkdtemp(prefix="drift-detect-pathspec-test-")
        self._tmp_dirs.append(tmp)
        repo_root = Path(tmp) / dirname
        (repo_root / "core").mkdir(parents=True)
        (repo_root / "documents").mkdir(parents=True)
        (repo_root / "infra").mkdir(parents=True)
        return repo_root

    def test_fake_root_rejected_regardless_of_repo_dirname(self):
        # 실측 결함: repo_name 일치(구 방식)는 디렉터리 이름이 "Core-System" 일
        # 때만 걸렸다. 구조 기반 탐지는 디렉터리 이름과 무관하게 걸려야 한다.
        for dirname in ("Core-System", "core-1701", "core-system"):
            with self.subTest(dirname=dirname):
                repo_root = self._make_repo(dirname)
                with self.assertRaises(PathValidationError):
                    validate_path(
                        "Core-System/core/shop/**",
                        repo_name=dirname,
                        repo_root=repo_root,
                    )

    def test_structural_detection_alone_catches_fake_root(self):
        # repo_name 을 아예 넘기지 않아도(=repo_name 일치 경로가 무력화된 상황을
        # 시뮬레이션) 구조 기반 탐지만으로 가짜 루트를 잡아야 한다.
        repo_root = self._make_repo("core-1701")
        with self.assertRaises(PathValidationError):
            validate_path("Core-System/core/shop/**", repo_root=repo_root)

    def test_real_top_level_dirs_pass_no_false_positive(self):
        # 오탐 방지: 레포 루트에 실재하는 최상위 디렉터리로 시작하는 정상 경로는
        # 구조 기반 탐지를 통과해야 한다.
        repo_root = self._make_repo("core-1701")
        validate_path("core/shop/urls.py", repo_root=repo_root)
        validate_path("documents/tasks/feature/x/implementation-plan.md", repo_root=repo_root)
        validate_path("infra/terraform/main.tf", repo_root=repo_root)

    def test_no_false_positive_when_neither_segment_exists(self):
        # 첫 세그먼트도, 두번째 세그먼트도 레포 루트에 없으면 가짜 루트로 보지
        # 않는다 (판정 근거 부족 — 단순 오탈자/신규 경로일 수 있음).
        repo_root = self._make_repo("core-1701")
        validate_path("nonexistent/also_nonexistent/file.py", repo_root=repo_root)

    def test_wildcard_only_prefix_skipped(self):
        # 와일드카드가 섞인 경로는 와일드카드 없는 접두부만으로 판정한다.
        # 접두부가 세그먼트 2개 미만이면 판정 근거가 없으므로 통과시킨다.
        repo_root = self._make_repo("core-1701")
        validate_path("**/migrations/**", repo_root=repo_root)
        validate_path("core/**", repo_root=repo_root)

    def test_missing_repo_root_is_fail_open(self):
        # repo_root 가 존재하지 않는 경로거나 파일시스템 접근이 불가하면 거부하지
        # 않는다 (fail-open, 스펙 §6).
        tmp = tempfile.mkdtemp(prefix="drift-detect-pathspec-test-")
        self._tmp_dirs.append(tmp)
        missing_root = Path(tmp) / "does-not-exist"
        validate_path("Core-System/core/shop/**", repo_root=missing_root)

    def test_repo_root_none_skips_structural_check(self):
        # repo_root 를 넘기지 않으면 구조 기반 탐지 자체가 동작하지 않는다
        # (기존 repo_name 기반 탐지만 적용) — 호출자가 repo_root 를 모르는 상황의
        # 하위호환.
        validate_path("Some-Unrelated-Prefix/core/shop/**")


class TestPathMatcher(unittest.TestCase):
    def test_star_matches_single_segment_only(self):
        matcher = PathMatcher(allow_patterns=["core/*/urls.py"])
        self.assertTrue(matcher.is_allowed("core/shop/urls.py"))
        self.assertFalse(matcher.is_allowed("core/shop/sub/urls.py"))

    def test_doublestar_matches_zero_or_more_segments(self):
        matcher = PathMatcher(allow_patterns=["core/shop/**"])
        self.assertTrue(matcher.is_allowed("core/shop/urls.py"))
        self.assertTrue(matcher.is_allowed("core/shop/sub/deep/file.py"))
        self.assertFalse(matcher.is_allowed("core/shopping/urls.py"))
        self.assertFalse(matcher.is_allowed("core/other/urls.py"))

    def test_exact_file_path_matches_only_that_file(self):
        matcher = PathMatcher(allow_patterns=["core/core/config/aws.py"])
        self.assertTrue(matcher.is_allowed("core/core/config/aws.py"))
        self.assertFalse(matcher.is_allowed("core/core/config/aws.py.bak"))
        self.assertFalse(matcher.is_allowed("core/core/config/sub/aws.py"))

    def test_deny_beats_allow(self):
        matcher = PathMatcher(
            allow_patterns=["core/shop/**"], deny_patterns=["core/shop/legacy/**"]
        )
        self.assertTrue(matcher.is_allowed("core/shop/urls.py"))
        self.assertFalse(matcher.is_allowed("core/shop/legacy/old.py"))

    def test_path_outside_allow_is_denied_by_default(self):
        matcher = PathMatcher(allow_patterns=["core/shop/**"])
        self.assertFalse(matcher.is_allowed("core/other_app/models.py"))


if __name__ == "__main__":
    unittest.main()
