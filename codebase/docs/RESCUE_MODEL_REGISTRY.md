# Rescue model registry adapter

`tools.covis_multicam` can load the frozen `veriswarm.model.v1` registry without
changing the legacy YOLO/hash command. The registry is the authority for the model
hash, ordered class map, modality, model ID, input declaration and lifecycle status.

The adapter accepts only the frozen rescue ontology:
`person_candidate`, `water_or_flood`, `road_blocked`, `debris`, `fire`, `smoke` and
`structure_damage`. A model may declare any non-empty strict subset. The loaded
Ultralytics model's indexed class names must exactly equal the registry order; otherwise
startup fails before cameras are opened.

Copy `config/rescue_model_registry.example.json` outside Git, replace every placeholder
with the selected model's real immutable values, and run from `codebase`:

```powershell
python -m tools.covis_multicam `
  --camera cam1 0 dshow `
  --camera cam2 2 msmf `
  --weights C:\models\rescue.pt `
  --model-registry C:\models\rescue-model.json `
  --run-id RESCUE-MULTIVIEW-P2-01 `
  --record-video
```

The dashboard source panels and evidence records include the model ID and modality. The
run config and every JSONL event copy the validated registry record. Raw weights,
datasets, evidence, videos and private paths remain outside Git.

For backward compatibility, the existing `--weights` plus
`--expected-model-sha256` mode remains available. It does not provide a rescue ontology
or registry identity and is therefore a legacy/demo mode, not the selected rescue-model
handoff.
