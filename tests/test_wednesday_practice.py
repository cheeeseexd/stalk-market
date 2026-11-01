import unittest
from scripts.wednesday_practice import candidates, pick, questions_for, simulate


class WednesdayPracticeTests(unittest.TestCase):
    def bars(self):
        return [[930,100,100.2,99.8,100],[935,100,100.5,99.5,100],[940,100,103,97,100],[1510,101,101,101,101]]

    def test_unresolved_dual_touch_is_stop_first(self):
        t=simulate('X',self.bars(),{'side':'long','stop_pct':.01,'target_pct':.02},.004)
        self.assertTrue(t['ambiguous_bar_stop_first'])
        self.assertEqual((t['trade']['exit'],t['trade']['reason']),(99,'stop'))

    def test_target_at_open_precedes_later_stop_but_no_favorable_improvement(self):
        b=self.bars();b[2]=[940,104,105,97,100]
        t=simulate('X',b,{'side':'long','stop_pct':.01,'target_pct':.02},.004)
        self.assertEqual((t['trade']['exit'],t['trade']['reason']),(102,'target'))
        self.assertFalse(t['ambiguous_bar_stop_first'])

    def test_gap_stop_and_entry_bar_are_included(self):
        b=self.bars();b[1]=[935,100,101,98,100]
        plan={'side':'long','stop_pct':.01,'target_pct':.02}
        self.assertEqual(simulate('X',b,plan,.004)['trade']['exit_hm'],935)
        b=self.bars();b[2]=[940,90,91,89,90]
        t=simulate('X',b,plan,.004)
        self.assertEqual(t['trade']['exit'],90)
        self.assertLess(t['trade']['net'],-50)

    def test_short_target_and_cost_inclusive_budget_under_stress(self):
        b=self.bars();b[1]=[935,100,100.3,99.5,100];b[2]=[940,100,100.3,96,97]
        plan={'side':'short','stop_pct':.01,'target_pct':.02}
        for worse in (0,.005):
            t=simulate('X',b,plan,.0025,worse)
            self.assertEqual(t['trade']['reason'],'target')
            self.assertLessEqual(t['planned_loss_including_costs'],50)
            self.assertAlmostEqual(t['levels']['target'],t['levels']['entry']*.98)
            self.assertLessEqual(t['trade']['qty']*t['trade']['entry'],5000)

    def test_deadline_flattens_at_open_not_later_extremes(self):
        b=self.bars();b[2]=[940,100,100.5,99.5,100];b[-1]=[1510,100,120,80,100]
        t=simulate('X',b,{'side':'long','stop_pct':.01,'target_pct':.02},.004)
        self.assertEqual((t['trade']['reason'],t['trade']['exit']),('flatten',100))

    def test_actual_numeric_ranking_is_required_and_quality_precedes_probability(self):
        facts={'reference':100,'candidates':candidates(.02)}
        answers={}
        for i,c in enumerate(facts['candidates']):
            answers[c['id']+'_quality']={'score':4 if i==3 else 2}
            answers[c['id']+'_target_first']={'noul':.3 if i==3 else .9}
        self.assertEqual(pick(facts,answers)['id'],facts['candidates'][3]['id'])
        del answers[facts['candidates'][0]['id']+'_quality']
        with self.assertRaises(ValueError):pick(facts,answers)
        self.assertEqual(len(questions_for(facts)),27)


if __name__=='__main__':unittest.main()
