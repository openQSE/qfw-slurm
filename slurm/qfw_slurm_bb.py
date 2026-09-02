#!/usr/bin/env python3
"""Private Slurm burst-buffer lifecycle helper."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_DELAYED = 10
EXIT_REJECTED = 20
EXIT_OPERATIONAL = 30
MAX_DIRECTIVE = 2048
IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]+$")
UINT = re.compile(r"^[0-9]+$")
FIELDS = (
    "qpu",
    "workload",
    "circuits",
    "qubits",
    "depth",
    "shots",
    "oneq",
    "twoq",
    "measurements",
)
REQUIRED = {"qpu", "workload", "circuits", "qubits", "depth", "shots"}
DRIVER_OPTIONS = {
    "qpu": "--qpu",
    "workload": "--workload-kind",
    "circuits": "--circ-count",
    "qubits": "--max-qubits",
    "depth": "--max-depth",
    "shots": "--max-shots",
    "oneq": "--max-one-q-gates",
    "twoq": "--max-two-q-gates",
    "measurements": "--max-measurements",
}


class HelperError(RuntimeError):
    """A bounded, user-visible lifecycle helper failure."""


def parse_directive(path: Path) -> dict[str, str]:
    data = path.read_text(encoding="utf-8")
    if len(data) > MAX_DIRECTIVE + 1:
        raise HelperError("QFW directive exceeds 2048 bytes")
    lines = [line.strip() for line in data.splitlines() if line.strip()]
    qfw_lines = [line for line in lines if line.startswith("#QFW ")]
    if len(qfw_lines) != 1:
        raise HelperError("exactly one QFW directive is required")
    tokens = qfw_lines[0].split()
    if tokens[:2] != ["#QFW", "v=1"]:
        raise HelperError("unsupported QFW directive version")
    values: dict[str, str] = {}
    for token in tokens[2:]:
        if "=" not in token:
            raise HelperError("malformed QFW directive field")
        name, value = token.split("=", 1)
        if name not in FIELDS or name in values or not value:
            raise HelperError(f"invalid or duplicate QFW field: {name}")
        values[name] = value
    missing = REQUIRED - values.keys()
    if missing:
        raise HelperError("missing QFW fields: " + ",".join(sorted(missing)))
    if values["workload"] not in {"quantum", "hybrid"}:
        raise HelperError("invalid QFW workload")
    services = values["qpu"].split(",")
    if not services or len(services) != len(set(services)):
        raise HelperError("QPM service list is empty or contains duplicates")
    if any(not IDENTIFIER.fullmatch(service) for service in services):
        raise HelperError("invalid QPM service identifier")
    for name in FIELDS[2:]:
        if name in values and (
            not UINT.fullmatch(values[name]) or int(values[name]) == 0
        ):
            raise HelperError(f"invalid QFW numeric field: {name}")
    return values


def state_path(state_dir: Path, cluster: str, job_id: int) -> Path:
    if not IDENTIFIER.fullmatch(cluster):
        raise HelperError("invalid Slurm cluster name")
    return state_dir / f"{cluster}-{job_id}.json"


def locate_state(args, allow_missing: bool = False) -> Path:
    if args.cluster != "auto":
        return state_path(args.state_dir, args.cluster, args.canonical_job_id)
    matches = list(args.state_dir.glob(f"*-{args.job_id}.json"))
    if len(matches) == 0 and allow_missing:
        raise HelperError(
            "cannot release allocation without configured Slurm cluster name"
        )
    if len(matches) != 1:
        raise HelperError("cannot identify allocation state for teardown")
    current = read_state(matches[0])
    args.cluster = str(current.get("cluster", ""))
    args.canonical_job_id = int(current.get("canonical_job_id", 0))
    return state_path(args.state_dir, args.cluster, args.canonical_job_id)


def write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_context(path: Path, reservations: list, job_id: int) -> None:
    value = json.dumps(reservations, separators=(",", ":"))
    targets = {path.with_suffix(".env")}
    if str(job_id) != path.stem.rsplit("-", 1)[-1]:
        targets.add(path.with_name(f"{path.stem.rsplit('-', 1)[0]}-{job_id}.env"))
    for target in targets:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", dir=target.parent
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(f"QFW_RESERVATIONS={value}\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


def read_state(path: Path) -> dict:
    try:
        metadata = path.stat()
        if metadata.st_mode & 0o077:
            raise HelperError("allocation state permissions are unsafe")
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise HelperError("accepted allocation state is unavailable") from error
    except (OSError, json.JSONDecodeError) as error:
        raise HelperError("allocation state is unreadable") from error
    if not isinstance(value, dict):
        raise HelperError("allocation state is malformed")
    return value


def allocation_epoch(submit_time: int, restart_count: int) -> int:
    if submit_time <= 0 or restart_count < 0 or restart_count > 999999:
        raise HelperError("invalid Slurm allocation epoch")
    value = submit_time * 1_000_000 + restart_count
    if value > (1 << 64) - 1:
        raise HelperError("Slurm allocation epoch exceeds uint64")
    return value


def driver_command(args, operation: str, directive: dict[str, str] | None):
    epoch = allocation_epoch(args.submit_time, args.restart_count)
    command = [
        str(args.driver),
        operation,
        "--config",
        str(args.plugin_config),
        "--cluster",
        args.cluster,
        "--job-id",
        str(args.canonical_job_id),
        "--uid",
        str(args.uid),
        "--gid",
        str(args.gid),
        "--allocation-epoch",
        str(epoch),
        "--json",
    ]
    if args.het_job_id:
        command.extend(
            [
                "--hetero-job-id",
                str(args.job_id),
                "--hetero-component",
                str(args.het_component),
            ]
        )
    if directive is not None:
        command.extend(["--walltime-seconds", str(args.walltime_seconds)])
        for name in FIELDS:
            if name in directive:
                command.extend([DRIVER_OPTIONS[name], directive[name]])
    return command, epoch


def invoke_driver(args, operation: str, directive=None):
    command, epoch = driver_command(args, operation, directive)
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=args.timeout_seconds,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise HelperError(
            completed.stderr.strip() or "native gateway operation returned no result"
        )
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise HelperError("native gateway operation returned invalid JSON") from error
    if result.get("operation") != operation:
        raise HelperError("native gateway operation returned the wrong result")
    result["driver_status"] = completed.returncode
    result["allocation_epoch"] = str(epoch)
    return result


def base_state(args, result: dict, state: str) -> dict:
    return {
        "schema": "qfw-slurm-allocation-v1",
        "state": state,
        "cluster": args.cluster,
        "canonical_job_id": str(args.canonical_job_id),
        "job_uid": args.uid,
        "job_gid": args.gid,
        "restart_count": args.restart_count,
        "allocation_epoch": result["allocation_epoch"],
        "request_id": str(result.get("request_id", 0)),
        "diagnostic": result.get("diagnostic", ""),
    }


def reservation_attempts(path: Path) -> int:
    try:
        value = read_state(path).get("reservation_attempts", 0)
    except HelperError:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HelperError("allocation reservation attempt state is invalid")
    return value


def evaluation(args, path: Path, directive: dict[str, str]) -> int:
    result = invoke_driver(args, "evaluate", directive)
    state = result.get("state")
    record = base_state(args, result, f"evaluation-{state}")
    record["reservation_attempts"] = reservation_attempts(path)
    write_state(path, record)
    if state == "accepted":
        return EXIT_OK
    if state == "delayed":
        return EXIT_DELAYED
    if state == "rejected":
        return EXIT_REJECTED
    raise HelperError(result.get("diagnostic") or "QPM evaluation failed")


def reservation(args, path: Path, directive: dict[str, str]) -> int:
    attempts = reservation_attempts(path)
    if attempts >= args.max_reservation_attempts:
        record = base_state(args, {
            "allocation_epoch": str(
                allocation_epoch(args.submit_time, args.restart_count)
            ),
            "request_id": 0,
            "diagnostic": "final QPM reservation attempt limit exhausted",
        }, "reservation-exhausted")
        record["reservation_attempts"] = attempts
        write_state(path, record)
        return EXIT_REJECTED
    result = invoke_driver(args, "reserve", directive)
    state = result.get("state")
    record = base_state(args, result, f"reservation-{state}")
    record["reservation_attempts"] = attempts + 1
    if state == "accepted":
        reservations = result.get("reservations")
        if not isinstance(reservations, list) or not reservations:
            raise HelperError("accepted reserve result has no reservations")
        record["reservations"] = reservations
        write_state(path, record)
        if args.job_id != args.canonical_job_id:
            write_state(
                state_path(args.state_dir, args.cluster, args.job_id), record
            )
        write_context(path, reservations, args.job_id)
        return EXIT_OK
    write_state(path, record)
    if state == "delayed":
        return EXIT_DELAYED
    if state == "rejected":
        return EXIT_REJECTED
    raise HelperError(result.get("diagnostic") or "QPM reservation failed")


def release(args, path: Path) -> int:
    canonical_path = path
    try:
        current = read_state(path)
    except HelperError:
        current = base_state(args, {
            "allocation_epoch": str(
                allocation_epoch(args.submit_time, args.restart_count)
            ),
            "request_id": 0,
            "diagnostic": "local reservation state was unavailable",
        }, "release-recovery")
    else:
        args.cluster = str(current.get("cluster", args.cluster))
        args.canonical_job_id = int(
            current.get("canonical_job_id", args.canonical_job_id)
        )
        canonical_path = state_path(
            args.state_dir, args.cluster, args.canonical_job_id
        )
    if current.get("state") not in {
        "release-recovery",
        "reservation-accepted", "release-unresolved"
    }:
        return EXIT_OK
    try:
        result = invoke_driver(args, "release")
        current["release"] = result
        current["state"] = "released" if result.get("state") == "released" else (
            "release-unresolved"
        )
    except (HelperError, OSError, subprocess.SubprocessError) as error:
        current["state"] = "release-unresolved"
        current["release"] = {"diagnostic": str(error)}
    write_state(path, current)
    if canonical_path != path:
        write_state(canonical_path, current)
    return EXIT_OK


def render_paths(args, path: Path) -> int:
    current = read_state(path)
    if current.get("state") != "reservation-accepted":
        raise HelperError("allocation has no accepted QPM reservation")
    value = json.dumps(current.get("reservations"), separators=(",", ":"))
    target = Path(args.path_file)
    target.write_text(f"QFW_RESERVATIONS={value}\n", encoding="utf-8")
    return EXIT_OK


def parser() -> argparse.ArgumentParser:
    output = argparse.ArgumentParser(prog="qfw-slurm-bb")
    output.add_argument("operation", choices=("evaluate", "reserve", "paths", "release", "status"))
    output.add_argument("--driver", type=Path, default=Path("/usr/bin/qfw-slurm-driver"))
    output.add_argument("--plugin-config", type=Path, default=Path("/etc/qfw-slurm/plugin.conf"))
    output.add_argument("--state-dir", type=Path, default=Path("/var/lib/qfw-slurm/allocations"))
    output.add_argument("--job-script", type=Path)
    output.add_argument("--path-file", type=Path)
    output.add_argument("--cluster", default="auto")
    output.add_argument("--job-id", required=True, type=int)
    output.add_argument("--canonical-job-id", required=True, type=int)
    output.add_argument("--uid", required=True, type=int)
    output.add_argument("--gid", required=True, type=int)
    output.add_argument("--submit-time", required=True, type=int)
    output.add_argument("--restart-count", type=int, default=0)
    output.add_argument("--walltime-seconds", type=int, default=60)
    output.add_argument("--het-job-id", type=int, default=0)
    output.add_argument("--het-component", type=int, default=0)
    output.add_argument("--timeout-seconds", type=float, default=125.0)
    output.add_argument("--max-reservation-attempts", type=int, default=8)
    return output


def main() -> int:
    args = parser().parse_args()
    try:
        if min(args.job_id, args.canonical_job_id, args.uid, args.gid) < 0:
            raise HelperError("invalid Slurm job identity")
        if args.max_reservation_attempts <= 0:
            raise HelperError("reservation attempt limit must be positive")
        path = locate_state(args, args.operation == "release")
        if args.operation == "status":
            print(json.dumps(read_state(path), sort_keys=True))
            return EXIT_OK
        if args.operation == "paths":
            if args.path_file is None:
                raise HelperError("--path-file is required")
            return render_paths(args, path)
        if args.operation == "release":
            return release(args, path)
        if args.job_script is None:
            raise HelperError("--job-script is required")
        directive = parse_directive(args.job_script)
        if args.operation == "evaluate":
            return evaluation(args, path, directive)
        return reservation(args, path, directive)
    except (HelperError, OSError, subprocess.SubprocessError) as error:
        print(f"qfw-slurm-bb: {error}", file=sys.stderr)
        return EXIT_OPERATIONAL


if __name__ == "__main__":
    raise SystemExit(main())
