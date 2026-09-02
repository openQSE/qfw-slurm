#!/usr/bin/env python3

import json
import os
import sys

operation = sys.argv[1]
mode = os.environ.get("QFW_TEST_DRIVER_MODE", "accepted")
with open(os.environ["QFW_TEST_DRIVER_LOG"], "a", encoding="utf-8") as stream:
    stream.write(operation + "\n")

result = {
    "schema": "qfw-slurm-driver-v1",
    "operation": operation,
    "request_id": 91,
    "state": mode,
    "diagnostic": "",
}
if operation == "reserve" and mode == "accepted":
    result["reservations"] = [["nwqsim-site", "41"]]
elif operation == "release":
    result["state"] = "released"
    result["unresolved_count"] = 0
print(json.dumps(result))
raise SystemExit(0 if result["state"] in {"accepted", "released"} else 6)
