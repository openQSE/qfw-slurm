#define _POSIX_C_SOURCE 200809L

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <poll.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "qsgp/qsgp_protocol.h"

static int remaining_ms(const struct timespec *deadline)
{
	struct timespec now;
	int64_t nanoseconds;
	int64_t milliseconds;

	if (deadline == NULL)
		return -1;
	if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
		return 0;
	nanoseconds = (deadline->tv_sec - now.tv_sec) * INT64_C(1000000000) +
		(deadline->tv_nsec - now.tv_nsec);
	if (nanoseconds <= 0)
		return 0;
	milliseconds = (nanoseconds + INT64_C(999999)) / INT64_C(1000000);
	if (milliseconds > INT32_MAX)
		return INT32_MAX;
	return (int)milliseconds;
}
static int wait_for_fd(int fd, short events,
	const struct timespec *deadline)
{
	struct pollfd descriptor = {
		.fd = fd,
		.events = events,
	};
	int timeout;
	int result;

	for (;;) {
		timeout = remaining_ms(deadline);
		if (timeout == 0)
			return QSGP_ERR_TIMEOUT;
		result = poll(&descriptor, 1, timeout);
		if (result > 0) {
			if ((descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)) != 0)
				return QSGP_ERR_IO;
			if ((descriptor.revents & events) != 0)
				return QSGP_OK;
			continue;
		}
		if (result == 0)
			return QSGP_ERR_TIMEOUT;
		if (errno != EINTR)
			return QSGP_ERR_IO;
	}
}

int qsgp_deadline_after_ms(struct timespec *deadline, uint32_t timeout_ms)
{
	uint64_t nanoseconds;

	if (deadline == NULL || timeout_ms == 0)
		return QSGP_ERR_INVALID;
	if (clock_gettime(CLOCK_MONOTONIC, deadline) != 0)
		return QSGP_ERR_IO;
	nanoseconds = (uint64_t)deadline->tv_nsec +
		(uint64_t)timeout_ms * UINT64_C(1000000);
	deadline->tv_sec += (time_t)(nanoseconds / UINT64_C(1000000000));
	deadline->tv_nsec = (long)(nanoseconds % UINT64_C(1000000000));
	return QSGP_OK;
}

int qsgp_connect_deadline(const char *host, const char *port,
	const struct timespec *deadline)
{
	struct addrinfo hints;
	struct addrinfo *addresses = NULL;
	struct addrinfo *address;
	int status;
	int socket_fd = -1;

	if (host == NULL || *host == '\0' || port == NULL || *port == '\0' ||
	    deadline == NULL)
		return QSGP_ERR_INVALID;
	memset(&hints, 0, sizeof(hints));
	hints.ai_family = AF_UNSPEC;
	hints.ai_socktype = SOCK_STREAM;
	hints.ai_protocol = IPPROTO_TCP;
	status = getaddrinfo(host, port, &hints, &addresses);
	if (status != 0)
		return QSGP_ERR_IO;
	for (address = addresses; address != NULL; address = address->ai_next) {
		int flags;
		int socket_error;
		socklen_t error_size = sizeof(socket_error);

		socket_fd = socket(address->ai_family, address->ai_socktype,
			address->ai_protocol);
		if (socket_fd < 0)
			continue;
		flags = fcntl(socket_fd, F_GETFL, 0);
		if (flags < 0 || fcntl(socket_fd, F_SETFL, flags | O_NONBLOCK) < 0) {
			close(socket_fd);
			socket_fd = -1;
			continue;
		}
		status = connect(socket_fd, address->ai_addr,
			address->ai_addrlen);
		if (status == 0)
			break;
		if (errno != EINPROGRESS ||
		    wait_for_fd(socket_fd, POLLOUT, deadline) != QSGP_OK ||
		    getsockopt(socket_fd, SOL_SOCKET, SO_ERROR, &socket_error,
			&error_size) != 0 || socket_error != 0) {
			close(socket_fd);
			socket_fd = -1;
			continue;
		}
		break;
	}
	freeaddrinfo(addresses);
	if (socket_fd < 0 && remaining_ms(deadline) == 0)
		return QSGP_ERR_TIMEOUT;
	return socket_fd < 0 ? QSGP_ERR_IO : socket_fd;
}

int qsgp_write_all(int fd, const void *data, size_t size,
	const struct timespec *deadline)
{
	const uint8_t *cursor = data;
	size_t written = 0;

	if (fd < 0 || (data == NULL && size != 0) || deadline == NULL)
		return QSGP_ERR_INVALID;
	while (written < size) {
		ssize_t result;
		int status = wait_for_fd(fd, POLLOUT, deadline);

		if (status != QSGP_OK)
			return status;
		result = send(fd, cursor + written, size - written, MSG_NOSIGNAL);
		if (result > 0) {
			written += (size_t)result;
			continue;
		}
		if (result == 0)
			return QSGP_ERR_IO;
		if (errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK)
			return QSGP_ERR_IO;
	}
	return QSGP_OK;
}

int qsgp_read_exact(int fd, void *data, size_t size,
	const struct timespec *deadline)
{
	uint8_t *cursor = data;
	size_t received = 0;

	if (fd < 0 || (data == NULL && size != 0) || deadline == NULL)
		return QSGP_ERR_INVALID;
	while (received < size) {
		ssize_t result;
		int status = wait_for_fd(fd, POLLIN, deadline);

		if (status != QSGP_OK)
			return status;
		result = recv(fd, cursor + received, size - received, 0);
		if (result > 0) {
			received += (size_t)result;
			continue;
		}
		if (result == 0)
			return QSGP_ERR_IO;
		if (errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK)
			return QSGP_ERR_IO;
	}
	return QSGP_OK;
}

int qsgp_send_credential(int fd, const uint8_t *credential,
	size_t credential_size, const struct timespec *deadline)
{
	uint32_t encoded_size;
	int status;

	if (credential == NULL || credential_size == 0 ||
	    credential_size > QSGP_MAX_CREDENTIAL_SIZE ||
	    credential_size > UINT32_MAX)
		return QSGP_ERR_INVALID;
	encoded_size = htonl((uint32_t)credential_size);
	status = qsgp_write_all(fd, &encoded_size, sizeof(encoded_size), deadline);
	if (status != QSGP_OK)
		return status;
	return qsgp_write_all(fd, credential, credential_size, deadline);
}

int qsgp_receive_credential(int fd, uint8_t **credential,
	size_t *credential_size, size_t maximum_size,
	const struct timespec *deadline)
{
	uint32_t encoded_size;
	uint32_t decoded_size;
	uint8_t *buffer;
	int status;

	if (credential == NULL || credential_size == NULL || maximum_size == 0 ||
	    maximum_size > QSGP_MAX_CREDENTIAL_SIZE)
		return QSGP_ERR_INVALID;
	*credential = NULL;
	*credential_size = 0;
	status = qsgp_read_exact(fd, &encoded_size, sizeof(encoded_size), deadline);
	if (status != QSGP_OK)
		return status;
	decoded_size = ntohl(encoded_size);
	if (decoded_size == 0 || decoded_size > maximum_size)
		return QSGP_ERR_BOUNDS;
	buffer = malloc(decoded_size);
	if (buffer == NULL)
		return QSGP_ERR_NOMEM;
	status = qsgp_read_exact(fd, buffer, decoded_size, deadline);
	if (status != QSGP_OK) {
		free(buffer);
		return status;
	}
	*credential = buffer;
	*credential_size = decoded_size;
	return QSGP_OK;
}
