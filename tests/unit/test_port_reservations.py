"""Port allocation holds real sockets until each service is ready to start."""
import socket

import pytest

from testlib import reserved_tcp_ports


def test_reservations_are_distinct_and_released_individually():
    with reserved_tcp_ports(3) as reservations:
        addresses = [sock.getsockname() for sock in reservations]
        assert len(set(addresses)) == 3
        for address in addresses:
            with socket.socket() as contender, pytest.raises(OSError):
                contender.bind(address)
        reservations[0].close()
        with socket.socket() as service:
            service.bind(addresses[0])
            for address in addresses[1:]:
                with socket.socket() as contender, pytest.raises(OSError):
                    contender.bind(address)
    for address in addresses:
        with socket.socket() as service:
            service.bind(address)


def test_reservations_close_when_startup_raises():
    with pytest.raises(RuntimeError, match="startup failed"):
        with reserved_tcp_ports(3) as reservations:
            addresses = [sock.getsockname() for sock in reservations]
            raise RuntimeError("startup failed")
    for address in addresses:
        with socket.socket() as service:
            service.bind(address)
