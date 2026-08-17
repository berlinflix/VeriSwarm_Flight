#!/usr/bin/env bash
# Regenerate Python gRPC stubs from attestation.proto.
# Run from the repo root: bash proto/build.sh
set -euo pipefail

cd "$(dirname "$0")/.."

python3 -m grpc_tools.protoc \
  -I proto \
  --python_out=protocol \
  --grpc_python_out=protocol \
  --pyi_out=protocol \
  proto/attestation.proto

# Patch generated grpc code to use a package-relative import and keep the
# generated artifact free of whitespace errors checked by `git diff --check`.
sed -i 's/^import attestation_pb2 as attestation__pb2/from . import attestation_pb2 as attestation__pb2/' \
  protocol/attestation_pb2_grpc.py
sed -i 's/[[:space:]]*$//' protocol/attestation_pb2_grpc.py

echo "[ok] Regenerated protocol/attestation_pb2{,_grpc,.pyi}.py"
