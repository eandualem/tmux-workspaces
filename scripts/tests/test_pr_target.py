import unittest

from scripts.check_pr_target import valid_target


class PullRequestTargetTests(unittest.TestCase):
    def test_topic_and_promotion_routes(self):
        self.assertTrue(valid_target("develop", "feature/example", True))
        self.assertTrue(valid_target("develop", "feature/example", False))
        self.assertTrue(valid_target("main", "develop", True))
        self.assertFalse(valid_target("main", "feature/example", True))
        self.assertFalse(valid_target("main", "develop", False))
        self.assertFalse(valid_target("release", "feature/example", True))
