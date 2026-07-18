import socket

import pytest
from pytest_socket import SocketBlockedError


pytestmark = pytest.mark.unit


def test_unit_suite_blocks_network_sockets():
    with pytest.warns(UserWarning, match="tried to use socket"):
        with pytest.raises(SocketBlockedError):
            socket.socket()
