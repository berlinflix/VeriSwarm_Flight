from __future__ import annotations

import unittest

from tools.jetson_rescue_live import (
    AlertLatch,
    class_name,
    normalize_targets,
    overlap_clusters,
    percentile,
)


class JetsonRescueLiveTests(unittest.TestCase):
    def test_default_alert_enters_on_first_observation(self) -> None:
        latch = AlertLatch()
        self.assertEqual(latch.update(True), (True, "ALERT_ENTER"))
        self.assertEqual(latch.update(True), (True, None))

    def test_alert_clears_only_after_configured_empty_interval(self) -> None:
        latch = AlertLatch(enter_frames=1, exit_frames=3)
        latch.update(True)
        self.assertEqual(latch.update(False), (True, None))
        self.assertEqual(latch.update(False), (True, None))
        self.assertEqual(latch.update(False), (False, "ALERT_CLEAR"))

    def test_alert_entry_can_require_multiple_positive_frames(self) -> None:
        latch = AlertLatch(enter_frames=2, exit_frames=1)
        self.assertEqual(latch.update(True), (False, None))
        self.assertEqual(latch.update(True), (True, "ALERT_ENTER"))

    def test_alert_configuration_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            AlertLatch(enter_frames=0)
        with self.assertRaises(ValueError):
            AlertLatch(exit_frames=0)

    def test_target_names_are_casefolded(self) -> None:
        self.assertEqual(
            normalize_targets([" Person ", "SURVIVOR", ""]),
            {"person", "survivor"},
        )

    def test_class_name_supports_mapping_and_sequence(self) -> None:
        self.assertEqual(class_name({0: "person"}, 0), "person")
        self.assertEqual(class_name(["person"], 0), "person")
        self.assertEqual(class_name({}, 7), "7")

    def test_overlapping_boxes_form_diagnostic_cluster(self) -> None:
        boxes = [
            [0.0, 0.0, 100.0, 100.0],
            [5.0, 5.0, 95.0, 95.0],
            [200.0, 200.0, 260.0, 260.0],
        ]
        self.assertEqual(overlap_clusters(boxes), [(0, 1), (2,)])

    def test_percentile_uses_nearest_rank(self) -> None:
        self.assertEqual(percentile([1.0, 2.0, 3.0, 100.0], 0.95), 100.0)


if __name__ == "__main__":
    unittest.main()
