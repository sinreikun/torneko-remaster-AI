import unittest
from autoplayer.nextgen.pace import PaceController

class PaceContracts(unittest.TestCase):
    def feed(self,p,ms=1000,**kw):return p.observe('scope',350,ms=ms,quality=kw.get('quality',.9),match=kw.get('match',True),kind=kw.get('kind','normal'))
    def test_trial_accepts_faster_wait_and_has_no_turn_rate_cap(self):
        p=PaceController()
        for _ in range(8):event=self.feed(p)
        self.assertEqual(event['status'],'trial_started');self.assertLess(p.wait('scope',350),350)
        for _ in range(8):event=self.feed(p,500)
        self.assertEqual(event['status'],'accepted');self.assertGreater(p.last['rolling_turns_per_minute'],40)
        self.assertLess(p.wait('scope',350),350)
    def test_bad_prediction_rolls_back_and_no_effect_alone_is_not_failure(self):
        p=PaceController()
        for _ in range(8):self.feed(p,kind='no_change')
        event=self.feed(p,quality=.3)
        self.assertEqual(event['status'],'rollback');self.assertEqual(p.wait('scope',350),350)
        self.assertEqual(p.wait('other',140),140)
    def test_no_speed_gain_rolls_back_and_memory_is_bounded(self):
        p=PaceController()
        for _ in range(16):event=self.feed(p)
        self.assertEqual(event['status'],'rollback')
        for n in range(300):p.wait(n,140)
        self.assertEqual(len(p.profiles),256)

    def test_revalidation_and_trial_deadline_restore_baseline(self):
        p=PaceController();key=('screen',('a',),False)
        for _ in range(8):p.observe(key,140,ms=1000,quality=.9,match=True,kind='normal')
        self.assertLess(p.wait(key,140),140)
        self.assertEqual(p.invalidate_context('screen'),1);self.assertEqual(p.wait(key,140),140)
        q=PaceController()
        for _ in range(8):self.feed(q)
        q.tick+=64
        self.assertEqual(q.wait('scope',350),350)

    def test_existing_model_errors_do_not_block_or_falsely_fail_pace_trial(self):
        p=PaceController()
        for n in range(8):event=self.feed(p,match=bool(n%2))
        self.assertEqual(event['status'],'trial_started')
        for n in range(8):event=self.feed(p,500,match=bool(n%2))
        self.assertEqual(event['status'],'accepted')
        self.assertEqual(p.last['baseline_samples'],0)

    def test_prediction_accuracy_drop_compared_to_baseline_rolls_back(self):
        p=PaceController()
        for _ in range(8):self.feed(p)
        event=self.feed(p,500,match=False)
        self.assertEqual(event['status'],'rollback')
        self.assertEqual(p.wait('scope',350),350)
