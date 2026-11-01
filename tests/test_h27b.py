import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts.h27b import history_eligible, exclusion_reasons, verdict, run
from tests.test_paper_daily import isolated


class H27bTests(unittest.TestCase):
    def test_history_eligibility_uses_features_only_and_reports_reasons(self):
        p = pd.DataFrame(dict(beta60=[0,1,np.nan,1],vol20=[.02,.02,.02,0],turn20=[1e8,np.nan,1e8,1e8],close=[1,999,2,3]))
        self.assertEqual(history_eligible(p).tolist(),[True,False,False,False])
        self.assertEqual(exclusion_reasons(p).tolist(),['','turn20','beta60','vol20'])
        p.close = np.nan
        self.assertEqual(history_eligible(p).tolist(),[True,False,False,False])

    def test_coverage_and_power_precede_strict_evidence_gate(self):
        self.assertEqual(verdict(.799,100,100,3),'Unresolved')
        self.assertEqual(verdict(.9,39,100,3),'Unresolved')
        self.assertEqual(verdict(.8,40,50,2.5),'Failed')
        self.assertEqual(verdict(.8,40,51,2.49),'Failed')
        self.assertEqual(verdict(.8,40,51,2.5),'Passed')

    def test_second_run_guard_preserves_evidence(self):
        with isolated() as root:
            (root/'data/h27b').mkdir(parents=True)
            (root/'RUNBOOK.md').write_text('## H27b: frozen registration')
            p = root/'data/h27b/started.json'
            p.write_text('original')
            with patch('scripts.h27b.ROOT',root):
                with self.assertRaises(FileExistsError):run()
            self.assertEqual(p.read_text(),'original')
