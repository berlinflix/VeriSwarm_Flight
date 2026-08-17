# VeriSwarm OP-TEE Trusted Application (Alpha node)

TEE-protected Ed25519 signer for the Jetson Orin Nano. The keypair is born and
lives inside the OP-TEE secure world; the normal world only ever sees the public
key and signatures. This demonstrates a hardware-isolated signing-key operation,
not trusted execution of inference. It feeds experiments **R2** (signing latency, Table 4.2 / Fig 12) and
**R-EN** (energy, Table 4.13).

```
optee/
  ta/                     # Trusted Application (secure world)
    veriswarm_ta.c        #   Ed25519 keygen in secure storage + sign/getpub
    include/veriswarm_ta.h#   UUID + command IDs (shared with the host CA)
    user_ta_header_defines.h
    sub.mk  Makefile
  host/                   # Client Application (normal world)
    main.c  Makefile      #   getpub / sign <hex> bridge for the Python signer
```

Target device (confirmed): **L4T R36.4.7 / JetPack 6.2**, OP-TEE ~3.22 (Ed25519
available), `/dev/tee0` present, `nvidia-l4t-optee` installed.

---

## 0. One-time: get the TA dev kit

Building a TA needs `TA_DEV_KIT_DIR` (headers + `ta_dev_kit.mk` + the TA signing
key) — **not** shipped by the `nvidia-l4t-optee` runtime package. First check
whether one is already on the device or in your SDK:

```bash
find / -path "*export-ta_arm64*" -name ta_dev_kit.mk 2>/dev/null
```

If that prints nothing, fetch and build NVIDIA's OP-TEE source for R36.4 (from
the Jetson Linux "OP-TEE" source bundle / `source_sync.sh`), which produces the
dev kit at `optee/optee_os/out/arm-plat-tegra/export-ta_arm64`. Then:

```bash
export TA_DEV_KIT_DIR=/path/to/export-ta_arm64
```

> The signing key matters: the TA must be signed with the key whose public half
> is baked into this device's OP-TEE core. The default OP-TEE dev kit key works
> on dev-fused Jetsons. If loading later fails with a signature error, we use
> NVIDIA's TA key from their OP-TEE source instead (see step 4 notes).

---

## 1. Build the TA

```bash
cd optee/ta
make                       # -> 7e9a4c10-3b62-4d8e-a1f5-9c2b6d04e7a3.ta (signed)
```

## 2. Install the TA

```bash
sudo cp 7e9a4c10-3b62-4d8e-a1f5-9c2b6d04e7a3.ta /lib/optee_armtz/
```

## 3. Build the host CA

```bash
cd ../host
make                       # -> veriswarm_optee_ca  (links against -lteec)
```

If `tee_client_api.h` is missing, install the client dev headers or point
`CFLAGS` at the `optee_client` `public` include dir from the same source bundle.

## 4. Smoke-test on the device

```bash
sudo ./veriswarm_optee_ca getpub
# -> 64 hex chars (32-byte Ed25519 public key); stable across reboots

sudo ./veriswarm_optee_ca sign 7b22746573...   # any hex; -> 128 hex chars (64-byte sig)
```

If `getpub` returns `0xffff0006` (NOT_SUPPORTED) on the sign path, this OP-TEE
build disabled Ed25519 — tell me and we switch the TA to the enabled curve.
If `OpenSession` returns a signature/verification error, it's the TA signing key
— rebuild with NVIDIA's key.

## 5. Wire it into the evaluation harness

The Python backend (`signing/optee_backend.py`) shells out to the CA. Point it
at the binary and run the hardware experiments:

```bash
export VERISWARM_OPTEE_CA=$PWD/veriswarm_optee_ca
cd ~/veriswarm
sudo -E python3 -m eval.run_all --hardware --only R2          # signing latency
# R-EN (energy) additionally samples tegrastats around the signing loop.
```

`eval.harness.optee_available()` returns true once `/dev/tee0` exists and the CA
is found, so R2/R-EN flip from PENDING to real measurements automatically.

`sudo -E` preserves the env var; `/dev/tee0` is root-only on stock L4T. To run
without sudo, add a udev rule granting your user group access to `/dev/tee0`.

---

## Notes
- Same keypair every boot (persistent object `veriswarm.ed25519.v1`), so Alpha's
  public key can go in the peer registry once and stays valid.
- The TA signs the full canonical receipt bytes (pure Ed25519), so its signatures
  verify under the unchanged `protocol/receipts.ReceiptVerifier` — software peers
  (Bravo/Charlie) and the hardware Alpha are cryptographically interchangeable to
  a verifier.
