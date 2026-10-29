"""scripts/definition_cost.py --per-run: Part 3's per-run figures, rebuilt from the committed
baseline's traces and its saved measurement. No tokens. Owner: Vipin Mourya."""

import contextlib
import importlib.util
import io
import unittest

from harness import ROOT

SPEC = importlib.util.spec_from_file_location("definition_cost", ROOT / "scripts" / "definition_cost.py")


class PerRunTest(unittest.TestCase):
    def test_the_baselines_per_run_cost_is_rebuilt_from_its_traces(self):
        script = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(script)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            script.main(["--per-run", str(ROOT / "results" / "baseline"),
                         "--measured", str(ROOT / "docs" / "milestones" / "m1_definition_cost_2026-10-25.json")])
        text = out.getvalue()
        for figure in ("57 loop calls", "nonstandard_request 36, operator_review 21", "4,880.4 tokens per run",
                       "42.6%", "1,029.9", "t04_same_access_as 15,400 (45.6%)", "3,850.6 tokens per run"):
            self.assertIn(figure, text)
