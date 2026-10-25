import math, unittest
from nbalab import safe_eval as se, registry


class SafeEval(unittest.TestCase):
    def test_arithmetic(self):
        self.assertAlmostEqual(se.evaluate("a*0.7 + b*0.3", {"a": 10.0, "b": 20.0}), 13.0)
        self.assertAlmostEqual(se.evaluate("sqrt(abs(x)) + x**2", {"x": -4.0}), 18.0)

    def test_division_by_zero_is_nan_not_exception(self):
        self.assertTrue(math.isnan(float(se.evaluate("a/b", {"a": 1.0, "b": 0.0}))))

    def test_rejects_unsafe_syntax(self):
        for bad in ["__import__('os').system('x')", "a.__class__", "a[0]", "open('f')", "lambda: 1", "[x for x in a]", "a if b else c", "exec('1')"]:
            with self.assertRaises(se.FormulaError, msg=bad):
                se.evaluate(bad, {"a": 1.0, "b": 2.0, "c": 3.0})

    def test_unknown_name_and_huge_exponent_rejected(self):
        with self.assertRaises(se.FormulaError):
            se.evaluate("zzz + 1", {"a": 1.0})
        with self.assertRaises(se.FormulaError):
            se.evaluate("a**99", {"a": 2.0})

    def test_names_in(self):
        self.assertEqual(se.names_in("pts_per36*0.5 + ast_per36"), {"pts_per36", "ast_per36"})


class Registry(unittest.TestCase):
    def test_kinds_and_unavailable_metrics_are_explicit(self):
        ms = registry.all_metrics()
        self.assertGreaterEqual(len(ms), 70)
        kinds = {m.kind for m in ms}
        self.assertTrue({"observed", "derived", "inferred", "projected", "unavailable"} <= kinds)
        for m in ms:
            if m.kind == "unavailable":
                self.assertTrue(m.unavailable_reason, m.id)
            self.assertTrue(m.formula_version)

    def test_search_finds_aliases(self):
        self.assertTrue(any(m.id == "ts_pct" for m in registry.search("true shooting", 10)))


if __name__ == "__main__":
    unittest.main()
