/*
 * VeriSwarm OP-TEE Trusted Application — Ed25519 attestation signer.
 *
 * Runs in the OP-TEE secure world on the Alpha node (Jetson Orin Nano). The
 * Ed25519 keypair is generated inside the TEE on first use and stored as a
 * persistent object in secure storage; the private key never leaves the secure
 * world. The normal world only ever sees the public key and signatures.
 *
 * Performance: the persistent key and a ready-to-use sign operation are loaded
 * ONCE per session (TA_OpenSessionEntryPoint) and cached in the session
 * context, so the hot signing path is just the secure-world Ed25519 sign and
 * does not re-open secure storage on every call. This is what makes the
 * signing-latency measurement (R2) reflect a real deployment rather than a
 * per-call secure-storage round-trip.
 *
 * Commands:
 *   TA_VERISWARM_CMD_GET_PUBKEY -> returns the 32-byte Ed25519 public key.
 *   TA_VERISWARM_CMD_SIGN       -> pure Ed25519 over the input message; returns
 *                                  the 64-byte signature. The message is the
 *                                  receipt's canonical bytes, so the signature
 *                                  verifies under the existing ReceiptVerifier.
 */

#include <tee_internal_api.h>
#include <tee_internal_api_extensions.h>
#include <tee_api_defines_extensions.h>

#include "veriswarm_ta.h"

#define KEY_OBJ_ID		"veriswarm.ed25519.v1"
#define ED25519_PUBLIC_LEN	32u
#define ED25519_SIG_LEN		64u

/* Per-session state: key + sign operation, loaded once at session open. */
struct vs_session {
	TEE_ObjectHandle key;
	TEE_OperationHandle sign_op;
};

/*
 * Open the persistent Ed25519 keypair, generating and persisting it on the
 * very first call. On success *out holds an open object handle.
 */
static TEE_Result load_or_create_keypair(TEE_ObjectHandle *out)
{
	TEE_Result res;
	TEE_ObjectHandle transient = TEE_HANDLE_NULL;
	TEE_ObjectHandle persistent = TEE_HANDLE_NULL;

	res = TEE_OpenPersistentObject(TEE_STORAGE_PRIVATE,
				       KEY_OBJ_ID, sizeof(KEY_OBJ_ID) - 1,
				       TEE_DATA_FLAG_ACCESS_READ,
				       &persistent);
	if (res == TEE_SUCCESS) {
		*out = persistent;
		return TEE_SUCCESS;
	}
	if (res != TEE_ERROR_ITEM_NOT_FOUND)
		return res;

	/* First boot: generate a fresh keypair entirely inside the TEE. */
	res = TEE_AllocateTransientObject(TEE_TYPE_ED25519_KEYPAIR, 256,
					 &transient);
	if (res != TEE_SUCCESS)
		return res;

	res = TEE_GenerateKey(transient, 256, NULL, 0);
	if (res != TEE_SUCCESS) {
		TEE_FreeTransientObject(transient);
		return res;
	}

	/* Persist it so the identity survives reboots. */
	res = TEE_CreatePersistentObject(TEE_STORAGE_PRIVATE,
					 KEY_OBJ_ID, sizeof(KEY_OBJ_ID) - 1,
					 TEE_DATA_FLAG_ACCESS_READ |
					 TEE_DATA_FLAG_ACCESS_WRITE,
					 transient, NULL, 0, &persistent);
	TEE_FreeTransientObject(transient);
	if (res != TEE_SUCCESS)
		return res;

	*out = persistent;
	return TEE_SUCCESS;
}

static TEE_Result cmd_get_pubkey(struct vs_session *s, uint32_t param_types,
				 TEE_Param params[4])
{
	const uint32_t expected = TEE_PARAM_TYPES(TEE_PARAM_TYPE_MEMREF_OUTPUT,
						  TEE_PARAM_TYPE_NONE,
						  TEE_PARAM_TYPE_NONE,
						  TEE_PARAM_TYPE_NONE);
	TEE_Result res;
	size_t len;  /* GP API expects size_t*; 8 bytes on aarch64 */

	if (param_types != expected)
		return TEE_ERROR_BAD_PARAMETERS;
	if (params[0].memref.size < ED25519_PUBLIC_LEN) {
		params[0].memref.size = ED25519_PUBLIC_LEN;
		return TEE_ERROR_SHORT_BUFFER;
	}

	len = params[0].memref.size;
	res = TEE_GetObjectBufferAttribute(s->key, TEE_ATTR_ED25519_PUBLIC_VALUE,
					   params[0].memref.buffer, &len);
	params[0].memref.size = len;
	return res;
}

static TEE_Result cmd_sign(struct vs_session *s, uint32_t param_types,
			   TEE_Param params[4])
{
	const uint32_t expected = TEE_PARAM_TYPES(TEE_PARAM_TYPE_MEMREF_INPUT,
						  TEE_PARAM_TYPE_MEMREF_OUTPUT,
						  TEE_PARAM_TYPE_NONE,
						  TEE_PARAM_TYPE_NONE);
	TEE_Result res;
	size_t sig_len;  /* GP API expects size_t*; 8 bytes on aarch64 */

	if (param_types != expected)
		return TEE_ERROR_BAD_PARAMETERS;
	if (params[1].memref.size < ED25519_SIG_LEN) {
		params[1].memref.size = ED25519_SIG_LEN;
		return TEE_ERROR_SHORT_BUFFER;
	}

	/*
	 * Pure Ed25519 over the whole message (no context, no pre-hash). The
	 * key was already bound to s->sign_op at session open, so this is just
	 * the secure-world signature.
	 */
	sig_len = params[1].memref.size;
	res = TEE_AsymmetricSignDigest(s->sign_op, NULL, 0,
				       params[0].memref.buffer,
				       params[0].memref.size,
				       params[1].memref.buffer, &sig_len);
	params[1].memref.size = sig_len;
	return res;
}

/* -- GlobalPlatform TA entry points ------------------------------------- */

TEE_Result TA_CreateEntryPoint(void)
{
	return TEE_SUCCESS;
}

void TA_DestroyEntryPoint(void)
{
}

TEE_Result TA_OpenSessionEntryPoint(uint32_t param_types,
				    TEE_Param params[4] __unused,
				    void **session)
{
	struct vs_session *s;
	TEE_Result res;

	(void)param_types;

	s = TEE_Malloc(sizeof(*s), TEE_MALLOC_FILL_ZERO);
	if (!s)
		return TEE_ERROR_OUT_OF_MEMORY;

	res = load_or_create_keypair(&s->key);
	if (res != TEE_SUCCESS)
		goto err;

	res = TEE_AllocateOperation(&s->sign_op, TEE_ALG_ED25519,
				    TEE_MODE_SIGN, 256);
	if (res != TEE_SUCCESS)
		goto err_key;

	res = TEE_SetOperationKey(s->sign_op, s->key);
	if (res != TEE_SUCCESS)
		goto err_op;

	*session = s;
	return TEE_SUCCESS;

err_op:
	TEE_FreeOperation(s->sign_op);
err_key:
	TEE_CloseObject(s->key);
err:
	TEE_Free(s);
	return res;
}

void TA_CloseSessionEntryPoint(void *session)
{
	struct vs_session *s = session;

	if (!s)
		return;
	if (s->sign_op != TEE_HANDLE_NULL)
		TEE_FreeOperation(s->sign_op);
	if (s->key != TEE_HANDLE_NULL)
		TEE_CloseObject(s->key);
	TEE_Free(s);
}

TEE_Result TA_InvokeCommandEntryPoint(void *session,
				      uint32_t cmd_id,
				      uint32_t param_types,
				      TEE_Param params[4])
{
	struct vs_session *s = session;

	switch (cmd_id) {
	case TA_VERISWARM_CMD_GET_PUBKEY:
		return cmd_get_pubkey(s, param_types, params);
	case TA_VERISWARM_CMD_SIGN:
		return cmd_sign(s, param_types, params);
	default:
		return TEE_ERROR_NOT_SUPPORTED;
	}
}
