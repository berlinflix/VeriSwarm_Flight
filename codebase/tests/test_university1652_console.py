from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.build_university1652_locations import (
    build_manifest,
    parse_kml_coordinate,
    parse_name_list,
)
from tools.university1652_console import (
    enrich_hypotheses,
    extract_upload,
    load_locations,
    safe_upload_suffix,
)


KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>
<Point><coordinates>-81.8520187995621,41.367270200649,0</coordinates></Point>
</Placemark></Document></kml>"""


class University1652ConsoleTests(unittest.TestCase):
    def test_builds_location_manifest_from_official_source_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kml_dir = root / "kml"
            kml_dir.mkdir()
            (kml_dir / "0000.kml").write_text(KML, encoding="utf-8")
            names = root / "names.txt"
            names.write_text("0000 Example Hall, Example University\n", encoding="utf-8")

            self.assertEqual(parse_name_list(names)["0000"], "Example Hall, Example University")
            self.assertEqual(parse_kml_coordinate(kml_dir / "0000.kml"), (41.367270200649, -81.8520187995621))
            manifest = build_manifest(kml_dir, names)
            self.assertEqual(manifest["location_count"], 1)
            self.assertEqual(manifest["locations"]["0000"]["name"], "Example Hall, Example University")

    def test_load_locations_rejects_wrong_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "locations.json"
            path.write_text(json.dumps({"schema": "wrong", "locations": {}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_locations(path)

    def test_multipart_upload_extracts_bytes_and_strips_client_path(self) -> None:
        boundary = "test-boundary"
        payload = b"\xff\xd8query-image\xff\xd9"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="image"; filename="../../query.jpg"\r\n'
            "Content-Type: image/jpeg\r\n\r\n"
        ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
        data, filename = extract_upload(body, f"multipart/form-data; boundary={boundary}")
        self.assertEqual(data, payload)
        self.assertEqual(filename, "query.jpg")

    def test_upload_suffix_is_restricted(self) -> None:
        self.assertEqual(safe_upload_suffix("view.JPEG"), ".jpg")
        with self.assertRaises(ValueError):
            safe_upload_suffix("payload.svg")

    def test_hypotheses_receive_names_coordinates_and_safe_image_urls(self) -> None:
        value = enrich_hypotheses(
            [
                {
                    "rank": 1,
                    "location_id": "0038",
                    "gallery_path": "/not/exposed/to/browser.jpg",
                    "cosine_similarity": 0.91,
                }
            ],
            {
                "0038": {
                    "name": "Boesel Musical Arts Center",
                    "latitude": 41.367,
                    "longitude": -81.852,
                }
            },
        )[0]
        self.assertEqual(value["gallery_image_url"], "/api/gallery/0038")
        self.assertNotIn("gallery_path", value)
        self.assertIn("41.3670000", value["openstreetmap_url"])
        self.assertEqual(value["name"], "Boesel Musical Arts Center")


if __name__ == "__main__":
    unittest.main()
