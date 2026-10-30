"""Repeat the eight unchanged valid expectations from the previous audit.
The ninth original expectation used a formatting-only mismatch; see residuals.py replacement.
"""
import importlib.util
from pathlib import Path
import sys
import unittest
root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
path = root / 'docs/audit_evidence/2026-10-30-p17c45/reproduce.py'
spec = importlib.util.spec_from_file_location('previous_audit', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
suite = unittest.TestSuite(module.Residuals(name) for name in unittest.defaultTestLoader.getTestCaseNames(module.Residuals)
                           if name != 'test_enqueued_v1_neighbor_can_reach_verification')
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
