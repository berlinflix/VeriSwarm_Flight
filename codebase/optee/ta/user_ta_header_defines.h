#ifndef USER_TA_HEADER_DEFINES_H
#define USER_TA_HEADER_DEFINES_H

#include "veriswarm_ta.h"

#define TA_UUID			TA_VERISWARM_UUID

/*
 * The key and sign operation are cached per session (see the session context in
 * veriswarm_ta.c), so we do NOT need INSTANCE_KEEP_ALIVE for fast signing.
 * Leaving it off means a reinstalled TA loads fresh on the next session, so
 * code iterations don't require a reboot to clear a cached instance.
 */
#define TA_FLAGS		(TA_FLAG_SINGLE_INSTANCE | \
				 TA_FLAG_MULTI_SESSION)

#define TA_STACK_SIZE		(2 * 1024)
#define TA_DATA_SIZE		(32 * 1024)

#define TA_DESCRIPTION		"VeriSwarm Ed25519 attestation signer"
#define TA_VERSION		"1.0"

#endif /* USER_TA_HEADER_DEFINES_H */
