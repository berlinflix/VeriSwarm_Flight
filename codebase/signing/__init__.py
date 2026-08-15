"""
Hardware signing backends for VeriSwarm.

The software backend (`protocol.receipts.ReceiptSigner`) holds the Ed25519 key
in process memory. On the Alpha node the key lives inside the OP-TEE secure
world and never leaves it; `optee_backend.OPTEEReceiptSigner` exposes the same
interface so the rest of the protocol is backend-agnostic.
"""
