#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "qfw_slurm_native.h"

static int append_text(char *output, size_t output_size, size_t *offset,
	const char *text)
{
	size_t length = strlen(text);

	if (*offset >= output_size || length >= output_size - *offset)
		return -1;
	memcpy(output + *offset, text, length);
	*offset += length;
	output[*offset] = '\0';
	return 0;
}

static int append_json_string(char *output, size_t output_size,
	size_t *offset, const char *value)
{
	const unsigned char *cursor;
	char escaped[7];

	if (append_text(output, output_size, offset, "\"") != 0)
		return -1;
	for (cursor = (const unsigned char *)value; *cursor != '\0'; cursor++) {
		if (*cursor == '"' || *cursor == '\\') {
			escaped[0] = '\\';
			escaped[1] = (char)*cursor;
			escaped[2] = '\0';
		} else if (*cursor < 0x20U) {
			(void)snprintf(escaped, sizeof(escaped), "\\u%04x", *cursor);
		} else {
			escaped[0] = (char)*cursor;
			escaped[1] = '\0';
		}
		if (append_text(output, output_size, offset, escaped) != 0)
			return -1;
	}
	return append_text(output, output_size, offset, "\"");
}

static int compare_results(const void *left, const void *right, void *context)
{
	const struct qsgp_reserve_response *response = context;
	size_t left_index = *(const size_t *)left;
	size_t right_index = *(const size_t *)right;

	return strcmp(response->results[left_index].service_id,
		response->results[right_index].service_id);
}

static void sort_indices(size_t *indices, size_t count,
	const struct qsgp_reserve_response *response)
{
	size_t index;

	for (index = 1; index < count; index++) {
		size_t value = indices[index];
		size_t position = index;

		while (position > 0 && compare_results(&indices[position - 1U],
			&value, (void *)response) > 0) {
			indices[position] = indices[position - 1U];
			position--;
		}
		indices[position] = value;
	}
}

int qfw_reservations_json(const struct qsgp_reserve_response *response,
	char *output, size_t output_size)
{
	size_t indices[QSGP_MAX_SERVICES];
	size_t offset = 0;
	size_t index;

	if (response == NULL || output == NULL || output_size < 3U ||
	    response->decision != QSGP_ADMISSION_ACCEPTED ||
	    response->result_count == 0 ||
	    response->result_count > QSGP_MAX_SERVICES)
		return -1;
	output[0] = '\0';
	for (index = 0; index < response->result_count; index++) {
		const struct qsgp_service_result *result = &response->results[index];

		if (result->decision != QSGP_ADMISSION_ACCEPTED ||
		    !result->has_reservation_id || result->reservation_id == 0 ||
		    result->service_id[0] == '\0')
			return -1;
		indices[index] = index;
	}
	sort_indices(indices, response->result_count, response);
	for (index = 1; index < response->result_count; index++) {
		if (strcmp(response->results[indices[index - 1U]].service_id,
			response->results[indices[index]].service_id) == 0)
			return -1;
	}
	if (append_text(output, output_size, &offset, "[") != 0)
		return -1;
	for (index = 0; index < response->result_count; index++) {
		const struct qsgp_service_result *result =
			&response->results[indices[index]];
		char reservation_id[32];

		if (index != 0 && append_text(output, output_size, &offset, ",") != 0)
			return -1;
		if (append_text(output, output_size, &offset, "[") != 0 ||
		    append_json_string(output, output_size, &offset,
			result->service_id) != 0 ||
		    append_text(output, output_size, &offset, ",") != 0)
			return -1;
		(void)snprintf(reservation_id, sizeof(reservation_id),
			"\"%" PRIu64 "\"", result->reservation_id);
		if (append_text(output, output_size, &offset, reservation_id) != 0 ||
		    append_text(output, output_size, &offset, "]") != 0)
			return -1;
	}
	return append_text(output, output_size, &offset, "]");
}
