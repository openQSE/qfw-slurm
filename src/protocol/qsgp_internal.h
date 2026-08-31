#ifndef QSGP_INTERNAL_H
#define QSGP_INTERNAL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "qsgp/qsgp_protocol.h"

struct qsgp_builder {
	uint8_t *data;
	size_t size;
	size_t capacity;
};

struct qsgp_tlv {
	uint16_t type;
	uint16_t flags;
	const uint8_t *value;
	size_t length;
};

struct qsgp_cursor {
	const uint8_t *data;
	size_t size;
	size_t offset;
};

int qsgp_builder_init(struct qsgp_builder *builder, size_t initial_size);
void qsgp_builder_destroy(struct qsgp_builder *builder);
int qsgp_builder_add_raw(struct qsgp_builder *builder, const void *data,
	size_t size);
int qsgp_builder_add_tlv(struct qsgp_builder *builder, uint16_t type,
	uint16_t flags, const void *value, size_t size);
int qsgp_builder_add_u32(struct qsgp_builder *builder, uint16_t type,
	uint32_t value);
int qsgp_builder_add_u64(struct qsgp_builder *builder, uint16_t type,
	uint64_t value);
int qsgp_builder_add_string(struct qsgp_builder *builder, uint16_t type,
	const char *value, size_t maximum_size);
int qsgp_builder_finish(struct qsgp_builder *builder, uint16_t message_type,
	uint64_t correlation_id, struct qsgp_frame *frame);

int qsgp_cursor_next(struct qsgp_cursor *cursor, struct qsgp_tlv *tlv);
int qsgp_tlv_u32(const struct qsgp_tlv *tlv, uint32_t *value);
int qsgp_tlv_u64(const struct qsgp_tlv *tlv, uint64_t *value);
int qsgp_tlv_string(const struct qsgp_tlv *tlv, char *value,
	size_t capacity);
bool qsgp_known_tlv(uint16_t type);

uint64_t qsgp_hton64(uint64_t value);
uint64_t qsgp_ntoh64(uint64_t value);

#endif
