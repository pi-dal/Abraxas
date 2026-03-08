import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

from core.bot import build_system_prompt


class ExecutionProtocolTests(unittest.TestCase):
    def test_execution_protocol_is_injected_into_system_prompt(self):
        prompt = build_system_prompt()
        self.assertIn("CRITICAL EXECUTION RULE", prompt)
        self.assertIn("[PLAN]", prompt)
        self.assertIn("[CRITIQUE]", prompt)

    def test_execution_protocol_mentions_override_clause(self):
        prompt = build_system_prompt()
        self.assertIn("overridden only by explicit user instruction", prompt)

    def test_execution_protocol_lists_high_risk_targets(self):
        prompt = build_system_prompt()
        self.assertIn("bash:", prompt)
        self.assertIn("write:", prompt)
        self.assertIn("src/core or src/channel", prompt)


if __name__ == "__main__":
    unittest.main()
