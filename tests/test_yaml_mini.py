import unittest

from drift_detect.yaml_mini import ContractParseError, parse_contract_yaml


class TestParseContractYaml(unittest.TestCase):
    def test_valid_minimal(self):
        text = "contract_version: 1\nscope:\n  allow:\n    - core/shop/**\n"
        result = parse_contract_yaml(text)
        self.assertEqual(result["contract_version"], 1)
        self.assertEqual(result["scope"]["allow"], ["core/shop/**"])
        self.assertEqual(result["scope"]["deny"], [])

    def test_valid_with_deny(self):
        text = (
            "contract_version: 1\n"
            "scope:\n"
            "  allow:\n"
            "    - core/shop/**\n"
            "    - core/core/config/aws.py\n"
            "  deny:\n"
            "    - core/common/**\n"
        )
        result = parse_contract_yaml(text)
        self.assertEqual(
            result["scope"]["allow"], ["core/shop/**", "core/core/config/aws.py"]
        )
        self.assertEqual(result["scope"]["deny"], ["core/common/**"])

    def test_unknown_key_under_scope_is_ignored(self):
        text = (
            "contract_version: 1\n"
            "scope:\n"
            "  allow:\n"
            "    - core/shop/**\n"
            "  forbid_patterns:\n"
            "    - eval(\n"
            "  deny:\n"
            "    - core/common/**\n"
        )
        result = parse_contract_yaml(text)
        self.assertEqual(result["scope"]["allow"], ["core/shop/**"])
        self.assertEqual(result["scope"]["deny"], ["core/common/**"])

    def test_unknown_scalar_key_before_scope_is_ignored(self):
        # 결함 재현: 미지 키가 scope 보다 앞이면 예전 파서는 통째로 실패했다.
        text = (
            "contract_version: 1\n"
            "unknown_future_key: x\n"
            "scope:\n"
            "  allow:\n"
            "    - core/shop/**\n"
        )
        result = parse_contract_yaml(text)
        self.assertEqual(result["scope"]["allow"], ["core/shop/**"])
        self.assertEqual(result["scope"]["deny"], [])

    def test_unknown_list_key_before_scope_is_ignored(self):
        text = (
            "contract_version: 1\n"
            "rejected:\n"
            "  - core/legacy/**\n"
            "scope:\n"
            "  allow:\n"
            "    - core/shop/**\n"
        )
        result = parse_contract_yaml(text)
        self.assertEqual(result["scope"]["allow"], ["core/shop/**"])

    def test_unknown_key_after_scope_is_ignored(self):
        text = (
            "contract_version: 1\n"
            "scope:\n"
            "  allow:\n"
            "    - core/shop/**\n"
            "watch:\n"
            "  - core/common/**\n"
        )
        result = parse_contract_yaml(text)
        self.assertEqual(result["scope"]["allow"], ["core/shop/**"])

    def test_unknown_nested_mapping_key_before_scope_is_ignored(self):
        text = (
            "contract_version: 1\n"
            "phase_gates:\n"
            "  build: pending\n"
            "  release: pending\n"
            "scope:\n"
            "  allow:\n"
            "    - core/shop/**\n"
        )
        result = parse_contract_yaml(text)
        self.assertEqual(result["scope"]["allow"], ["core/shop/**"])

    def test_missing_contract_version_raises(self):
        text = "scope:\n  allow:\n    - a/**\n"
        with self.assertRaises(ContractParseError):
            parse_contract_yaml(text)

    def test_missing_scope_raises(self):
        text = "contract_version: 1\n"
        with self.assertRaises(ContractParseError):
            parse_contract_yaml(text)

    def test_empty_allow_raises(self):
        text = "contract_version: 1\nscope:\n  deny:\n    - a/**\n"
        with self.assertRaises(ContractParseError):
            parse_contract_yaml(text)

    def test_malformed_list_item_raises(self):
        text = "contract_version: 1\nscope:\n  allow:\n    core/shop/**\n"
        with self.assertRaises(ContractParseError):
            parse_contract_yaml(text)

    def test_empty_body_raises(self):
        with self.assertRaises(ContractParseError):
            parse_contract_yaml("")


if __name__ == "__main__":
    unittest.main()
