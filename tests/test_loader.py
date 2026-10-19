import os
import sys
import tempfile
import unittest

from drift_detect import loader


class TestEnsureOnPath(unittest.TestCase):
    def setUp(self):
        self._had_env = "DRIFT_DETECT_SRC" in os.environ
        self._orig_env_value = os.environ.get("DRIFT_DETECT_SRC")

    def tearDown(self):
        os.environ.pop("DRIFT_DETECT_SRC", None)
        if self._had_env:
            os.environ["DRIFT_DETECT_SRC"] = self._orig_env_value

    def test_default_path_resolves_to_real_src(self):
        # 이 테스트 프로세스 자체가 이미 그 경로에서 로드됐으므로 항상 참이어야 한다.
        self.assertTrue(loader.ensure_on_path())
        self.assertIn(loader._DEFAULT_SRC, sys.path)

    def test_env_override_missing_dir_returns_false(self):
        os.environ["DRIFT_DETECT_SRC"] = "/no/such/drift/detect/src/path"
        self.assertFalse(loader.ensure_on_path())

    def test_env_override_dir_without_package_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DRIFT_DETECT_SRC"] = tmp
            self.assertFalse(loader.ensure_on_path())

    def test_idempotent_does_not_duplicate_sys_path_entry(self):
        loader.ensure_on_path()
        count_before = sys.path.count(loader._DEFAULT_SRC)
        loader.ensure_on_path()
        count_after = sys.path.count(loader._DEFAULT_SRC)
        self.assertEqual(count_before, count_after)

    def test_never_raises_when_src_is_a_file_not_a_directory(self):
        with tempfile.NamedTemporaryFile() as fh:
            os.environ["DRIFT_DETECT_SRC"] = fh.name
            try:
                result = loader.ensure_on_path()
            except Exception as e:  # pragma: no cover - 실패해야 정상
                self.fail(f"ensure_on_path() 가 예외를 던짐: {e}")
            self.assertFalse(result)

    def test_never_raises_on_garbage_env_value(self):
        os.environ["DRIFT_DETECT_SRC"] = "///:::garbage:::???"
        try:
            result = loader.ensure_on_path()
        except Exception as e:  # pragma: no cover - 실패해야 정상
            self.fail(f"ensure_on_path() 가 예외를 던짐: {e}")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
