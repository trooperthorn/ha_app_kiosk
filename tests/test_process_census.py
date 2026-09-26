import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'wayland-kiosk'))

from crash_monitor import describe_census  # noqa: E402


def census(processes, targets):
    return {'processes': dict(processes), 'targets': dict(targets)}


class DescribeCensusTests(unittest.TestCase):
    def test_first_census_and_no_change_are_silent(self):
        current = census({1: 'browser', 2: 'gpu', 3: 'renderer'}, {'A': 'page'})
        self.assertIsNone(describe_census(None, current))
        self.assertIsNone(describe_census(current, current))

    def test_spare_renderer_death_keeps_page_target(self):
        before = census({1: 'browser', 2: 'gpu', 3: 'renderer', 4: 'renderer'}, {'A': 'page'})
        after = census({1: 'browser', 2: 'gpu', 3: 'renderer', 5: 'renderer'}, {'A': 'page'})
        line = describe_census(before, after)
        self.assertIn('gone=4:renderer', line)
        self.assertIn('new=5:renderer', line)
        self.assertIn('page_target_survived=yes', line)
        self.assertIn('now=browserx1,gpux1,rendererx2', line)

    def test_page_renderer_crash_reports_lost_target(self):
        before = census({1: 'browser', 3: 'renderer'}, {'A': 'page', 'W': 'service_worker'})
        after = census({1: 'browser', 6: 'renderer'}, {'B': 'page'})
        line = describe_census(before, after)
        self.assertIn('targets_gone=page,service_worker', line)
        self.assertIn('targets_new=page', line)
        self.assertIn('page_target_survived=no', line)


if __name__ == '__main__':
    unittest.main()
