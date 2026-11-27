"""DEFw process-role configuration shared by qfw-slurm clients."""

from __future__ import annotations

# The DEFw program that runs a Python module as a DEFw client process. It
# runs the active interpreter on DEFw v2, takes ``-m MODULE``, and finds
# the directory from the parent variables its caller prepares.
DEFW_LAUNCHER = "defw2-python"
