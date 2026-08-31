#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <munge.h>

#include "qsgp/qsgp_protocol.h"

int qsgp_munge_encode(const uint8_t *data, size_t size,
	uint8_t **credential, size_t *credential_size)
{
	char *encoded = NULL;
	munge_ctx_t context;
	munge_err_t result;
	size_t length;

	if (data == NULL || size == 0 || size > QSGP_MAX_FRAME_SIZE ||
	    size > INT_MAX || credential == NULL || credential_size == NULL)
		return QSGP_ERR_INVALID;
	*credential = NULL;
	*credential_size = 0;
	context = munge_ctx_create();
	if (context == NULL)
		return QSGP_ERR_NOMEM;
	result = munge_encode(&encoded, context, data, (int)size);
	munge_ctx_destroy(context);
	if (result != EMUNGE_SUCCESS || encoded == NULL) {
		free(encoded);
		return QSGP_ERR_AUTH;
	}
	length = strlen(encoded);
	if (length == 0 || length > QSGP_MAX_CREDENTIAL_SIZE) {
		free(encoded);
		return QSGP_ERR_BOUNDS;
	}
	*credential = (uint8_t *)encoded;
	*credential_size = length;
	return QSGP_OK;
}
int qsgp_munge_decode(const uint8_t *credential, size_t credential_size,
	uint8_t **data, size_t *size, struct qsgp_peer_identity *identity)
{
	char *encoded;
	void *decoded = NULL;
	int decoded_size = 0;
	uid_t uid = 0;
	gid_t gid = 0;
	time_t encode_time = 0;
	munge_ctx_t context;
	munge_err_t result;

	if (credential == NULL || credential_size == 0 ||
	    credential_size > QSGP_MAX_CREDENTIAL_SIZE || data == NULL ||
	    size == NULL || identity == NULL)
		return QSGP_ERR_INVALID;
	*data = NULL;
	*size = 0;
	memset(identity, 0, sizeof(*identity));
	encoded = malloc(credential_size + 1U);
	if (encoded == NULL)
		return QSGP_ERR_NOMEM;
	memcpy(encoded, credential, credential_size);
	encoded[credential_size] = '\0';
	context = munge_ctx_create();
	if (context == NULL) {
		free(encoded);
		return QSGP_ERR_NOMEM;
	}
	result = munge_decode(encoded, context, &decoded, &decoded_size,
		&uid, &gid);
	free(encoded);
	if (result == EMUNGE_SUCCESS)
		(void)munge_ctx_get(context, MUNGE_OPT_ENCODE_TIME, &encode_time);
	munge_ctx_destroy(context);
	if (result != EMUNGE_SUCCESS || decoded == NULL || decoded_size <= 0) {
		free(decoded);
		return QSGP_ERR_AUTH;
	}
	if ((size_t)decoded_size > QSGP_MAX_FRAME_SIZE) {
		free(decoded);
		return QSGP_ERR_BOUNDS;
	}
	identity->uid = uid;
	identity->gid = gid;
	identity->encode_time = (unsigned int)encode_time;
	*data = decoded;
	*size = (size_t)decoded_size;
	return QSGP_OK;
}

void qsgp_munge_free(void *data)
{
	free(data);
}
