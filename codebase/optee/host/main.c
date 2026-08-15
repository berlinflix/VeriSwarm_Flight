/*
 * VeriSwarm OP-TEE host Client Application (CA).
 *
 * A tiny normal-world bridge between the Python signer
 * (signing/optee_backend.py) and the secure-world TA. It speaks a one-shot
 * line protocol so the Python side can just shell out to it:
 *
 *     veriswarm_optee_ca getpub                -> "<pubkey_hex>\n"
 *     veriswarm_optee_ca sign <message_hex>    -> "<signature_hex>\n"
 *
 * <message_hex> is the receipt's canonical bytes; the TA signs them with pure
 * Ed25519 so the signature verifies under the existing ReceiptVerifier.
 *
 * /dev/tee0 is root-only on stock L4T, so this is normally run via sudo.
 */

#include <err.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <tee_client_api.h>

#include "veriswarm_ta.h"

#define MAX_MSG_BYTES	8192u
#define ED25519_PUB_LEN	32u
#define ED25519_SIG_LEN	64u

static int hex_to_bin(const char *hex, uint8_t *out, size_t cap, size_t *out_len)
{
	size_t n = strlen(hex);
	size_t bytes = n / 2;
	size_t i;

	if (n % 2 || bytes > cap)
		return -1;
	for (i = 0; i < bytes; i++) {
		unsigned int v;

		if (sscanf(hex + 2 * i, "%2x", &v) != 1)
			return -1;
		out[i] = (uint8_t)v;
	}
	*out_len = bytes;
	return 0;
}

static void print_hex(const uint8_t *buf, size_t len)
{
	size_t i;

	for (i = 0; i < len; i++)
		printf("%02x", buf[i]);
	printf("\n");
}

int main(int argc, char *argv[])
{
	TEEC_Context ctx;
	TEEC_Session sess;
	TEEC_Operation op;
	TEEC_UUID uuid = TA_VERISWARM_UUID;
	TEEC_Result res;
	uint32_t origin = 0;
	int rc = 1;

	if (argc < 2) {
		fprintf(stderr, "usage: %s getpub | sign <message_hex>\n", argv[0]);
		return 2;
	}

	res = TEEC_InitializeContext(NULL, &ctx);
	if (res != TEEC_SUCCESS) {
		fprintf(stderr, "TEEC_InitializeContext failed: 0x%x\n", res);
		return 1;
	}

	res = TEEC_OpenSession(&ctx, &sess, &uuid, TEEC_LOGIN_PUBLIC,
			       NULL, NULL, &origin);
	if (res != TEEC_SUCCESS) {
		fprintf(stderr, "TEEC_OpenSession failed: 0x%x (origin 0x%x)\n",
			res, origin);
		TEEC_FinalizeContext(&ctx);
		return 1;
	}

	if (!strcmp(argv[1], "getpub")) {
		uint8_t pub[ED25519_PUB_LEN];

		memset(&op, 0, sizeof(op));
		op.paramTypes = TEEC_PARAM_TYPES(TEEC_MEMREF_TEMP_OUTPUT,
						 TEEC_NONE, TEEC_NONE, TEEC_NONE);
		op.params[0].tmpref.buffer = pub;
		op.params[0].tmpref.size = sizeof(pub);

		res = TEEC_InvokeCommand(&sess, TA_VERISWARM_CMD_GET_PUBKEY,
					 &op, &origin);
		if (res != TEEC_SUCCESS) {
			fprintf(stderr, "getpub failed: 0x%x (origin 0x%x)\n",
				res, origin);
			goto out;
		}
		print_hex(pub, op.params[0].tmpref.size);
		rc = 0;
	} else if (!strcmp(argv[1], "sign")) {
		static uint8_t msg[MAX_MSG_BYTES];
		uint8_t sig[ED25519_SIG_LEN];
		size_t msg_len = 0;

		if (argc < 3) {
			fprintf(stderr, "usage: %s sign <message_hex>\n", argv[0]);
			goto out;
		}
		if (hex_to_bin(argv[2], msg, sizeof(msg), &msg_len) != 0) {
			fprintf(stderr, "bad or oversized hex message\n");
			goto out;
		}

		memset(&op, 0, sizeof(op));
		op.paramTypes = TEEC_PARAM_TYPES(TEEC_MEMREF_TEMP_INPUT,
						 TEEC_MEMREF_TEMP_OUTPUT,
						 TEEC_NONE, TEEC_NONE);
		op.params[0].tmpref.buffer = msg;
		op.params[0].tmpref.size = msg_len;
		op.params[1].tmpref.buffer = sig;
		op.params[1].tmpref.size = sizeof(sig);

		res = TEEC_InvokeCommand(&sess, TA_VERISWARM_CMD_SIGN,
					 &op, &origin);
		if (res != TEEC_SUCCESS) {
			fprintf(stderr, "sign failed: 0x%x (origin 0x%x)\n",
				res, origin);
			goto out;
		}
		print_hex(sig, op.params[1].tmpref.size);
		rc = 0;
	} else if (!strcmp(argv[1], "bench")) {
		/*
		 * In-TEE signing-latency benchmark (experiment R2): open ONE session
		 * (already open) and sign a fixed message `iters` times, timing each
		 * TEEC_InvokeCommand. That captures the normal->secure world switch plus
		 * the Ed25519 sign inside the TEE, but NOT process spawn or session
		 * setup, so it is comparable to the in-process software sign in R1.
		 * One microsecond latency is printed per line for the harness to read.
		 */
		long iters = (argc >= 3) ? atol(argv[2]) : 1000;
		uint8_t msg[256];
		uint8_t sig[ED25519_SIG_LEN];
		long i;

		memset(msg, 0xab, sizeof(msg));
		memset(&op, 0, sizeof(op));
		op.paramTypes = TEEC_PARAM_TYPES(TEEC_MEMREF_TEMP_INPUT,
						 TEEC_MEMREF_TEMP_OUTPUT,
						 TEEC_NONE, TEEC_NONE);
		op.params[0].tmpref.buffer = msg;
		op.params[0].tmpref.size = sizeof(msg);
		op.params[1].tmpref.buffer = sig;
		op.params[1].tmpref.size = sizeof(sig);

		/* Warm up: force the TA instance + key to load before timing. */
		res = TEEC_InvokeCommand(&sess, TA_VERISWARM_CMD_SIGN, &op, &origin);
		if (res != TEEC_SUCCESS) {
			fprintf(stderr, "bench warmup failed: 0x%x (origin 0x%x)\n",
				res, origin);
			goto out;
		}

		for (i = 0; i < iters; i++) {
			struct timespec t0, t1;

			op.params[1].tmpref.size = sizeof(sig);
			clock_gettime(CLOCK_MONOTONIC, &t0);
			res = TEEC_InvokeCommand(&sess, TA_VERISWARM_CMD_SIGN,
						 &op, &origin);
			clock_gettime(CLOCK_MONOTONIC, &t1);
			if (res != TEEC_SUCCESS) {
				fprintf(stderr, "bench sign %ld failed: 0x%x\n", i, res);
				goto out;
			}
			printf("%.3f\n", (t1.tv_sec - t0.tv_sec) * 1e6 +
					 (t1.tv_nsec - t0.tv_nsec) / 1e3);
		}
		rc = 0;
	} else if (!strcmp(argv[1], "loadsign")) {
		/*
		 * Silent sustained signing loop for the energy measurement (R-EN):
		 * sign `iters` times with no per-call output, so the CPU/secure-world
		 * load reflects signing alone. The harness runs this in the background
		 * with a large count and terminates it after sampling power.
		 */
		long iters = (argc >= 3) ? atol(argv[2]) : 1000000;
		uint8_t msg[256];
		uint8_t sig[ED25519_SIG_LEN];
		long i;

		memset(msg, 0xab, sizeof(msg));
		memset(&op, 0, sizeof(op));
		op.paramTypes = TEEC_PARAM_TYPES(TEEC_MEMREF_TEMP_INPUT,
						 TEEC_MEMREF_TEMP_OUTPUT,
						 TEEC_NONE, TEEC_NONE);
		op.params[0].tmpref.buffer = msg;
		op.params[0].tmpref.size = sizeof(msg);
		op.params[1].tmpref.buffer = sig;
		op.params[1].tmpref.size = sizeof(sig);

		for (i = 0; i < iters; i++) {
			op.params[1].tmpref.size = sizeof(sig);
			res = TEEC_InvokeCommand(&sess, TA_VERISWARM_CMD_SIGN,
						 &op, &origin);
			if (res != TEEC_SUCCESS) {
				fprintf(stderr, "loadsign %ld failed: 0x%x\n", i, res);
				goto out;
			}
		}
		rc = 0;
	} else {
		fprintf(stderr, "unknown command: %s\n", argv[1]);
	}

out:
	TEEC_CloseSession(&sess);
	TEEC_FinalizeContext(&ctx);
	return rc;
}
