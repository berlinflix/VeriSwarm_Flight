"""Jetson-hosted University-1652 visual geolocation console.

The browser uploads one drone-view image.  The Jetson embeds it, ranks the
frozen satellite gallery, attaches the official University-1652 WGS84
coordinates and returns a map-ready top-k result.  Retrieval is deliberately
reported as a *visual hypothesis*: it is not a GPS measurement and must not be
fed to flight control without temporal/VIO consistency checks.

The service uses only Python's standard-library HTTP server.  The heavy model
and gallery cache are loaded lazily on the first request, and may be unloaded
from the UI to release GPU memory.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np

try:
    from tools.hazard_atlas import hazard_asset, hazard_atlas_html
except ModuleNotFoundError:  # Standalone Jetson handoff beside this module.
    from hazard_atlas import hazard_asset, hazard_atlas_html  # type: ignore[no-redef]

try:
    from tools.rescue_video import (
        RescueVideoRuntime,
        extract_video_upload,
        rescue_video_html,
    )
except ModuleNotFoundError:  # Standalone Jetson handoff beside this module.
    from rescue_video import (  # type: ignore[no-redef]
        RescueVideoRuntime,
        extract_video_upload,
        rescue_video_html,
    )

try:
    from tools.university1652_retrieval import (
        _load_cache,
        acceptance_decision,
        build_encoder,
        encode_pil_images,
        gallery_images,
        gallery_manifest_sha256,
        location_id,
        normalized_sha256,
        rank_top_k,
        sha256_file,
    )
except ModuleNotFoundError:  # Standalone Jetson handoff beside the retrieval module.
    from university1652_retrieval import (  # type: ignore[no-redef]
        _load_cache,
        acceptance_decision,
        build_encoder,
        encode_pil_images,
        gallery_images,
        gallery_manifest_sha256,
        location_id,
        normalized_sha256,
        rank_top_k,
        sha256_file,
    )


SCHEMA = "veriswarm.geolocation.console_result.v1"
SUPPORTED_UPLOADS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
DEFAULT_MAX_UPLOAD_MIB = 15


def load_locations(path: Path) -> dict[str, dict[str, object]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid location manifest: {error}") from error
    if value.get("schema") != "veriswarm.geolocation.university1652_locations.v1":
        raise ValueError("unsupported location-manifest schema")
    locations = value.get("locations")
    if not isinstance(locations, dict) or not locations:
        raise ValueError("location manifest contains no locations")
    for location_id_value, item in locations.items():
        if not re.fullmatch(r"\d{4}", str(location_id_value)) or not isinstance(item, dict):
            raise ValueError("location manifest has an invalid record")
        latitude = float(item.get("latitude"))
        longitude = float(item.get("longitude"))
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError(f"location {location_id_value} is out of range")
    return locations


def safe_upload_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.casefold()
    if suffix not in SUPPORTED_UPLOADS:
        raise ValueError("upload must be JPEG, PNG or WebP")
    return ".jpg" if suffix == ".jpeg" else suffix


def extract_upload(body: bytes, content_type: str) -> tuple[bytes, str]:
    match = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type)
    if not match:
        raise ValueError("expected a multipart image upload")
    boundary = ("--" + (match.group(1) or match.group(2)).strip()).encode()
    for part in body.split(boundary):
        split_at = part.find(b"\r\n\r\n")
        if split_at < 0:
            continue
        headers = part[:split_at].decode("utf-8", "replace")
        filename_match = re.search(r'filename="([^"]*)"', headers)
        if not filename_match:
            continue
        filename = Path(filename_match.group(1)).name or "query.jpg"
        payload = part[split_at + 4 :]
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        if payload:
            return payload, filename
    raise ValueError("upload contains no image file")


def enrich_hypotheses(
    hypotheses: list[dict[str, object]],
    locations: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for hypothesis in hypotheses:
        location_id_value = str(hypothesis["location_id"])
        location = locations.get(location_id_value)
        if location is None:
            raise ValueError(f"no coordinate metadata for location {location_id_value}")
        result.append(
            {
                "rank": int(hypothesis["rank"]),
                "location_id": location_id_value,
                "name": str(location["name"]),
                "latitude": float(location["latitude"]),
                "longitude": float(location["longitude"]),
                "cosine_similarity": float(hypothesis["cosine_similarity"]),
                "gallery_image_url": f"/api/gallery/{location_id_value}",
                "openstreetmap_url": (
                    "https://www.openstreetmap.org/?mlat="
                    f"{float(location['latitude']):.7f}&mlon={float(location['longitude']):.7f}"
                    f"#map=18/{float(location['latitude']):.7f}/{float(location['longitude']):.7f}"
                ),
                "google_maps_url": (
                    "https://www.google.com/maps/search/?api=1&query="
                    f"{float(location['latitude']):.7f},{float(location['longitude']):.7f}"
                ),
            }
        )
    return result


class RetrievalRuntime:
    """Lazy, serialized access to the University-1652 encoder and cache."""

    def __init__(
        self,
        *,
        checkpoint: Path,
        expected_checkpoint_sha256: str,
        gallery_root: Path,
        gallery_cache: Path,
        locations_path: Path,
        evidence_dir: Path,
        device: str,
        top_k: int,
        minimum_similarity: float | None,
        minimum_margin: float | None,
    ) -> None:
        self.checkpoint = checkpoint.resolve(strict=True)
        self.gallery_root = gallery_root.resolve(strict=True)
        self.gallery_cache = gallery_cache.resolve(strict=True)
        self.locations_path = locations_path.resolve(strict=True)
        self.evidence_dir = evidence_dir.resolve()
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.device = device
        self.top_k = top_k
        self.minimum_similarity = minimum_similarity
        self.minimum_margin = minimum_margin
        self.locations = load_locations(self.locations_path)
        self.model_sha256 = sha256_file(self.checkpoint)
        expected = normalized_sha256(
            expected_checkpoint_sha256, "expected_checkpoint_sha256"
        )
        if self.model_sha256 != expected:
            raise ValueError(
                f"checkpoint hash mismatch: expected {expected}, got {self.model_sha256}"
            )
        self.location_manifest_sha256 = sha256_file(self.locations_path)
        self._encoder = None
        self._gallery_features: np.ndarray | None = None
        self._labels: list[str] = []
        self._paths: list[str] = []
        self._gallery_manifest_sha256: str | None = None
        self._lock = threading.Lock()
        self._loaded_at: str | None = None

    @property
    def loaded(self) -> bool:
        return self._encoder is not None and self._gallery_features is not None

    def status(self) -> dict[str, object]:
        return {
            "ok": True,
            "model_loaded": self.loaded,
            "model_sha256": self.model_sha256,
            "location_manifest_sha256": self.location_manifest_sha256,
            "location_count": len(self.locations),
            "device": self.device,
            "loaded_at": self._loaded_at,
            "policy": "visual_hypothesis_vio_confirmation_required",
        }

    def _ensure_loaded(self) -> None:
        if self.loaded:
            return
        paths = gallery_images(self.gallery_root)
        labels = [location_id(path) for path in paths]
        manifest_hash = gallery_manifest_sha256(paths, self.gallery_root)
        encoder = build_encoder(self.checkpoint, self.device)
        features, cached_labels, cached_paths = _load_cache(
            self.gallery_cache, self.model_sha256, manifest_hash
        )
        if len(cached_labels) != len(paths):
            raise ValueError("gallery cache and gallery directory have different sizes")
        self._encoder = encoder
        self._gallery_features = features
        self._labels = cached_labels or labels
        self._paths = cached_paths
        self._gallery_manifest_sha256 = manifest_hash
        self._loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def analyze(self, image_bytes: bytes, filename: str) -> dict[str, object]:
        from PIL import Image, UnidentifiedImageError  # type: ignore[import-not-found]

        suffix = safe_upload_suffix(filename)
        query_sha256 = hashlib.sha256(image_bytes).hexdigest()
        request_id = uuid.uuid4().hex
        started = time.perf_counter()
        try:
            image = Image.open(io.BytesIO(image_bytes))
            if image.width < 32 or image.height < 32:
                raise ValueError("image is too small")
            if image.width * image.height > 40_000_000:
                raise ValueError("decoded image exceeds the 40-megapixel safety limit")
            image.load()
        except (UnidentifiedImageError, OSError) as error:
            raise ValueError("uploaded bytes are not a valid image") from error

        query_width, query_height = image.width, image.height
        try:
            with self._lock:
                self._ensure_loaded()
                assert self._encoder is not None
                assert self._gallery_features is not None
                query_feature = encode_pil_images(self._encoder, [image], self.device)[0]
                raw_hypotheses = rank_top_k(
                    query_feature,
                    self._gallery_features,
                    self._labels,
                    self._paths,
                    self.top_k,
                )
        finally:
            image.close()
        accepted, reason, margin = acceptance_decision(
            raw_hypotheses,
            minimum_similarity=self.minimum_similarity,
            minimum_margin=self.minimum_margin,
        )
        top_k = enrich_hypotheses(raw_hypotheses, self.locations)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        result: dict[str, object] = {
            "schema": SCHEMA,
            "request_id": request_id,
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "accepted": accepted,
            "reason": reason,
            "display_status": (
                "APPEARANCE GATE PASSED — VIO CONFIRMATION REQUIRED"
                if accepted
                else "UNVERIFIED VISUAL HYPOTHESIS"
            ),
            "coordinate_meaning": "official WGS84 coordinate of the matched gallery location",
            "not_a_gps_measurement": True,
            "vio_confirmation_required": True,
            "appearance_margin": margin,
            "query_filename": Path(filename).name,
            "query_sha256": query_sha256,
            "query_width": query_width,
            "query_height": query_height,
            "checkpoint_sha256": self.model_sha256,
            "gallery_manifest_sha256": self._gallery_manifest_sha256,
            "location_manifest_sha256": self.location_manifest_sha256,
            "device": self.device,
            "elapsed_ms": round(elapsed_ms, 2),
            "top_k": top_k,
            "thresholds": {
                "minimum_similarity": self.minimum_similarity,
                "minimum_margin": self.minimum_margin,
            },
        }
        query_path = self.evidence_dir / f"{request_id}.query{suffix}"
        result_path = self.evidence_dir / f"{request_id}.result.json"
        with query_path.open("xb") as stream:
            stream.write(image_bytes)
        with result_path.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        return result

    def unload(self) -> dict[str, object]:
        with self._lock:
            self._encoder = None
            self._gallery_features = None
            self._labels = []
            self._paths = []
            self._loaded_at = None
            try:
                import torch  # type: ignore[import-not-found]

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
        return self.status()

    def gallery_image(self, location_id_value: str) -> Path:
        if not re.fullmatch(r"\d{4}", location_id_value):
            raise ValueError("invalid location ID")
        directory = (self.gallery_root / location_id_value).resolve()
        try:
            directory.relative_to(self.gallery_root)
        except ValueError as error:
            raise ValueError("gallery path escapes gallery root") from error
        candidates = sorted(
            path for path in directory.iterdir() if path.suffix.casefold() in SUPPORTED_UPLOADS
        )
        if not candidates:
            raise ValueError("gallery image not found")
        return candidates[0]


def handler_factory(
    runtime: RetrievalRuntime,
    *,
    max_upload_bytes: int,
    google_key: str,
    hazard_asset_root: Path,
    rescue_runtime: RescueVideoRuntime | None = None,
    max_video_upload_bytes: int = 512 * 1024 * 1024,
):
    page = ui_html(runtime, google_key)
    hazard_page = hazard_atlas_html(hazard_asset_root, google_key=google_key)
    rescue_page = rescue_video_html(rescue_runtime) if rescue_runtime else None

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format_string: str, *args: object) -> None:
            print(f"{self.client_address[0]} {format_string % args}")

        def send_bytes(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, code: int, value: Any) -> None:
            self.send_bytes(
                code,
                json.dumps(value, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def do_GET(self) -> None:
            route = self.path.split("?", 1)[0]
            if route == "/":
                return self.send_bytes(200, page, "text/html; charset=utf-8")
            if route == "/hazards":
                return self.send_bytes(200, hazard_page, "text/html; charset=utf-8")
            if route == "/rescue":
                if rescue_page is None:
                    return self.send_json(503, {"error": "rescue-video model is not configured"})
                return self.send_bytes(200, rescue_page, "text/html; charset=utf-8")
            if route == "/api/rescue/status":
                if rescue_runtime is None:
                    return self.send_json(503, {"error": "rescue-video model is not configured"})
                return self.send_json(200, rescue_runtime.status())
            rescue_job_match = re.fullmatch(
                r"/api/rescue/jobs/([0-9a-f-]{36})", route
            )
            if rescue_job_match:
                if rescue_runtime is None:
                    return self.send_json(503, {"error": "rescue-video model is not configured"})
                job = rescue_runtime.job(rescue_job_match.group(1))
                return (
                    self.send_json(200, job)
                    if job
                    else self.send_json(404, {"error": "rescue-video job not found"})
                )
            hazard_match = re.fullmatch(r"/assets/hazards/([a-z0-9.-]+)", route)
            if hazard_match:
                try:
                    path, content_type = hazard_asset(
                        hazard_asset_root, hazard_match.group(1)
                    )
                    return self.send_bytes(200, path.read_bytes(), content_type)
                except (OSError, ValueError) as error:
                    return self.send_json(404, {"error": str(error)})
            if route in {"/health", "/api/status"}:
                return self.send_json(200, runtime.status())
            match = re.fullmatch(r"/api/gallery/(\d{4})", route)
            if match:
                try:
                    path = runtime.gallery_image(match.group(1))
                    return self.send_bytes(200, path.read_bytes(), "image/jpeg")
                except (OSError, ValueError) as error:
                    return self.send_json(404, {"error": str(error)})
            return self.send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            route = self.path.split("?", 1)[0]
            if route == "/api/rescue/unload":
                if rescue_runtime is None:
                    return self.send_json(503, {"error": "rescue-video model is not configured"})
                try:
                    return self.send_json(200, rescue_runtime.unload())
                except ValueError as error:
                    return self.send_json(409, {"error": str(error)})
            if route == "/api/rescue/analyze":
                if rescue_runtime is None:
                    return self.send_json(503, {"error": "rescue-video model is not configured"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    return self.send_json(400, {"error": "invalid Content-Length"})
                if length <= 0 or length > max_video_upload_bytes:
                    return self.send_json(
                        413,
                        {
                            "error": (
                                "video upload must be 1 byte to "
                                f"{max_video_upload_bytes // 1048576} MiB"
                            )
                        },
                    )
                try:
                    payload, filename = extract_video_upload(
                        self.rfile.read(length), self.headers.get("Content-Type", "")
                    )
                    job_id = rescue_runtime.start(payload, filename)
                except ValueError as error:
                    return self.send_json(409, {"error": str(error)})
                return self.send_json(202, {"job_id": job_id})
            if route == "/api/unload":
                return self.send_json(200, runtime.unload())
            if route != "/api/analyze":
                return self.send_json(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.send_json(400, {"error": "invalid Content-Length"})
            if length <= 0 or length > max_upload_bytes:
                return self.send_json(
                    413,
                    {"error": f"upload must be 1 byte to {max_upload_bytes // 1048576} MiB"},
                )
            try:
                payload, filename = extract_upload(
                    self.rfile.read(length), self.headers.get("Content-Type", "")
                )
                if rescue_runtime is not None:
                    if rescue_runtime.active_job is not None:
                        return self.send_json(
                            409,
                            {
                                "error": (
                                    "rescue-video analysis is active; visual geolocation "
                                    "will be available when it finishes"
                                )
                            },
                        )
                    with rescue_runtime.gpu_lock:
                        rescue_runtime.unload()
                        result = runtime.analyze(payload, filename)
                else:
                    result = runtime.analyze(payload, filename)
            except ValueError as error:
                return self.send_json(400, {"error": str(error)})
            except Exception as error:  # Preserve a fail-closed API without hiding the server log.
                print(f"analysis failed: {type(error).__name__}: {error}")
                return self.send_json(500, {"error": f"analysis failed: {type(error).__name__}"})
            return self.send_json(200, result)

    return Handler


def ui_html(runtime: RetrievalRuntime, google_key: str) -> bytes:
    replacements = {
        "__MODEL_HASH__": html.escape(runtime.model_sha256[:16]),
        "__LOCATION_COUNT__": str(len(runtime.locations)),
        "__DEVICE__": html.escape(runtime.device),
        "__GOOGLE_KEY__": json.dumps(google_key),
        "__MAP_PROVIDER__": "Google Maps" if google_key else "OpenStreetMap",
    }
    value = UI_TEMPLATE
    for marker, replacement in replacements.items():
        value = value.replace(marker, replacement)
    return value.encode("utf-8")


UI_TEMPLATE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VeriSwarm Visual Geolocation</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root{--bg:#07100f;--panel:#0b1716;--panel2:#10201e;--line:#233a37;--text:#ecf7f3;--muted:#8eaaa4;--mint:#4de8ad;--cyan:#66d9ef;--amber:#ffbf69;--red:#ff6b6b}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% -10%,#14372f 0,transparent 35%),var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,sans-serif;min-height:100vh}.shell{max-width:1400px;margin:auto;padding:22px 28px 60px}header{display:flex;align-items:center;gap:10px;padding:10px 0 22px;border-bottom:1px solid var(--line)}.brand{font-size:20px;font-weight:700;letter-spacing:-.02em}.brand b{color:var(--mint)}.grow{flex:1}.nav-link{font:11px JetBrains Mono,monospace;text-decoration:none;color:var(--muted);padding:6px 9px;border:1px solid var(--line);border-radius:5px}.nav-link.active{color:var(--mint);border-color:#267a5c}.chip{font:11px JetBrains Mono,monospace;text-transform:uppercase;letter-spacing:.08em;padding:6px 9px;border:1px solid var(--line);background:#091413;color:var(--muted);border-radius:5px}.chip.live{color:var(--mint);border-color:#267a5c}.grid{display:grid;grid-template-columns:minmax(330px,.8fr) minmax(540px,1.4fr);gap:18px;margin-top:18px}.panel{border:1px solid var(--line);background:linear-gradient(160deg,rgba(16,32,30,.96),rgba(8,18,17,.96));border-radius:12px;overflow:hidden}.panel-title{display:flex;align-items:center;gap:10px;padding:13px 16px;border-bottom:1px solid var(--line);font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}.dot{width:7px;height:7px;border-radius:50%;background:var(--mint);box-shadow:0 0 14px var(--mint)}#drop{min-height:330px;margin:16px;border:1px dashed #365b55;border-radius:9px;display:flex;align-items:center;justify-content:center;text-align:center;padding:28px;cursor:pointer;transition:.18s;background:rgba(5,13,12,.5);position:relative;overflow:hidden}#drop.hot{border-color:var(--mint);background:#0d251f}#drop.has-image{padding:0;border-style:solid}#preview{display:none;width:100%;height:100%;min-height:330px;object-fit:contain;background:#030706}.upload-copy h2{font-size:20px;margin:0 0 8px}.upload-copy p{color:var(--muted);margin:0}.button-row{display:flex;gap:10px;padding:0 16px 16px}.btn{border:1px solid var(--line);background:#10231f;color:var(--text);border-radius:6px;padding:10px 14px;font-weight:600;cursor:pointer}.btn.primary{background:var(--mint);border-color:var(--mint);color:#04110d;flex:1}.btn:disabled{opacity:.45;cursor:not-allowed}.notice{margin:0 16px 16px;padding:11px 12px;border-left:3px solid var(--amber);background:#21190d;color:#eed3a9;font-size:12px}.map-wrap{height:430px;background:#08100f;position:relative}#map{height:100%;width:100%}.empty-map{position:absolute;inset:0;display:grid;place-items:center;color:var(--muted);font:12px JetBrains Mono,monospace;z-index:2}.statusbar{display:grid;grid-template-columns:1.2fr .8fr .8fr .8fr;border-top:1px solid var(--line)}.metric{padding:13px 15px;border-right:1px solid var(--line);min-width:0}.metric:last-child{border-right:0}.label{font:10px JetBrains Mono,monospace;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}.value{font:600 15px JetBrains Mono,monospace;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.result{margin-top:18px}.hero{display:grid;grid-template-columns:150px 1fr;gap:16px;padding:16px}.hero img{width:150px;height:120px;object-fit:cover;border-radius:7px;background:#06100e}.rank{font:11px JetBrains Mono,monospace;color:var(--mint)}.place{font-size:22px;font-weight:700;margin:5px 0}.coords{font:13px JetBrains Mono,monospace;color:var(--cyan)}.state{display:inline-block;margin-top:10px;padding:5px 8px;border-radius:4px;background:#2b210f;color:var(--amber);font:10px JetBrains Mono,monospace}.candidates{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);border-top:1px solid var(--line)}.candidate{background:var(--panel);padding:12px;min-width:0}.candidate img{width:100%;height:88px;object-fit:cover;border-radius:5px;margin-bottom:8px}.candidate .name{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.candidate .score{font:11px JetBrains Mono,monospace;color:var(--muted)}.policy{margin-top:18px;border:1px solid #624a25;background:#1b150c;padding:14px 16px;border-radius:8px;color:#e8c995}.policy strong{color:var(--amber)}#error{display:none;margin:16px;border-left:3px solid var(--red);background:#261011;padding:12px;color:#ffb4b4}.spinner{display:inline-block;width:14px;height:14px;border:2px solid #174c3b;border-top-color:var(--mint);border-radius:50%;animation:spin .7s linear infinite;vertical-align:-2px;margin-right:7px}@keyframes spin{to{transform:rotate(360deg)}}a{color:var(--cyan)}.leaflet-container{background:#0a1413}.leaflet-popup-content-wrapper,.leaflet-popup-tip{background:#10201e;color:var(--text)}@media(max-width:900px){.grid{grid-template-columns:1fr}.candidates{grid-template-columns:repeat(2,1fr)}.shell{padding:14px}.statusbar{grid-template-columns:1fr 1fr}.chip{display:none}}
</style></head><body><div class="shell"><header><div class="brand"><b>VERI</b>SWARM / VISUAL GEOLOCATION</div><div class="grow"></div><a class="nav-link" href="/rescue">Survivor video</a><a class="nav-link active" href="/">Geolocation</a><a class="nav-link" href="/hazards">Hazard atlas</a><span class="chip" id="runtime">RUNTIME COLD</span><span class="chip">MAP __MAP_PROVIDER__</span><span class="chip live">JETSON __DEVICE__</span></header>
<div class="grid"><section class="panel"><div class="panel-title"><span class="dot"></span> Drone-view query</div><div id="drop"><div class="upload-copy" id="copy"><h2>Drop an aerial image</h2><p>JPEG, PNG or WebP · processed locally on Jetson</p></div><img id="preview" alt="uploaded aerial view"><input id="file" type="file" accept="image/jpeg,image/png,image/webp" hidden></div><div id="error"></div><div class="button-row"><button class="btn primary" id="locate" disabled>Locate visual match</button><button class="btn" id="clear">Clear</button><button class="btn" id="unload" title="Release model and GPU memory">Unload model</button></div><div class="notice">This is cross-view image retrieval. It reports the official coordinate of the closest satellite-gallery match; it is not a GNSS reading.</div></section>
<section class="panel"><div class="panel-title"><span class="dot"></span> Candidate map · 1,652 official locations</div><div class="map-wrap"><div id="emptyMap" class="empty-map">AWAITING QUERY IMAGE</div><div id="map"></div></div><div class="statusbar"><div class="metric"><div class="label">System status</div><div class="value" id="m-status">Ready</div></div><div class="metric"><div class="label">Top similarity</div><div class="value" id="m-score">—</div></div><div class="metric"><div class="label">Top-2 margin</div><div class="value" id="m-margin">—</div></div><div class="metric"><div class="label">Inference</div><div class="value" id="m-time">—</div></div></div></section></div>
<section class="panel result" id="result" hidden><div class="panel-title"><span class="dot"></span> Ranked location hypotheses</div><div class="hero"><img id="heroImg"><div><div class="rank">RANK 01 · MATCHED GALLERY COORDINATE</div><div class="place" id="heroName"></div><div class="coords" id="heroCoords"></div><div><a id="osmLink" target="_blank" rel="noopener">Open in OpenStreetMap</a> · <a id="googleLink" target="_blank" rel="noopener">Open in Google Maps</a></div><div class="state" id="heroState"></div></div></div><div class="candidates" id="candidates"></div></section>
<div class="policy"><strong>Operational policy:</strong> a single image may generate a location candidate, but flight control must not treat it as truth. Accept a position correction only after calibrated similarity/margin thresholds and temporal VIO/IMU consistency checks pass.</div>
</div><script>
const googleKey=__GOOGLE_KEY__;let chosen=null,map=null,gmap=null,markers=[];
const $=id=>document.getElementById(id),drop=$('drop'),file=$('file'),preview=$('preview'),locate=$('locate'),error=$('error');
drop.onclick=()=>file.click();drop.ondragover=e=>{e.preventDefault();drop.classList.add('hot')};drop.ondragleave=()=>drop.classList.remove('hot');drop.ondrop=e=>{e.preventDefault();drop.classList.remove('hot');pick(e.dataTransfer.files[0])};file.onchange=()=>pick(file.files[0]);
function pick(f){if(!f)return;if(!['image/jpeg','image/png','image/webp'].includes(f.type)){return fail('Choose a JPEG, PNG or WebP image.')}chosen=f;preview.src=URL.createObjectURL(f);preview.style.display='block';$('copy').style.display='none';drop.classList.add('has-image');locate.disabled=false;error.style.display='none';}
function fail(msg){error.textContent=msg;error.style.display='block';$('m-status').textContent='Error';locate.disabled=false;locate.textContent='Locate visual match';}
$('clear').onclick=()=>{chosen=null;file.value='';preview.removeAttribute('src');preview.style.display='none';$('copy').style.display='block';drop.classList.remove('has-image');locate.disabled=true;$('result').hidden=true;error.style.display='none'};
$('unload').onclick=async()=>{const r=await fetch('/api/unload',{method:'POST'});if(r.ok){$('runtime').textContent='RUNTIME COLD';$('runtime').classList.remove('live');$('m-status').textContent='GPU memory released'}};
locate.onclick=async()=>{if(!chosen)return;locate.disabled=true;locate.innerHTML='<span class="spinner"></span>Loading model + matching';$('m-status').textContent='Analyzing locally';error.style.display='none';const fd=new FormData();fd.append('image',chosen);try{const r=await fetch('/api/analyze',{method:'POST',body:fd});const j=await r.json();if(!r.ok)throw new Error(j.error||`server ${r.status}`);show(j)}catch(e){fail(e.message)}finally{locate.disabled=false;locate.textContent='Locate visual match'}};
function show(j){const t=j.top_k,first=t[0];$('runtime').textContent='RUNTIME HOT';$('runtime').classList.add('live');$('m-status').textContent=j.display_status;$('m-score').textContent=first.cosine_similarity.toFixed(4);$('m-margin').textContent=j.appearance_margin==null?(t.length>1?(first.cosine_similarity-t[1].cosine_similarity).toFixed(4):'—'):j.appearance_margin.toFixed(4);$('m-time').textContent=j.elapsed_ms.toFixed(0)+' ms';$('heroImg').src=first.gallery_image_url;$('heroName').textContent=first.name;$('heroCoords').textContent=`${first.latitude.toFixed(7)}, ${first.longitude.toFixed(7)}`;$('osmLink').href=first.openstreetmap_url;$('googleLink').href=first.google_maps_url;$('heroState').textContent=j.display_status;$('candidates').innerHTML=t.slice(1).map(x=>`<div class="candidate"><img src="${x.gallery_image_url}"><div class="rank">RANK ${String(x.rank).padStart(2,'0')}</div><div class="name" title="${escapeHtml(x.name)}">${escapeHtml(x.name)}</div><div class="score">${x.cosine_similarity.toFixed(4)} · ${x.latitude.toFixed(4)}, ${x.longitude.toFixed(4)}</div></div>`).join('');$('result').hidden=false;$('emptyMap').style.display='none';renderMap(t)}
function escapeHtml(s){return s.replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}
function renderMap(points){if(googleKey){return renderGoogle(points).catch(()=>renderLeaflet(points))}renderLeaflet(points)}
function renderLeaflet(points){if(gmap){$('map').innerHTML='';gmap=null}if(!map)map=L.map('map',{zoomControl:true});markers.forEach(m=>m.remove());markers=[];const bounds=[];points.forEach((p,i)=>{const marker=L.circleMarker([p.latitude,p.longitude],{radius:i===0?11:7,color:i===0?'#ffbf69':'#66d9ef',fillColor:i===0?'#ffbf69':'#66d9ef',fillOpacity:.9,weight:2}).addTo(map).bindPopup(`<b>#${p.rank} ${escapeHtml(p.name)}</b><br>${p.cosine_similarity.toFixed(4)}<br>${p.latitude.toFixed(6)}, ${p.longitude.toFixed(6)}`);markers.push(marker);bounds.push([p.latitude,p.longitude])});if(!map._tileLayer){map._tileLayer=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; OpenStreetMap contributors'}).addTo(map)}map.fitBounds(bounds,{padding:[45,45],maxZoom:16});setTimeout(()=>map.invalidateSize(),50)}
function loadGoogle(){return new Promise((resolve,reject)=>{if(window.google?.maps)return resolve();window.__mapsReady=resolve;const s=document.createElement('script');s.src=`https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(googleKey)}&callback=__mapsReady`;s.async=true;s.onerror=reject;document.head.appendChild(s)})}
async function renderGoogle(points){await loadGoogle();if(map){map.remove();map=null;$('map').innerHTML=''}const center={lat:points[0].latitude,lng:points[0].longitude};gmap=new google.maps.Map($('map'),{center,zoom:15,mapTypeId:'hybrid',disableDefaultUI:false});const bounds=new google.maps.LatLngBounds();points.forEach((p,i)=>{const pos={lat:p.latitude,lng:p.longitude};new google.maps.Marker({map:gmap,position:pos,label:String(p.rank),title:p.name});bounds.extend(pos)});gmap.fitBounds(bounds)}
fetch('/api/status').then(r=>r.json()).then(s=>{if(s.model_loaded){$('runtime').textContent='RUNTIME HOT';$('runtime').classList.add('live')}});
</script></body></html>'''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--expected-checkpoint-sha256", required=True)
    parser.add_argument("--gallery-dir", required=True, type=Path)
    parser.add_argument("--gallery-cache", required=True, type=Path)
    parser.add_argument("--locations", required=True, type=Path)
    parser.add_argument("--evidence-dir", default="~/VeriSwarm_Jetson_Evidence/geolocation-console", type=Path)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--minimum-similarity", type=float)
    parser.add_argument("--minimum-margin", type=float)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--max-upload-mib", type=int, default=DEFAULT_MAX_UPLOAD_MIB)
    parser.add_argument(
        "--rescue-model",
        type=Path,
        help="YOLO .pt or .onnx weights; enables /rescue in the same server",
    )
    parser.add_argument("--expected-rescue-model-sha256")
    parser.add_argument("--rescue-confidence", type=float, default=0.35)
    parser.add_argument("--rescue-person-class", type=int, default=0)
    parser.add_argument("--rescue-image-size", type=int, default=640)
    parser.add_argument("--rescue-stride", type=int, default=10)
    parser.add_argument("--rescue-max-frames", type=int, default=400)
    parser.add_argument("--max-video-upload-mib", type=int, default=512)
    parser.add_argument(
        "--rescue-work-dir", default="/tmp/veriswarm-rescue", type=Path
    )
    parser.add_argument(
        "--rescue-evidence-dir",
        default="~/VeriSwarm_Jetson_Evidence/rescue-video",
        type=Path,
    )
    parser.add_argument(
        "--optee-ca",
        default=(
            "/home/akaberlinflix/VeriSwarm_SIH_codebase/"
            "optee/host/veriswarm_optee_ca"
        ),
        type=Path,
    )
    parser.add_argument(
        "--hazard-assets-dir",
        type=Path,
        help="defaults to assets/hazards beside the code directory",
    )
    parser.add_argument(
        "--google-maps-api-key",
        default=os.environ.get("GOOGLE_MAPS_API_KEY", ""),
        help="optional; prefer GOOGLE_MAPS_API_KEY so the key is not stored in shell history",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.top_k < 2 or args.top_k > 20:
        raise SystemExit("--top-k must be between 2 and 20")
    if args.max_upload_mib <= 0 or args.max_upload_mib > 100:
        raise SystemExit("--max-upload-mib must be between 1 and 100")
    if args.max_video_upload_mib <= 0 or args.max_video_upload_mib > 2048:
        raise SystemExit("--max-video-upload-mib must be between 1 and 2048")
    if args.rescue_stride <= 0 or args.rescue_max_frames <= 0:
        raise SystemExit("rescue stride and max frames must be positive")
    runtime = RetrievalRuntime(
        checkpoint=args.checkpoint.expanduser(),
        expected_checkpoint_sha256=args.expected_checkpoint_sha256,
        gallery_root=args.gallery_dir.expanduser(),
        gallery_cache=args.gallery_cache.expanduser(),
        locations_path=args.locations.expanduser(),
        evidence_dir=args.evidence_dir.expanduser(),
        device=args.device,
        top_k=args.top_k,
        minimum_similarity=args.minimum_similarity,
        minimum_margin=args.minimum_margin,
    )
    gpu_lock = threading.Lock()
    rescue_runtime = None
    if args.rescue_model:
        rescue_runtime = RescueVideoRuntime(
            weights=args.rescue_model.expanduser(),
            expected_weights_sha256=args.expected_rescue_model_sha256,
            confidence=args.rescue_confidence,
            person_class=args.rescue_person_class,
            image_size=args.rescue_image_size,
            stride=args.rescue_stride,
            max_frames=args.rescue_max_frames,
            work_dir=args.rescue_work_dir.expanduser(),
            evidence_dir=args.rescue_evidence_dir.expanduser(),
            optee_ca=args.optee_ca.expanduser(),
            gpu_lock=gpu_lock,
            before_run=runtime.unload,
        )
    handler = handler_factory(
        runtime,
        max_upload_bytes=args.max_upload_mib * 1024 * 1024,
        google_key=args.google_maps_api_key.strip(),
        hazard_asset_root=(
            args.hazard_assets_dir.expanduser()
            if args.hazard_assets_dir
            else Path(__file__).resolve().parents[1] / "assets" / "hazards"
        ),
        rescue_runtime=rescue_runtime,
        max_video_upload_bytes=args.max_video_upload_mib * 1024 * 1024,
    )
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    print(
        "\n".join(
            [
                "VeriSwarm University-1652 console ready",
                f"  URL:         http://192.168.50.10:{args.port}",
                f"  Rescue:      http://192.168.50.10:{args.port}/rescue",
                f"  Atlas:       http://192.168.50.10:{args.port}/hazards",
                f"  Model:       {runtime.model_sha256[:16]}… (lazy; not loaded yet)",
                f"  Locations:   {len(runtime.locations)} official WGS84 records",
                f"  Map:         {'Google Maps' if args.google_maps_api_key else 'OpenStreetMap'}",
                (
                    f"  Detector:    {rescue_runtime.weights_sha256[:16]}… (lazy)"
                    if rescue_runtime
                    else "  Detector:    disabled (supply --rescue-model)"
                ),
                "  Safety:      visual hypothesis; VIO/IMU confirmation required",
                "  Stop:        Ctrl+C",
            ]
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...", flush=True)
    finally:
        server.server_close()
        if rescue_runtime and rescue_runtime.active_job is None:
            rescue_runtime.unload()
        runtime.unload()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
