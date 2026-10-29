import unittest

from progress import BuildProgress


class BuildProgressTest(unittest.TestCase):
    def test_counter_restarts_do_not_move_overall_progress_backwards(self):
        progress = BuildProgress()
        progress.begin("sdk_build")
        progress.update(80, 100)
        before = progress.value
        progress.update(1, 20)
        self.assertEqual(progress.value, before)
        progress.update(20, 20)
        self.assertGreater(progress.value, before)

    def test_unknown_total_does_not_invent_completion(self):
        progress = BuildProgress()
        progress.begin("sdk_build")
        before = progress.value
        progress.update(500, None)
        self.assertEqual(progress.value, before)
        self.assertFalse(progress.known)
        progress.update(0, 0)
        self.assertEqual(progress.value, before)

    def test_only_successful_finish_can_reach_one_hundred(self):
        progress = BuildProgress()
        progress.begin("cleanup")
        progress.update(999, 1)
        self.assertLess(progress.value, 100)
        progress.finish()
        self.assertEqual(progress.value, 100)


if __name__ == "__main__":
    unittest.main()
