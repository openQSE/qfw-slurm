#!/bin/sh

set -eu

driver=$1
python=${QFW_TEST_PYTHON:-python3}
defw_runner=${QFW_TEST_DEFW_RUNNER:-}

if [ "$(id -u)" -ne 0 ] || ! munge -n >/dev/null 2>&1; then
	exit 77
fi

work=$(mktemp -d /tmp/qfw-slurm-driver-system-XXXXXX)
gateway_pid=

cleanup()
{
	if [ -n "${gateway_pid}" ]; then
		kill -TERM "${gateway_pid}" >/dev/null 2>&1 || true
		count=0
		while kill -0 "${gateway_pid}" >/dev/null 2>&1; do
			count=$((count + 1))
			if [ "${count}" -ge 50 ]; then
				kill -KILL "${gateway_pid}" >/dev/null 2>&1 || true
				break
			fi
			sleep 0.02
		done
		wait "${gateway_pid}" >/dev/null 2>&1 || true
	fi
	rm -rf "${work}"
}
trap cleanup EXIT INT TERM

cluster=test-cluster
job_id=451
job_uid=$(id -u)
job_gid=$(id -g)
epoch=1788000000

stop_gateway()
{
	if [ -n "${gateway_pid}" ]; then
		kill -TERM "${gateway_pid}"
		count=0
		while kill -0 "${gateway_pid}" >/dev/null 2>&1; do
			count=$((count + 1))
			if [ "${count}" -ge 50 ]; then
				kill -KILL "${gateway_pid}" >/dev/null 2>&1 || true
				break
			fi
			sleep 0.02
		done
		wait "${gateway_pid}" >/dev/null 2>&1 || true
		gateway_pid=
	fi
}

start_gateway()
{
	mode=$1
	case_dir="${work}/${mode}"
	mkdir -p "${case_dir}"
	ready="${case_dir}/ready.json"
	journal="${case_dir}/journal.sqlite3"
	if [ -n "${defw_runner}" ]; then
		set -- "${defw_runner}" -m tests.system.deterministic_gateway
	else
		set -- "${python}" -m tests.system.deterministic_gateway
	fi
	"$@" \
		--journal "${journal}" \
		--ready-file "${ready}" \
		--cluster "${cluster}" \
		--job-id "${job_id}" \
		--uid "${job_uid}" \
		--gid "${job_gid}" \
		--timeout-seconds 0.5 \
		--mode "${mode}" \
		>"${case_dir}/gateway.log" 2>&1 &
	gateway_pid=$!
	count=0
	while [ ! -s "${ready}" ]; do
		if ! kill -0 "${gateway_pid}" >/dev/null 2>&1; then
			cat "${case_dir}/gateway.log" >&2
			exit 1
		fi
		count=$((count + 1))
		if [ "${count}" -ge 100 ]; then
			echo "deterministic gateway did not become ready" >&2
			exit 1
		fi
		sleep 0.05
	done
	port=$("${python}" -c \
		'import json,sys; print(json.load(open(sys.argv[1]))["port"])' \
		"${ready}")
	plugin_config="${case_dir}/plugin.conf"
	{
		echo '[gateway]'
		echo 'host=127.0.0.1'
		echo "port=${port}"
		echo 'connect_timeout_ms=500'
		echo 'request_timeout_ms=1000'
		echo 'max_credential_bytes=65536'
		echo "expected_munge_uid=${job_uid}"
		echo
		echo '[resource "nwqsim"]'
		echo 'service_id=nwqsim-site'
	} >"${plugin_config}"
	chmod 600 "${plugin_config}"
}

driver_common()
{
	command=$1
	shift
	"${driver}" "${command}" \
		--config "${plugin_config}" \
		--cluster "${cluster}" \
		--job-id "${job_id}" \
		--uid "${job_uid}" \
		--gid "${job_gid}" \
		--allocation-epoch "${epoch}" \
		"$@"
}

reserve_arguments()
{
	driver_common "$1" \
		--walltime-seconds 60 \
		--qpu nwqsim \
		--workload-kind quantum \
		--circ-count 2 \
		--max-qubits 5 \
		--max-depth 100 \
		--max-shots 1024
}

expect_reserve_status()
{
	mode=$1
	expected=$2
	start_gateway "${mode}"
	set +e
	output=$(reserve_arguments reserve 2>&1)
	status=$?
	set -e
	if [ "${status}" -ne "${expected}" ]; then
		echo "${output}" >&2
		echo "mode ${mode}: expected ${expected}, got ${status}" >&2
		exit 1
	fi
	stop_gateway
}

start_gateway accepted
evaluated=$(reserve_arguments evaluate)
printf '%s\n' "${evaluated}" | grep -q '^evaluate .*state=accepted'
if printf '%s\n' "${evaluated}" | grep -q 'reservation='; then
	echo "evaluation returned a reservation" >&2
	exit 1
fi
"${python}" - "${journal}" <<'PY'
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
assert connection.execute(
    "SELECT count(*) FROM allocations"
).fetchone()[0] == 0
assert connection.execute(
    "SELECT state FROM operations WHERE operation = 'evaluate'"
).fetchone() == ("complete",)
PY
first=$(reserve_arguments reserve)
second=$(reserve_arguments reserve)
first_export=$(printf '%s\n' "${first}" | grep '^export QFW_RESERVATIONS=')
second_export=$(printf '%s\n' "${second}" | grep '^export QFW_RESERVATIONS=')
test "${first_export}" = "${second_export}"
printf '%s\n' "${first_export}" | grep -q 'nwqsim-site.*41'
looked_up=$(driver_common get-reservations)
lookup_export=$(printf '%s\n' "${looked_up}" | \
	grep '^export QFW_RESERVATIONS=')
test "${first_export}" = "${lookup_export}"
released=$(driver_common release)
printf '%s\n' "${released}" | grep -q 'state=released'
"${python}" - "${journal}" <<'PY'
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
allocation = connection.execute(
    "SELECT state FROM allocations WHERE canonical_job_id = '451'"
).fetchone()
reserve_count = connection.execute(
    "SELECT count(*) FROM operations WHERE operation = 'reserve'"
).fetchone()[0]
release_count = connection.execute(
    "SELECT count(*) FROM operations WHERE operation = 'release'"
).fetchone()[0]
assert allocation == ("released",)
assert reserve_count == 1
assert release_count == 1
PY
stop_gateway

expect_reserve_status delayed 6
expect_reserve_status rejected 6
expect_reserve_status qpm-failure 5
expect_reserve_status malformed-qpm 5
expect_reserve_status timeout 3

start_gateway release-unresolved
set +e
output=$(reserve_arguments lifecycle 2>&1)
status=$?
set -e
if [ "${status}" -ne 8 ]; then
	echo "${output}" >&2
	echo "release-unresolved: expected 8, got ${status}" >&2
	exit 1
fi
printf '%s\n' "${output}" | grep -q 'state=release-unresolved'
"${python}" - "${journal}" <<'PY'
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
allocation = connection.execute(
    "SELECT state FROM allocations WHERE canonical_job_id = '451'"
).fetchone()
reservation = connection.execute(
    "SELECT state FROM reservations WHERE service_id = 'nwqsim-site'"
).fetchone()
assert allocation == ("release-incomplete",)
assert reservation == ("release-failed",)
PY
stop_gateway
