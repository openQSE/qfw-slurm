#ifndef QSGP_PROTOCOL_H
#define QSGP_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>
#include <time.h>

#include "qsgp/qsgp_types.h"

int qsgp_encode_reserve_request(const struct qsgp_reserve_request *request,
	uint64_t correlation_id, struct qsgp_frame *frame);
int qsgp_decode_reserve_request(const uint8_t *data, size_t size,
	struct qsgp_header *header, struct qsgp_reserve_request *request);

int qsgp_encode_reserve_response(const struct qsgp_reserve_response *response,
	uint64_t correlation_id, struct qsgp_frame *frame);
int qsgp_decode_reserve_response(const uint8_t *data, size_t size,
	struct qsgp_header *header, struct qsgp_reserve_response *response);

int qsgp_encode_release_request(const struct qsgp_release_request *request,
	uint64_t correlation_id, struct qsgp_frame *frame);
int qsgp_decode_release_request(const uint8_t *data, size_t size,
	struct qsgp_header *header, struct qsgp_release_request *request);

int qsgp_encode_release_response(const struct qsgp_release_response *response,
	uint64_t correlation_id, struct qsgp_frame *frame);
int qsgp_decode_release_response(const uint8_t *data, size_t size,
	struct qsgp_header *header, struct qsgp_release_response *response);

int qsgp_encode_error_response(const struct qsgp_error_response *response,
	uint64_t correlation_id, struct qsgp_frame *frame);
int qsgp_decode_error_response(const uint8_t *data, size_t size,
	struct qsgp_header *header, struct qsgp_error_response *response);

int qsgp_decode_header(const uint8_t *data, size_t size,
	struct qsgp_header *header);
void qsgp_frame_destroy(struct qsgp_frame *frame);
const char *qsgp_status_string(int status);

int qsgp_deadline_after_ms(struct timespec *deadline, uint32_t timeout_ms);
int qsgp_connect_deadline(const char *host, const char *port,
	const struct timespec *deadline);
int qsgp_write_all(int fd, const void *data, size_t size,
	const struct timespec *deadline);
int qsgp_read_exact(int fd, void *data, size_t size,
	const struct timespec *deadline);

int qsgp_munge_encode(const uint8_t *data, size_t size,
	uint8_t **credential, size_t *credential_size);
int qsgp_munge_decode(const uint8_t *credential, size_t credential_size,
	uint8_t **data, size_t *size, struct qsgp_peer_identity *identity);
void qsgp_munge_free(void *data);

int qsgp_send_credential(int fd, const uint8_t *credential,
	size_t credential_size, const struct timespec *deadline);
int qsgp_receive_credential(int fd, uint8_t **credential,
	size_t *credential_size, size_t maximum_size,
	const struct timespec *deadline);

#endif
