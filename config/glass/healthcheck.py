#!/usr/bin/python3
"""Jupyter health in containers; display health in explicit Glass mode."""
import os
import socket

DISPLAY_SOCKET = "/tmp/.X11-unix/X0"


def main():
    mode = os.environ.get("NEURODESKTOP_RUNTIME", "container")
    if mode == "container":
        os.execv("/etc/jupyter/docker_healthcheck.py", ["docker_healthcheck.py"])
    elif mode == "glass":
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(1)
            connection.connect(DISPLAY_SOCKET)
    else:
        raise ValueError("Unknown Neurodesktop runtime mode")


if __name__ == "__main__":
    main()
