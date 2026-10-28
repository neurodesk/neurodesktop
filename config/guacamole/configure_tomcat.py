#!/usr/bin/python3 -I
"""Confine Guacamole's per-user Tomcat connector to loopback."""

from __future__ import annotations

import argparse
import os
import stat
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree


class ConfigurationError(Exception):
    """The supplied Tomcat configuration cannot be made safe."""


def configure_server(path: Path, port: int) -> None:
    """Set the sole HTTP connector's port/address and disable shutdown TCP."""
    if not 1024 <= port <= 65535:
        raise ConfigurationError("port must be between 1024 and 65535")

    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        tree = ElementTree.parse(path)
    except (OSError, ElementTree.ParseError) as error:
        raise ConfigurationError(f"cannot read {path}: {error}") from error

    root = tree.getroot()
    if root.tag != "Server":
        raise ConfigurationError("server.xml root must be Server")
    connectors = [
        element
        for element in root.iter("Connector")
        if element.get("protocol") == "HTTP/1.1"
    ]
    if len(connectors) != 1:
        raise ConfigurationError(
            "server.xml must contain exactly one active HTTP/1.1 connector"
        )

    root.set("port", "-1")
    connector = connectors[0]
    connector.set("port", str(port))
    connector.set("address", "127.0.0.1")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            tree.write(handle, encoding="utf-8", xml_declaration=True)
            os.fchmod(handle.fileno(), mode)
        os.replace(temporary, path)
    except OSError as error:
        raise ConfigurationError(f"cannot write {path}: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure the private Guacamole Tomcat listener."
    )
    parser.add_argument("server_xml", type=Path)
    parser.add_argument("port", type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        configure_server(args.server_xml, args.port)
    except ConfigurationError as error:
        print(f"configure_tomcat: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
