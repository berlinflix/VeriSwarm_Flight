"""
OP-TEE hardware signing backend for the Alpha node (Jetson Orin Nano).

`OPTEEReceiptSigner` is a drop-in replacement for
`protocol.receipts.ReceiptSigner`: it exposes the same `.sign(receipt)`,
`.public_key_bytes`, and `.public_key_hex` surface, so `peer_consensus` and the
eval harness do not care which backend is in use. The difference is where the
private key lives. Here it is generated inside the OP-TEE Trusted Application
(secure world) and stored in OP-TEE secure storage; the normal world (this
Python process) only ever sees the public key and the signatures. The key bytes
never cross the secure-world boundary.

How the call actually reaches the secure world
----------------------------------------------
This Python class shells out to a small native Client Application (the "CA")
that performs the GlobalPlatform TEEC calls (`TEEC_InitializeContext`,
`TEEC_OpenSession`, `TEEC_InvokeCommand`). Keeping the TEEC plumbing in a tiny C
binary avoids fragile ctypes bindings against `libteec` and matches how OP-TEE
samples are structured. The CA speaks a trivial line protocol:

    veriswarm_optee_ca getpub                 -> prints "<pubkey_hex>\n"
    veriswarm_optee_ca sign <message_hex>     -> prints "<signature_hex>\n"

`message_hex` is the receipt's full canonical bytes; the TA signs them with
pure Ed25519 so the signature verifies under the existing ReceiptVerifier.

The CA path is taken from $VERISWARM_OPTEE_CA, falling back to a binary named
`veriswarm_optee_ca` on $PATH. See optee/README.md for the build/flash steps.

Until the Trusted Application is built and flashed, importing this module is
fine (so `eval.harness.optee_available` can probe it), but constructing or using
the signer on a machine without a TEE raises a clear error rather than guessing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from protocol.receipts import Receipt, SignedReceipt

_TEE_DEVICE = "/dev/tee0"
_DEFAULT_CA_NAME = "veriswarm_optee_ca"


class OPTEEUnavailable(RuntimeError):
    """Raised when the OP-TEE device or Client Application is not present."""


def _locate_ca() -> Optional[str]:
    env = os.environ.get("VERISWARM_OPTEE_CA")
    if env and Path(env).exists():
        return env
    return shutil.which(_DEFAULT_CA_NAME)


class OPTEEReceiptSigner:
    """Ed25519 signer whose private key lives inside the OP-TEE secure world."""

    def __init__(self, ca_path: Optional[str] = None, timeout_s: float = 5.0):
        self._ca = ca_path or _locate_ca()
        self._timeout = timeout_s
        self._public_key_hex: Optional[str] = None
        if not Path(_TEE_DEVICE).exists():
            raise OPTEEUnavailable(
                f"{_TEE_DEVICE} not found. This backend runs on the Jetson with "
                f"OP-TEE loaded. Use protocol.receipts.ReceiptSigner off-device."
            )
        if not self._ca:
            raise OPTEEUnavailable(
                "OP-TEE Client Application not found. Build it (see optee/README.md) "
                "and set $VERISWARM_OPTEE_CA or put 'veriswarm_optee_ca' on PATH."
            )

    # -- internal CA invocation ---------------------------------------------

    def _invoke(self, *args: str) -> str:
        proc = subprocess.run(
            [self._ca, *args],
            capture_output=True, text=True, timeout=self._timeout,
        )
        if proc.returncode != 0:
            raise OPTEEUnavailable(
                f"OP-TEE CA failed ({' '.join(args)}): rc={proc.returncode} "
                f"stderr={proc.stderr.strip()}"
            )
        return proc.stdout.strip()

    # -- ReceiptSigner-compatible surface -----------------------------------

    @property
    def public_key_hex(self) -> str:
        if self._public_key_hex is None:
            self._public_key_hex = self._invoke("getpub")
        return self._public_key_hex

    @property
    def public_key_bytes(self) -> bytes:
        return bytes.fromhex(self.public_key_hex)

    def sign(self, receipt: Receipt) -> SignedReceipt:
        """Sign the receipt's canonical bytes inside the TEE; return the pair.

        The TA must produce a *pure* Ed25519 signature over the exact canonical
        bytes (no normal-world pre-hash), because the existing
        `ReceiptVerifier` checks `verify_key.verify(receipt.canonical(), sig)`.
        Ed25519 does its own internal hashing, so a signature made this way
        verifies identically whether it came from the software or OP-TEE signer.
        """
        message_hex = receipt.canonical().hex()
        signature_hex = self._invoke("sign", message_hex)
        return SignedReceipt(receipt=receipt, signature=signature_hex)
