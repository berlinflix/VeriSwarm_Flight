from __future__ import annotations

import unittest

from tools.university1652_validate import ground_truth_rank, rank_metrics


class University1652ValidateTests(unittest.TestCase):
    def test_ground_truth_rank(self) -> None:
        hypotheses = [
            {"rank": 1, "location_id": "wrong"},
            {"rank": 2, "location_id": "correct"},
        ]
        self.assertEqual(ground_truth_rank(hypotheses, "correct"), 2)
        self.assertIsNone(ground_truth_rank(hypotheses, "missing"))

    def test_rank_metrics(self) -> None:
        self.assertEqual(
            rank_metrics([1, 2, 5, 6, None]),
            {
                "query_count": 5,
                "top1_count": 1,
                "top5_count": 3,
                "top1_rate": 0.2,
                "top5_rate": 0.6,
            },
        )

    def test_rank_metrics_requires_input(self) -> None:
        with self.assertRaises(ValueError):
            rank_metrics([])


if __name__ == "__main__":
    unittest.main()
