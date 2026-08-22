from __future__ import annotations

import unittest

from tools.jetson_camera_acceptance import (
    box_overlap,
    class_name,
    decide_case,
    normalize_targets,
    overlap_clusters,
    percentile,
)


class JetsonCameraAcceptanceTests(unittest.TestCase):
    def test_empty_case_passes_only_with_zero_target_frames(self) -> None:
        self.assertTrue(
            decide_case(
                case="empty", target_frame_count=0, frame_count=120, minimum_rate=0.8
            ).passed
        )
        failed = decide_case(
            case="empty", target_frame_count=1, frame_count=120, minimum_rate=0.8
        )
        self.assertFalse(failed.passed)
        self.assertIn("1 frames", failed.reason)

    def test_positive_case_uses_detection_rate_threshold(self) -> None:
        self.assertTrue(
            decide_case(
                case="occluded",
                target_frame_count=96,
                frame_count=120,
                minimum_rate=0.8,
            ).passed
        )
        self.assertFalse(
            decide_case(
                case="full-body-distance",
                target_frame_count=95,
                frame_count=120,
                minimum_rate=0.8,
            ).passed
        )

    def test_target_names_are_trimmed_case_insensitively(self) -> None:
        self.assertEqual(
            normalize_targets([" Person ", "SURVIVOR", ""]),
            {"person", "survivor"},
        )

    def test_class_name_supports_mapping_and_sequence(self) -> None:
        self.assertEqual(class_name({0: "person"}, 0), "person")
        self.assertEqual(class_name(["person"], 0), "person")
        self.assertEqual(class_name({}, 7), "7")

    def test_nearest_rank_percentile(self) -> None:
        self.assertEqual(percentile([1.0, 2.0, 3.0, 100.0], 0.95), 100.0)
        with self.assertRaises(ValueError):
            percentile([], 0.95)

    def test_overlap_geometry_matches_nested_occlusion_boxes(self) -> None:
        small = [387.82, 0.61, 452.85, 143.66]
        large = [388.17, 0.29, 485.62, 145.15]
        iou, containment = box_overlap(small, large)
        self.assertGreater(iou, 0.60)
        self.assertGreater(containment, 0.98)

    def test_overlap_clusters_retain_indices_and_separate_distant_boxes(self) -> None:
        boxes = [
            [0.0, 0.0, 10.0, 10.0],
            [1.0, 1.0, 9.0, 9.0],
            [100.0, 100.0, 120.0, 120.0],
        ]
        self.assertEqual(overlap_clusters(boxes), [(0, 1), (2,)])


if __name__ == "__main__":
    unittest.main()
