"""Build create-once Phase 3 clearance, settings, and launch artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sim.cosys.factorycity.config import load_config
from sim.cosys.factorycity.launch import (
    generate_launch_plan,
    render_cosys_settings,
    render_launch_manifest,
    write_create_once,
)
from sim.cosys.factorycity.phase3 import (
    build_cosys_base_settings,
    load_clearance_result,
    load_runtime_profile,
    render_clearance_request,
)


def _request_bytes(args):
    config, config_hash = load_config(args.config)
    profile, profile_hash = load_runtime_profile(args.runtime_profile)
    world_hash = __import__("hashlib").sha256(args.world.read_bytes()).hexdigest()
    probe_adapter = Path(__file__).with_name("unreal_clearance_probe.py")
    probe_adapter_hash = __import__("hashlib").sha256(
        probe_adapter.read_bytes()
    ).hexdigest()
    request = render_clearance_request(
        config,
        config_hash,
        profile,
        profile_hash,
        world_hash,
        probe_adapter_hash,
    )
    return config, config_hash, profile, profile_hash, world_hash, request


def _prepare(args):
    _, _, _, _, _, request = _request_bytes(args)
    digest = write_create_once(args.output, request)
    print(json.dumps({"output": str(args.output), "sha256": digest}, sort_keys=True))


def _finalize(args):
    occupied = [
        path for path in (args.settings_output, args.manifest_output) if path.exists()
    ]
    if occupied:
        raise ValueError(
            "refusing partial artifact creation because output already exists: "
            + ", ".join(str(path) for path in occupied)
        )
    config, config_hash, profile, _, _, expected_request = _request_bytes(args)
    observed_request = args.clearance_request.read_bytes()
    if observed_request != expected_request:
        raise ValueError("clearance request is not reproducible from current inputs")
    provider, _, clearance_hash = load_clearance_result(
        args.clearance_result, config, config_hash, observed_request
    )
    plan = generate_launch_plan(config, provider)
    base_settings = build_cosys_base_settings(config, profile)
    settings = render_cosys_settings(config, plan, base_settings)
    manifest = render_launch_manifest(config, config_hash, plan, settings)
    settings_hash = write_create_once(args.settings_output, settings)
    manifest_hash = write_create_once(args.manifest_output, manifest)
    print(
        json.dumps(
            {
                "clearance_result_sha256": clearance_hash,
                "manifest_output": str(args.manifest_output),
                "manifest_sha256": manifest_hash,
                "minimum_pairwise_distance_m": plan.minimum_pairwise_distance_m,
                "selected_count": len(plan.positions),
                "settings_output": str(args.settings_output),
                "settings_sha256": settings_hash,
            },
            sort_keys=True,
        )
    )


def _common(parser):
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--runtime-profile", type=Path, required=True)
    parser.add_argument("--world", type=Path, required=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare-clearance")
    _common(prepare)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.set_defaults(handler=_prepare)
    finalize = subparsers.add_parser("finalize")
    _common(finalize)
    finalize.add_argument("--clearance-request", type=Path, required=True)
    finalize.add_argument("--clearance-result", type=Path, required=True)
    finalize.add_argument("--settings-output", type=Path, required=True)
    finalize.add_argument("--manifest-output", type=Path, required=True)
    finalize.set_defaults(handler=_finalize)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
