import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from sim.cosys.factorycity.config import load_config, validate_config
from sim.cosys.factorycity.launch import (
    LAUNCH_MANIFEST_SCHEMA_ID,
    ClearanceContractError,
    ClearanceProbeResult,
    InsufficientLaunchCapacity,
    LaunchPlacementError,
    calculate_launch_bounds,
    generate_lattice_candidates,
    generate_launch_plan,
    render_cosys_settings,
    render_launch_manifest,
    write_create_once,
)


TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1]
    / "sim"
    / "cosys"
    / "factorycity"
    / "factorycity_five_drone.template.json"
)
EXAMPLE_SETTINGS_PATH = (
    TEMPLATE_PATH.parent
    / "examples"
    / "phase2_synthetic_settings.example.json"
)


class SyntheticClearanceProvider:
    def __init__(
        self,
        provider_id,
        *,
        ground_blocked=(),
        corridor_blocked=(),
        scene_validated=False,
        ground_z=-1.0,
    ):
        self.provider_id = provider_id
        self.scene_validated = scene_validated
        self.ground_blocked = set(ground_blocked)
        self.corridor_blocked = set(corridor_blocked)
        self.ground_z = ground_z
        self.requests = []

    def probe(self, request):
        self.requests.append(request)
        candidate_id = request.candidate.candidate_id
        ground_clear = candidate_id not in self.ground_blocked
        corridor_clear = candidate_id not in self.corridor_blocked
        reasons = []
        if not ground_clear:
            reasons.append("ground_blocked")
        if not corridor_clear:
            reasons.append("vertical_corridor_blocked")
        z_value = (
            self.ground_z(request)
            if callable(self.ground_z)
            else self.ground_z
        )
        return ClearanceProbeResult(
            ground_clear=ground_clear,
            vertical_corridor_clear=corridor_clear,
            ground_z_ned_m=z_value if ground_clear else None,
            evidence_id=f"synthetic/{candidate_id}",
            reason=";".join(reasons),
        )


@pytest.fixture
def raw_document():
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def loaded():
    return load_config(TEMPLATE_PATH)


@pytest.fixture
def config(loaded):
    return loaded[0]


@pytest.fixture
def provider(config):
    return SyntheticClearanceProvider(config.launch_area.ground_clearance_probe)


@pytest.fixture
def plan(config, provider):
    return generate_launch_plan(config, provider)


def _set_roster(document, names):
    document["fleet"]["expected_count"] = len(names)
    document["fleet"]["vehicles"] = [
        {"name": name, "type": f"type-{index}", "role": "member"}
        for index, name in enumerate(names)
    ]
    document["lifecycle"]["takeoff_order"] = list(names)
    document["lifecycle"]["landing_order"] = list(reversed(names))
    document["lifecycle"]["takeoff_altitude_offsets_m"] = [
        {"vehicle_name": name, "offset_m": 0.0} for name in names
    ]
    document["sensors"]["vehicle_profiles"] = [
        {"vehicle_name": name, "profile": "standard-navigation-v1"}
        for name in names
    ]


def _base_settings(config):
    return {
        "SettingsVersion": 1.2,
        "SimMode": "Multirotor",
        "custom_global": {"preserved": True},
        "Vehicles": {
            vehicle.name: {
                "VehicleType": vehicle.vehicle_type,
                "Yaw": index * 15,
                "Sensors": {"configured_elsewhere": {"Enabled": True}},
            }
            for index, vehicle in enumerate(config.fleet.vehicles)
        },
    }


def test_bounds_come_from_center_side_and_edge_clearance(config):
    bounds = calculate_launch_bounds(config.launch_area)

    assert bounds.minimum_x_m == -4.5
    assert bounds.maximum_x_m == 4.5
    assert bounds.minimum_y_m == -4.5
    assert bounds.maximum_y_m == 4.5
    assert bounds.side_length_m == 9.0


def test_lattice_includes_every_usable_boundary(config):
    bounds, intervals, candidates = generate_lattice_candidates(
        config.launch_area, config.fleet.expected_count
    )

    assert intervals == 5
    assert len(candidates) == 36
    assert min(item.x_m for item in candidates) == bounds.minimum_x_m
    assert max(item.x_m for item in candidates) == bounds.maximum_x_m
    assert min(item.y_m for item in candidates) == bounds.minimum_y_m
    assert max(item.y_m for item in candidates) == bounds.maximum_y_m
    assert len({item.candidate_id for item in candidates}) == len(candidates)


def test_single_vehicle_uses_configured_center(raw_document):
    _set_roster(raw_document, ("solo",))
    raw_document["launch_area"]["center_ned_m"] = {"x": 12.5, "y": -3.25}
    raw_document["limits"]["geofence"]["max"]["x"] = 30.0
    config = validate_config(raw_document)

    _, intervals, candidates = generate_lattice_candidates(config.launch_area, 1)

    assert intervals == 0
    assert [(item.x_m, item.y_m) for item in candidates] == [(12.5, -3.25)]


def test_lattice_work_is_bounded_by_configured_fleet_size(raw_document):
    raw_document["launch_area"]["minimum_separation_m"] = 1e-300
    raw_document["limits"]["minimum_separation_m"] = 1e-300
    config = validate_config(raw_document)

    _, intervals, candidates = generate_lattice_candidates(
        config.launch_area, config.fleet.expected_count
    )

    assert intervals <= config.fleet.expected_count
    assert len(candidates) <= (config.fleet.expected_count + 1) ** 2


@pytest.mark.parametrize("count", [True, 0, -1, 1.5])
def test_candidate_count_must_be_a_positive_integer(config, count):
    with pytest.raises(LaunchPlacementError, match="integer|greater than zero"):
        generate_lattice_candidates(config.launch_area, count)


def test_plan_is_generic_over_roster_size_and_square(raw_document):
    names = ("red", "green", "blue")
    _set_roster(raw_document, names)
    raw_document["launch_area"]["side_length_m"] = 18.0
    raw_document["launch_area"]["center_ned_m"] = {"x": 4.0, "y": -5.0}
    raw_document["limits"]["geofence"]["min"] = {
        "x": -20.0,
        "y": -20.0,
        "z": -10.0,
    }
    raw_document["limits"]["geofence"]["max"] = {
        "x": 20.0,
        "y": 20.0,
        "z": 1.0,
    }
    config = validate_config(raw_document)
    provider = SyntheticClearanceProvider(
        config.launch_area.ground_clearance_probe,
        ground_z=lambda request: -1.0 + (request.candidate.x_m / 100.0),
    )

    plan = generate_launch_plan(config, provider)

    assert tuple(item.vehicle_name for item in plan.positions) == names
    assert len(plan.positions) == len(names)
    assert all(plan.bounds.contains(item.x_m, item.y_m) for item in plan.positions)
    assert all(
        item.distance_m >= config.launch_area.minimum_separation_m
        for item in plan.pairwise_distances
    )


def test_single_vehicle_plan_and_manifest_have_no_fake_pair_distance(raw_document):
    _set_roster(raw_document, ("solo",))
    config = validate_config(raw_document)
    provider = SyntheticClearanceProvider(config.launch_area.ground_clearance_probe)
    plan = generate_launch_plan(config, provider)
    settings = render_cosys_settings(config, plan, _base_settings(config))
    manifest = json.loads(render_launch_manifest(config, "a" * 64, plan, settings))

    assert len(plan.positions) == 1
    assert plan.pairwise_distances == ()
    assert plan.minimum_pairwise_distance_m is None
    assert manifest["pairwise_distances"] == []
    assert manifest["metrics"]["minimum_pairwise_distance_m"] is None
    assert all(manifest["checks"].values())


def test_repeated_inputs_and_seed_produce_identical_plan(config):
    first = generate_launch_plan(
        config, SyntheticClearanceProvider(config.launch_area.ground_clearance_probe)
    )
    second = generate_launch_plan(
        config, SyntheticClearanceProvider(config.launch_area.ground_clearance_probe)
    )

    assert first == second


def test_seed_is_used_for_deterministic_tie_breaks(raw_document):
    selected_sequences = set()
    for seed in range(8):
        document = copy.deepcopy(raw_document)
        document["launch_area"]["deterministic_seed"] = seed
        config = validate_config(document)
        provider = SyntheticClearanceProvider(
            config.launch_area.ground_clearance_probe
        )
        plan = generate_launch_plan(config, provider)
        selected_sequences.add(tuple(item.candidate_id for item in plan.positions))

    assert len(selected_sequences) > 1


def test_every_candidate_receives_configured_clearance_request(config, provider):
    plan = generate_launch_plan(config, provider)

    assert len(provider.requests) == plan.candidate_count
    assert {
        (
            request.altitude_minimum_z_ned_m,
            request.altitude_maximum_z_ned_m,
            request.required_clearance_m,
        )
        for request in provider.requests
    } == {
        (
            config.limits.altitude_band.minimum_z,
            config.limits.altitude_band.maximum_z,
            config.limits.collision_clearance_m,
        )
    }


def test_ground_blocked_candidates_are_never_selected(config):
    blocked = {"r0c0", "r0c5", "r5c0"}
    provider = SyntheticClearanceProvider(
        config.launch_area.ground_clearance_probe, ground_blocked=blocked
    )

    plan = generate_launch_plan(config, provider)

    assert blocked.isdisjoint(item.candidate_id for item in plan.positions)
    assert plan.accepted_candidate_count == plan.candidate_count - len(blocked)


def test_vertical_corridor_blocked_candidates_are_never_selected(config):
    blocked = {"r0c0", "r0c5", "r5c0"}
    provider = SyntheticClearanceProvider(
        config.launch_area.ground_clearance_probe, corridor_blocked=blocked
    )

    plan = generate_launch_plan(config, provider)

    assert blocked.isdisjoint(item.candidate_id for item in plan.positions)
    assert plan.accepted_candidate_count == plan.candidate_count - len(blocked)


def test_too_few_clear_candidates_fail_closed(config):
    _, _, candidates = generate_lattice_candidates(
        config.launch_area, config.fleet.expected_count
    )
    accepted = {item.candidate_id for item in candidates[:4]}
    blocked = {
        item.candidate_id for item in candidates if item.candidate_id not in accepted
    }
    provider = SyntheticClearanceProvider(
        config.launch_area.ground_clearance_probe, ground_blocked=blocked
    )

    with pytest.raises(InsufficientLaunchCapacity, match="fewer candidates"):
        generate_launch_plan(config, provider)


def test_insufficient_pairwise_capacity_fails_closed(raw_document):
    raw_document["launch_area"]["minimum_separation_m"] = 7.0
    raw_document["limits"]["minimum_separation_m"] = 7.0
    config = validate_config(raw_document)
    provider = SyntheticClearanceProvider(config.launch_area.ground_clearance_probe)

    with pytest.raises(InsufficientLaunchCapacity, match="minimum separation"):
        generate_launch_plan(config, provider)


@pytest.mark.parametrize(
    ("ground_z", "match"),
    [
        (2.0, "vertical geofence"),
        (-3.0, "altitude band is not entirely above"),
    ],
)
def test_selected_ground_must_be_compatible_with_flight_limits(
    config, ground_z, match
):
    provider = SyntheticClearanceProvider(
        config.launch_area.ground_clearance_probe, ground_z=ground_z
    )
    with pytest.raises(LaunchPlacementError, match=match):
        generate_launch_plan(config, provider)


def test_provider_identity_must_match_configuration(config):
    provider = SyntheticClearanceProvider("wrong-provider")
    with pytest.raises(ClearanceContractError, match="does not match"):
        generate_launch_plan(config, provider)


def test_provider_scene_validated_flag_must_be_boolean(config):
    provider = SyntheticClearanceProvider(config.launch_area.ground_clearance_probe)
    provider.scene_validated = "false"
    with pytest.raises(ClearanceContractError, match="scene_validated"):
        generate_launch_plan(config, provider)


def test_provider_exception_fails_closed(config):
    provider = SyntheticClearanceProvider(config.launch_area.ground_clearance_probe)

    def fail(_request):
        raise RuntimeError("probe unavailable")

    provider.probe = fail
    with pytest.raises(ClearanceContractError, match="probe unavailable"):
        generate_launch_plan(config, provider)


@pytest.mark.parametrize(
    ("result", "match"),
    [
        (None, "wrong result type"),
        (
            ClearanceProbeResult(True, True, None, "evidence", ""),
            "ground_z_ned_m",
        ),
        (
            ClearanceProbeResult(True, True, float("nan"), "evidence", ""),
            "ground_z_ned_m",
        ),
        (
            ClearanceProbeResult(True, True, 0.0, "", ""),
            "evidence_id",
        ),
        (
            ClearanceProbeResult(False, True, None, "evidence", ""),
            "reason",
        ),
        (
            ClearanceProbeResult(1, True, 0.0, "evidence", ""),
            "ground_clear",
        ),
    ],
)
def test_malformed_provider_results_fail_closed(config, result, match):
    provider = SyntheticClearanceProvider(config.launch_area.ground_clearance_probe)
    provider.probe = lambda _request: result
    with pytest.raises(ClearanceContractError, match=match):
        generate_launch_plan(config, provider)


def test_plan_contains_complete_bounds_distances_and_clearance(config, plan):
    assert len(plan.positions) == config.fleet.expected_count
    assert len(plan.pairwise_distances) == 10
    assert plan.minimum_pairwise_distance_m == min(
        item.distance_m for item in plan.pairwise_distances
    )
    assert plan.minimum_pairwise_distance_m >= config.launch_area.minimum_separation_m
    assert all(position.ground_z_ned_m == -1.0 for position in plan.positions)
    assert all(
        position.z_ned_m
        == position.ground_z_ned_m - config.launch_area.initial_spawn_clearance_m
        for position in plan.positions
    )
    selected = {item.candidate_id for item in plan.positions}
    assert all(
        evaluation.accepted
        for evaluation in plan.evaluations
        if evaluation.candidate.candidate_id in selected
    )


def test_settings_renderer_patches_only_roster_type_and_position(config, plan):
    base = _base_settings(config)
    original = copy.deepcopy(base)

    rendered = json.loads(render_cosys_settings(config, plan, base))

    assert base == original
    assert rendered["custom_global"] == original["custom_global"]
    for position in plan.positions:
        output = rendered["Vehicles"][position.vehicle_name]
        source = original["Vehicles"][position.vehicle_name]
        assert output["X"] == position.x_m
        assert output["Y"] == position.y_m
        assert output["Z"] == position.z_ned_m
        assert output["Yaw"] == source["Yaw"]
        assert output["Sensors"] == source["Sensors"]


def test_settings_renderer_is_byte_deterministic(config, plan):
    base = _base_settings(config)
    assert render_cosys_settings(config, plan, base) == render_cosys_settings(
        config, plan, copy.deepcopy(base)
    )


def test_committed_synthetic_settings_example_is_reproducible(config, plan):
    expected = json.loads(EXAMPLE_SETTINGS_PATH.read_text(encoding="utf-8"))
    base = copy.deepcopy(expected)
    for vehicle in base["Vehicles"].values():
        vehicle.pop("X")
        vehicle.pop("Y")
        vehicle.pop("Z")

    rendered = render_cosys_settings(config, plan, base)

    assert json.loads(rendered) == expected


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda base, _config: base.pop("Vehicles"), "Vehicles object"),
        (
            lambda base, _config: base["Vehicles"].pop(next(iter(base["Vehicles"]))),
            "exact configured roster",
        ),
        (
            lambda base, _config: base["Vehicles"].update({"outsider": {}}),
            "exact configured roster",
        ),
        (
            lambda base, _config: base["Vehicles"].update(
                {next(iter(base["Vehicles"])): []}
            ),
            "must be an object",
        ),
        (
            lambda base, _config: base["Vehicles"][
                next(iter(base["Vehicles"]))
            ].update({"VehicleType": "wrong"}),
            "VehicleType mismatch",
        ),
    ],
)
def test_settings_renderer_rejects_inconsistent_base(config, plan, mutation, match):
    base = _base_settings(config)
    mutation(base, config)
    with pytest.raises(LaunchPlacementError, match=match):
        render_cosys_settings(config, plan, base)


def test_settings_renderer_rejects_nonfinite_json(config, plan):
    base = _base_settings(config)
    base["invalid"] = float("inf")
    with pytest.raises(LaunchPlacementError, match="finite JSON"):
        render_cosys_settings(config, plan, base)


def test_renderer_rejects_plan_roster_drift(config, plan):
    changed_position = replace(plan.positions[0], vehicle_name="outsider")
    changed_plan = replace(plan, positions=(changed_position,) + plan.positions[1:])
    with pytest.raises(LaunchPlacementError, match="roster or order"):
        render_cosys_settings(config, changed_plan, _base_settings(config))


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (
            lambda plan: replace(plan, deterministic_seed=plan.deterministic_seed + 1),
            "seed does not match",
        ),
        (
            lambda plan: replace(plan, accepted_candidate_count=0),
            "accepted candidate count",
        ),
        (
            lambda plan: replace(plan, pairwise_distances=plan.pairwise_distances[:-1]),
            "pairwise distance count",
        ),
        (
            lambda plan: replace(
                plan,
                minimum_pairwise_distance_m=plan.minimum_pairwise_distance_m + 1.0,
            ),
            "minimum distance",
        ),
    ],
)
def test_renderer_revalidates_tampered_plan(config, plan, change, match):
    with pytest.raises(LaunchPlacementError, match=match):
        render_cosys_settings(config, change(plan), _base_settings(config))


def test_manifest_is_deterministic_complete_and_hash_linked(loaded, plan):
    config, config_sha256 = loaded
    settings = render_cosys_settings(config, plan, _base_settings(config))

    first = render_launch_manifest(config, config_sha256, plan, settings)
    second = render_launch_manifest(config, config_sha256.upper(), plan, settings)
    manifest = json.loads(first)

    assert first == second
    assert manifest["schema"] == LAUNCH_MANIFEST_SCHEMA_ID
    assert manifest["source_configuration"]["sha256"] == config_sha256
    assert manifest["settings_artifact"]["sha256"] == hashlib.sha256(
        settings
    ).hexdigest()
    assert manifest["candidate_summary"] == {
        "accepted": plan.accepted_candidate_count,
        "evaluated": plan.candidate_count,
        "rejected": plan.candidate_count - plan.accepted_candidate_count,
    }
    assert len(manifest["positions"]) == config.fleet.expected_count
    assert len(manifest["pairwise_distances"]) == 10
    assert all(manifest["checks"].values())
    assert manifest["metrics"]["minimum_pairwise_distance_m"] == (
        plan.minimum_pairwise_distance_m
    )

    claimed_hash = manifest.pop("manifest_payload_sha256")
    canonical = json.dumps(
        manifest,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert claimed_hash == hashlib.sha256(canonical).hexdigest()


def test_manifest_records_rejections_and_scene_validation(config):
    provider = SyntheticClearanceProvider(
        config.launch_area.ground_clearance_probe,
        ground_blocked={"r0c0"},
        scene_validated=True,
    )
    plan = generate_launch_plan(config, provider)
    settings = render_cosys_settings(config, plan, _base_settings(config))
    manifest = json.loads(render_launch_manifest(config, "a" * 64, plan, settings))

    assert manifest["clearance_provider"]["scene_validated"] is True
    rejected = [
        item for item in manifest["clearance_evaluations"] if not item["ground_clear"]
    ]
    assert [item["candidate_id"] for item in rejected] == ["r0c0"]
    assert rejected[0]["reason"] == "ground_blocked"


@pytest.mark.parametrize("bad_hash", ["", "a" * 63, "g" * 64, None])
def test_manifest_rejects_invalid_configuration_hash(config, plan, bad_hash):
    settings = render_cosys_settings(config, plan, _base_settings(config))
    with pytest.raises(LaunchPlacementError, match="SHA-256"):
        render_launch_manifest(config, bad_hash, plan, settings)


def test_manifest_requires_settings_bytes(config, plan):
    with pytest.raises(LaunchPlacementError, match="must be bytes"):
        render_launch_manifest(config, "a" * 64, plan, "not-bytes")


def test_artifact_writer_is_create_once_and_hashes_exact_bytes(tmp_path):
    destination = tmp_path / "launch-manifest.json"
    content = b"artifact bytes\n"

    digest = write_create_once(destination, content)

    assert destination.read_bytes() == content
    assert digest == hashlib.sha256(content).hexdigest()
    with pytest.raises(LaunchPlacementError, match="refusing to overwrite"):
        write_create_once(destination, b"replacement")
    assert destination.read_bytes() == content


def test_artifact_writer_requires_bytes(tmp_path):
    with pytest.raises(LaunchPlacementError, match="must be bytes"):
        write_create_once(tmp_path / "artifact.json", "text")
