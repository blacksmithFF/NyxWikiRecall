"""The manifest must keep the hook loading beside any memory provider."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ManifestTest(unittest.TestCase):
    def test_kind_is_declared_standalone(self):
        # __init__.py mentions register_memory_provider. Hermes reads an undeclared kind from that
        # text as exclusive and skips the general load, so pre_llm_call is never registered.
        lines = [line.strip() for line in (ROOT / "plugin.yaml").read_text(encoding="utf-8").splitlines()]
        self.assertIn("kind: standalone", lines)

    def test_hook_is_listed(self):
        text = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
        self.assertIn("- pre_llm_call", text)


if __name__ == "__main__":
    unittest.main()