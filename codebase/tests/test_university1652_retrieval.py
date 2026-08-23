from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from tools.university1652_retrieval import (
    acceptance_decision,
    gallery_images,
    l2_normalize,
    location_id,
    normalized_sha256,
    rank_top_k,
)


class University1652RetrievalTests(unittest.TestCase):
    def test_hash_normalization_is_strict(self) -> None:
        self.assertEqual(normalized_sha256("A" * 64, "hash"), "a" * 64)
        with self.assertRaises(ValueError):
            normalized_sha256("x" * 64, "hash")

    def test_gallery_discovery_is_sorted_and_uses_parent_as_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "0002").mkdir()
            (root / "0001").mkdir()
            (root / "0002" / "b.jpeg").write_bytes(b"b")
            (root / "0001" / "a.jpg").write_bytes(b"a")
            (root / "0001" / "ignore.txt").write_bytes(b"x")
            paths = gallery_images(root)
            self.assertEqual([path.name for path in paths], ["a.jpg", "b.jpeg"])
            self.assertEqual([location_id(path) for path in paths], ["0001", "0002"])

    def test_l2_normalization_rejects_zero_rows(self) -> None:
        with self.assertRaises(ValueError):
            l2_normalize(np.zeros((1, 2), dtype=np.float32))

    def test_ranking_uses_cosine_similarity(self) -> None:
        hypotheses = rank_top_k(
            np.asarray([1.0, 0.0]),
            np.asarray([[0.0, 1.0], [1.0, 0.0], [0.8, 0.2]]),
            ["wrong", "correct", "second"],
            ["a", "b", "c"],
            2,
        )
        self.assertEqual([item["location_id"] for item in hypotheses], ["correct", "second"])

    def test_acceptance_requires_thresholds_and_margin(self) -> None:
        hypotheses = [
            {"cosine_similarity": 0.90},
            {"cosine_similarity": 0.80},
        ]
        self.assertEqual(
            acceptance_decision(
                hypotheses, minimum_similarity=None, minimum_margin=None
            )[:2],
            (False, "thresholds_not_configured"),
        )
        accepted, reason, margin = acceptance_decision(
            hypotheses, minimum_similarity=0.85, minimum_margin=0.05
        )
        self.assertTrue(accepted)
        self.assertEqual(reason, "appearance_gate_passed_vio_confirmation_required")
        self.assertAlmostEqual(float(margin), 0.10)


if __name__ == "__main__":
    unittest.main()
