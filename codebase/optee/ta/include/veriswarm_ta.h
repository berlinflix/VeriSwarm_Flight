#ifndef VERISWARM_TA_H
#define VERISWARM_TA_H

/*
 * UUID of the VeriSwarm attestation Trusted Application.
 * Must match the BINARY name in the TA Makefile and the file dropped into
 * /lib/optee_armtz/<uuid>.ta, and the TEEC_UUID used by the host CA.
 */
#define TA_VERISWARM_UUID                                  \
	{ 0x7e9a4c10, 0x3b62, 0x4d8e,                      \
		{ 0xa1, 0xf5, 0x9c, 0x2b, 0x6d, 0x04, 0xe7, 0xa3 } }

/* Command IDs accepted by TA_InvokeCommandEntryPoint. */
#define TA_VERISWARM_CMD_GET_PUBKEY	0  /* out: 32-byte Ed25519 public key   */
#define TA_VERISWARM_CMD_SIGN		1  /* in: message bytes; out: 64-byte sig */

#endif /* VERISWARM_TA_H */
