import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import tuesday_practice as p


class TuesdayPracticeTests(unittest.TestCase):
    def test_budget_includes_cost_and_stressed_entry_resizes(self):
        bars = [[935,100,100.6,99.6,100], [940,100,101,98,100], [1510,110,110,110,110]]
        for side in ('long','short'):
            for rate in (.0015,.0025,.004):
                for worse in (0,.005):
                    t = p.simulate('X',bars,side,rate,worse)
                    self.assertLessEqual(t['qty']*t['entry']*(.01+rate),50)
                    self.assertLessEqual(t['qty']*t['entry'],5000)
        boundary = [[935,101,102,100,101],[1510,101,102,100,101]]
        self.assertNotEqual(p.simulate('X',boundary,'long',.0015)['qty'],
                            p.simulate('X',boundary,'long',.0015,.005)['qty'])

    def test_entry_bar_and_gap_stop(self):
        bars = [[935,100,101,98,100], [940,90,91,89,90], [1510,110,110,110,110]]
        t = p.simulate('X',bars,'long',.004)
        self.assertEqual((t['exit_hm'],t['exit']),(935,99))
        bars[0] = [935,100,100.5,99.5,100]
        t = p.simulate('X',bars,'long',.004)
        self.assertEqual((t['exit_hm'],t['exit']),(940,90))
        self.assertLess(t['net'],-50)  # Risk budget is not a gap guarantee.

    def test_agreement_requires_direction_and_both_evidence(self):
        a = {'X':{'side':'long','sufficient_evidence':True}}
        for side, evidence, expected in [('long',True,'long'),('long',False,'skip'),('short',True,'skip'),('skip',True,'skip')]:
            self.assertEqual(p.agreement(a,{'X':{'side':side,'sufficient_evidence':evidence}})['X']['side'],expected)

    def fixture(self, root, kind='comparison'):
        folder = root/'tuesday_compare_1'; folder.mkdir()
        visible = {'kind':kind,'stocks':{'X':{}},'packet':'tuesday_compare_1'}
        p.write(folder/'visible.json',visible)
        p.write(folder/'sealed.json',{'secret_outcome':123456789})
        p.write(folder/'manifest.json',{'visible.json':p.sha(folder/'visible.json'),
                'scorer.py':p.sha(Path(p.__file__)),'sealed.json':p.sha(folder/'sealed.json')})
        plan = {'stocks':{'X':{'side':'skip','sufficient_evidence':False,'observations':['Observed'],
                'reason':'Missing edge','missing_evidence':['Evidence'],'invalidation':'Need verified edge'}}}
        return folder,plan

    def test_missing_claude_blocks_without_reading_or_hashing_sealed(self):
        with tempfile.TemporaryDirectory() as t, patch.object(p,'OUT',Path(t)):
            f,plan = self.fixture(Path(t))
            p.commit('tuesday_compare_1','assistant',plan,{'test':True})
            (f/'sealed.json').unlink()  # Missing file must not be the reported blocker.
            for action in (p.score,p.review_input):
                with self.assertRaisesRegex(ValueError,'Pending actual claude'):
                    action('tuesday_compare_1')
            self.assertFalse((f/'started.json').exists())

    def test_review_is_anonymous_has_no_hidden_path_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as t, patch.object(p,'OUT',Path(t)):
            f,plan = self.fixture(Path(t))
            for actor in ('assistant','claude'):
                p.commit('tuesday_compare_1',actor,plan,{'test_secret':'do not transmit'})
            (f/'sealed.json').unlink()
            text = json.dumps(p.review_input('tuesday_compare_1'))
            for secret in ('123456789','test_secret','provenance','actor'):
                self.assertNotIn(secret,text)
            self.assertIn('anonymous_plans',text)
            (f/'assistant.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'Committed plan changed'):
                p.review_input('tuesday_compare_1')

    def test_score_is_single_use_and_zero_participation_not_fake_trades(self):
        with tempfile.TemporaryDirectory() as t, patch.object(p,'OUT',Path(t)):
            f,plan = self.fixture(Path(t),'solo')
            (f/'visible.json').write_text(json.dumps({'kind':'solo','stocks':{'X':{'round_trip_cost_rate':.004}}}))
            (f/'sealed.json').write_text(json.dumps({'source_day':'2024-02-06','stocks':{'X':[[935,100,101,99,100],[1510,100,101,99,100]]}}))
            m=p.read(f/'manifest.json');m['visible.json']=p.sha(f/'visible.json');m['sealed.json']=p.sha(f/'sealed.json')
            (f/'manifest.json').write_text(json.dumps(m))
            p.commit('tuesday_compare_1','assistant',plan,{'test':True})
            p.write(f/'review.json',{'ok':False})
            with patch('builtins.print'):
                p.score('tuesday_compare_1')
            result=p.read(f/'result.json')
            self.assertEqual(result['accounts']['assistant']['base'],{'trades':[],'net':0})
            with self.assertRaises(FileExistsError):
                p.score('tuesday_compare_1')


if __name__ == '__main__':
    unittest.main()
