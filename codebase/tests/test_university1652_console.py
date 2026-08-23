from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from tools.build_university1652_locations import (
    build_manifest,
    parse_kml_coordinate,
    parse_name_list,
)
from tools.university1652_console import (
    enrich_hypotheses,
    extract_upload,
    handler_factory,
    load_locations,
    safe_upload_suffix,
)
from tools.hazard_atlas import hazard_asset, hazard_atlas_html


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

    def test_hazard_atlas_is_explicitly_advisory_and_has_all_layers(self) -> None:
        page = hazard_atlas_html(
            Path("assets/hazards"), google_key="browser-demo-key"
        ).decode("utf-8")
        self.assertIn("India Rescue Coverage Atlas", page)
        self.assertIn("not an operational hazard product", page)
        self.assertIn("Flood corridors", page)
        self.assertIn("Seismic corridors", page)
        self.assertIn("Landslide belts", page)
        self.assertIn("VIO/IMU/temporal consistency", page)
        self.assertIn("/assets/hazards/flood-affected-reference.png", page)
        self.assertIn("maps.googleapis.com/maps/api/js", page)
        self.assertIn('const googleKey="browser-demo-key"', page)
        self.assertIn("new google.maps.Polyline", page)
        self.assertIn("Guwahati Multi-hazard Pilot", page)
        self.assertIn("not a computed risk surface", page)
        self.assertNotIn("tile.openstreetmap.org", page)

    def test_hazard_assets_are_allow_listed(self) -> None:
        path, content_type = hazard_asset(
            Path("assets/hazards"), "seismic-zones-reference.png"
        )
        self.assertTrue(path.is_file())
        self.assertEqual(content_type, "image/png")
        with self.assertRaises(ValueError):
            hazard_asset(Path("assets/hazards"), "../secrets.txt")

    def test_server_exposes_hazard_atlas_and_reference_assets(self) -> None:
        runtime = SimpleNamespace(
            model_sha256="a" * 64,
            locations={"0000": {}},
            device="cpu",
        )
        handler = handler_factory(
            runtime,  # type: ignore[arg-type]
            max_upload_bytes=1024,
            google_key="",
            hazard_asset_root=Path("assets/hazards"),
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            with urllib.request.urlopen(f"http://{host}:{port}/hazards") as response:
                self.assertEqual(response.status, 200)
                self.assertIn(b"not an operational hazard product", response.read())
            with urllib.request.urlopen(
                f"http://{host}:{port}/assets/hazards/seismic-zones-reference.png"
            ) as response:
                self.assertEqual(response.headers.get_content_type(), "image/png")
                self.assertGreater(len(response.read()), 1000)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
