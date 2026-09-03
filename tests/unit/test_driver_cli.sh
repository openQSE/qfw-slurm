#!/bin/sh

set -eu

driver=$1

"${driver}" --help | grep -q \
	'evaluate|reserve|get-reservations|release|lifecycle'

if "${driver}" reserve >/dev/null 2>&1; then
	echo "driver accepted an incomplete reserve command" >&2
	exit 1
fi

if "${driver}" release \
	--config /does/not/exist \
	--cluster test \
	--job-id 1 \
	--uid 1 \
	--gid 1 \
	--allocation-epoch 1 >/dev/null 2>&1; then
	echo "driver accepted a missing protected configuration" >&2
	exit 1
fi

if nm -u "${driver}" | grep -Eq 'slurm|spank'; then
	echo "driver has a Slurm dependency" >&2
	exit 1
fi
