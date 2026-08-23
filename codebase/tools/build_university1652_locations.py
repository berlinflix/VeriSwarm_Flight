"""Build the compact University-1652 location manifest used by the web console.

The official dataset publishes one KML file per location and a separate ordered
building-name list.  This tool converts those sources into a small, deterministic
JSON artifact suitable for an offline Jetson deployment.
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path


SCHEMA = "veriswarm.geolocation.university1652_locations.v1"


def parse_name_list(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or len(parts[0]) != 4 or not parts[0].isdigit():
            raise ValueError(f"invalid name-list row {line_number}: {raw_line!r}")
        if parts[0] in result:
            raise ValueError(f"duplicate location ID in name list: {parts[0]}")
        result[parts[0]] = parts[1].strip()
    if not result:
        raise ValueError("name list is empty")
    return result


def parse_kml_coordinate(path: Path) -> tuple[float, float]:
    root = ET.parse(path).getroot()
    namespace = {"kml": "http://www.opengis.net/kml/2.2"}
    point = root.find(".//kml:Point/kml:coordinates", namespace)
    if point is None or not point.text:
        raise ValueError(f"KML has no Point coordinates: {path}")
    fields = [item.strip() for item in point.text.strip().split(",")]
    if len(fields) < 2:
        raise ValueError(f"invalid KML coordinate: {path}")
    longitude, latitude = float(fields[0]), float(fields[1])
    if not -180.0 <= longitude <= 180.0 or not -90.0 <= latitude <= 90.0:
        raise ValueError(f"KML coordinate is out of range: {path}")
    return latitude, longitude


def build_manifest(kml_dir: Path, name_list: Path) -> dict[str, object]:
    names = parse_name_list(name_list)
    locations: dict[str, dict[str, object]] = {}
    for location_id, name in sorted(names.items()):
        kml = kml_dir / f"{location_id}.kml"
        if not kml.is_file():
            raise ValueError(f"missing KML for location {location_id}: {kml}")
        latitude, longitude = parse_kml_coordinate(kml)
        locations[location_id] = {
            "name": name,
            "latitude": latitude,
            "longitude": longitude,
        }
    return {
        "schema": SCHEMA,
        "coordinate_system": "WGS84",
        "coordinate_meaning": "official University-1652 gallery location",
        "location_count": len(locations),
        "locations": locations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kml-dir", required=True, type=Path)
    parser.add_argument("--name-list", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    output = args.out.expanduser().resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {output}")
    manifest = build_manifest(
        args.kml_dir.expanduser().resolve(), args.name_list.expanduser().resolve()
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(f"wrote {manifest['location_count']} locations to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
