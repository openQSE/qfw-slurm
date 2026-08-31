#define _POSIX_C_SOURCE 200809L

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <pwd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "qfw_slurm_native.h"

static void set_error(char *error, size_t error_size, const char *message)
{
	if (error != NULL && error_size != 0)
		(void)snprintf(error, error_size, "%s", message);
}

static char *trim(char *value)
{
	char *end;

	while (isspace((unsigned char)*value))
		value++;
	end = value + strlen(value);
	while (end > value && isspace((unsigned char)end[-1]))
		end--;
	*end = '\0';
	return value;
}

static int copy_value(char *destination, size_t capacity, const char *value)
{
	size_t length = strlen(value);

	if (length == 0 || length >= capacity)
		return -1;
	memcpy(destination, value, length + 1U);
	return 0;
}

static int parse_u32(const char *value, uint32_t *output)
{
	char *end = NULL;
	unsigned long parsed;

	errno = 0;
	parsed = strtoul(value, &end, 10);
	if (errno != 0 || end == value || *end != '\0' || parsed > UINT32_MAX)
		return -1;
	*output = (uint32_t)parsed;
	return 0;
}

static int parse_size(const char *value, size_t *output)
{
	char *end = NULL;
	unsigned long long parsed;

	errno = 0;
	parsed = strtoull(value, &end, 10);
	if (errno != 0 || end == value || *end != '\0' || parsed > SIZE_MAX)
		return -1;
	*output = (size_t)parsed;
	return 0;
}

static int parse_expected_uid(const char *value, uid_t *uid)
{
	struct passwd *entry;
	uint32_t numeric;

	if (parse_u32(value, &numeric) == 0) {
		*uid = (uid_t)numeric;
		return 0;
	}
	entry = getpwnam(value);
	if (entry == NULL)
		return -1;
	*uid = entry->pw_uid;
	return 0;
}

static int parse_resource_header(const char *line, char *name,
	size_t name_size)
{
	const char prefix[] = "resource \"";
	size_t length = strlen(line);
	size_t prefix_length = sizeof(prefix) - 1U;

	if (length <= prefix_length + 1U ||
	    strncmp(line, prefix, prefix_length) != 0 ||
	    line[length - 1U] != '"')
		return -1;
	length -= prefix_length + 1U;
	if (length == 0 || length >= name_size)
		return -1;
	memcpy(name, line + prefix_length, length);
	name[length] = '\0';
	return 0;
}

void qfw_plugin_config_init(struct qfw_plugin_config *config)
{
	if (config == NULL)
		return;
	memset(config, 0, sizeof(*config));
	config->connect_timeout_ms = 5000;
	config->request_timeout_ms = 120000;
	config->max_credential_bytes = QSGP_MAX_CREDENTIAL_SIZE;
	config->expected_munge_uid = (uid_t)-1;
}

static int validate_config(const struct qfw_plugin_config *config,
	char *error, size_t error_size)
{
	if (config->gateway_host[0] == '\0' ||
	    config->gateway_port[0] == '\0') {
		set_error(error, error_size, "gateway host and port are required");
		return -1;
	}
	if (config->connect_timeout_ms == 0 ||
	    config->request_timeout_ms == 0 ||
	    config->request_timeout_ms < config->connect_timeout_ms) {
		set_error(error, error_size, "gateway timeouts are invalid");
		return -1;
	}
	if (config->max_credential_bytes == 0 ||
	    config->max_credential_bytes > QSGP_MAX_CREDENTIAL_SIZE) {
		set_error(error, error_size, "credential size limit is invalid");
		return -1;
	}
	if (config->expected_munge_uid == (uid_t)-1) {
		set_error(error, error_size, "expected_munge_uid is required");
		return -1;
	}
	if (config->resource_count == 0) {
		set_error(error, error_size, "at least one resource is required");
		return -1;
	}
	return 0;
}

int qfw_plugin_config_load(const char *path,
	struct qfw_plugin_config *config, char *error, size_t error_size)
{
	enum { SECTION_NONE, SECTION_GATEWAY, SECTION_RESOURCE } section =
		SECTION_NONE;
	struct qfw_resource_mapping *resource = NULL;
	char *line = NULL;
	size_t line_capacity = 0;
	ssize_t line_size;
	unsigned int line_number = 0;
	FILE *stream;
	struct stat metadata;
	int descriptor;
	int result = -1;

	if (path == NULL || config == NULL) {
		set_error(error, error_size, "configuration path is required");
		return -1;
	}
	qfw_plugin_config_init(config);
	descriptor = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
	if (descriptor < 0 || fstat(descriptor, &metadata) != 0 ||
	    !S_ISREG(metadata.st_mode) || metadata.st_uid != 0 ||
	    (metadata.st_mode & (S_IWGRP | S_IWOTH)) != 0) {
		if (descriptor >= 0)
			(void)close(descriptor);
		set_error(error, error_size,
			"plugin configuration must be a protected root-owned file");
		return -1;
	}
	stream = fdopen(descriptor, "r");
	if (stream == NULL) {
		(void)close(descriptor);
		set_error(error, error_size, "cannot open plugin configuration");
		return -1;
	}
	while ((line_size = getline(&line, &line_capacity, stream)) >= 0) {
		char *key;
		char *value;
		char *separator;

		(void)line_size;
		line_number++;
		key = trim(line);
		if (*key == '\0' || *key == '#' || *key == ';')
			continue;
		if (*key == '[') {
			size_t length = strlen(key);

			if (length < 3U || key[length - 1U] != ']')
				goto invalid_line;
			key[length - 1U] = '\0';
			key++;
			if (strcmp(key, "gateway") == 0) {
				section = SECTION_GATEWAY;
				resource = NULL;
				continue;
			}
			if (config->resource_count >= QFW_PLUGIN_MAX_RESOURCES)
				goto invalid_line;
			resource = &config->resources[config->resource_count];
			if (parse_resource_header(key, resource->name,
				sizeof(resource->name)) != 0)
				goto invalid_line;
			config->resource_count++;
			section = SECTION_RESOURCE;
			continue;
		}
		separator = strchr(key, '=');
		if (separator == NULL)
			goto invalid_line;
		*separator = '\0';
		value = trim(separator + 1U);
		key = trim(key);
		if (*key == '\0' || *value == '\0')
			goto invalid_line;
		if (section == SECTION_GATEWAY) {
			if (strcmp(key, "host") == 0) {
				if (copy_value(config->gateway_host,
					sizeof(config->gateway_host), value) != 0)
					goto invalid_line;
			} else if (strcmp(key, "port") == 0) {
				if (copy_value(config->gateway_port,
					sizeof(config->gateway_port), value) != 0)
					goto invalid_line;
			} else if (strcmp(key, "connect_timeout_ms") == 0) {
				if (parse_u32(value,
					&config->connect_timeout_ms) != 0)
					goto invalid_line;
			} else if (strcmp(key, "request_timeout_ms") == 0) {
				if (parse_u32(value,
					&config->request_timeout_ms) != 0)
					goto invalid_line;
			} else if (strcmp(key, "max_credential_bytes") == 0) {
				if (parse_size(value,
					&config->max_credential_bytes) != 0)
					goto invalid_line;
			} else if (strcmp(key, "expected_munge_uid") == 0) {
				if (parse_expected_uid(value,
					&config->expected_munge_uid) != 0)
					goto invalid_line;
			} else {
				goto invalid_line;
			}
		} else if (section == SECTION_RESOURCE && resource != NULL &&
			   strcmp(key, "service_id") == 0) {
			if (resource->service_id[0] != '\0' ||
			    copy_value(resource->service_id,
				sizeof(resource->service_id), value) != 0)
				goto invalid_line;
		} else {
			goto invalid_line;
		}
	}
	if (ferror(stream)) {
		set_error(error, error_size, "cannot read plugin configuration");
		goto out;
	}
	for (size_t index = 0; index < config->resource_count; index++) {
		size_t other;

		if (config->resources[index].service_id[0] == '\0') {
			set_error(error, error_size,
				"resource is missing service_id");
			goto out;
		}
		for (other = 0; other < index; other++) {
			if (strcmp(config->resources[index].name,
				config->resources[other].name) == 0 ||
			    strcmp(config->resources[index].service_id,
				config->resources[other].service_id) == 0) {
				set_error(error, error_size,
					"resource mappings must be unique");
				goto out;
			}
		}
	}
	result = validate_config(config, error, error_size);
	goto out;

invalid_line:
	if (error != NULL && error_size != 0)
		(void)snprintf(error, error_size,
			"invalid plugin configuration at line %u", line_number);
out:
	free(line);
	(void)fclose(stream);
	return result;
}

const char *qfw_plugin_config_service_id(
	const struct qfw_plugin_config *config, const char *resource_name)
{
	size_t index;

	if (config == NULL || resource_name == NULL)
		return NULL;
	for (index = 0; index < config->resource_count; index++) {
		if (strcmp(config->resources[index].name, resource_name) == 0)
			return config->resources[index].service_id;
	}
	return NULL;
}
