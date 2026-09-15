import unittest

from drift_detect.block_extractor import BlockExtractionError, extract_contract_blocks


class TestExtractContractBlocks(unittest.TestCase):
    def test_single_block_found(self):
        text = (
            "# plan\n\n"
            "## 5. 주요 파일 변경 예상\n\n"
            "```yaml contract\n"
            "contract_version: 1\n"
            "scope:\n"
            "  allow:\n"
            "    - core/shop/**\n"
            "```\n"
        )
        blocks = extract_contract_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertIn("contract_version: 1", blocks[0])

    def test_no_block_returns_empty_list(self):
        text = "# plan\n\n산문만 있고 계약 블록은 없다.\n"
        self.assertEqual(extract_contract_blocks(text), [])

    def test_two_blocks_both_returned_caller_treats_as_error(self):
        text = (
            "```yaml contract\n"
            "contract_version: 1\n"
            "scope:\n"
            "  allow:\n"
            "    - a/**\n"
            "```\n"
            "본문\n"
            "```yaml contract\n"
            "contract_version: 1\n"
            "scope:\n"
            "  allow:\n"
            "    - b/**\n"
            "```\n"
        )
        blocks = extract_contract_blocks(text)
        self.assertEqual(len(blocks), 2)

    def test_unrelated_code_fence_is_skipped(self):
        text = (
            "예시:\n"
            "```yaml\n"
            "some: example\n"
            "```\n"
            "```yaml contract\n"
            "contract_version: 1\n"
            "scope:\n"
            "  allow:\n"
            "    - core/shop/**\n"
            "```\n"
        )
        blocks = extract_contract_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertIn("core/shop/**", blocks[0])

    def test_unterminated_fence_raises(self):
        text = "```yaml contract\ncontract_version: 1\nscope:\n  allow:\n    - a/**\n"
        with self.assertRaises(BlockExtractionError):
            extract_contract_blocks(text)


if __name__ == "__main__":
    unittest.main()
