from __future__ import annotations

import os

import pytest

from qfw_slurm_gateway.authentication import (
    AuthenticationError,
    MungeAuthenticator,
)


def test_munge_round_trip() -> None:
    authenticator = MungeAuthenticator()
    try:
        identity, payload = authenticator.decode(
            authenticator.encode(b"qsgp-authentication-test")
        )
    except AuthenticationError as error:
        pytest.skip(f"MUNGE daemon is unavailable: {error}")
    assert identity.uid == os.getuid()
    assert identity.gid == os.getgid()
    assert payload == b"qsgp-authentication-test"
