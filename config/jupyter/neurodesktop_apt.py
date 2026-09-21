#!/usr/bin/python3 -I
"""Install repository packages without accepting caller-controlled apt options."""

import os
import re
import sys


def apt_arguments(arguments):
    if arguments == ["update"]:
        return ["update"]
    if not arguments or arguments[0] != "install":
        raise ValueError("Use apt update or apt install [-y] [--no-install-recommends] PACKAGE...")
    packages = []
    options = []
    for argument in arguments[1:]:
        if argument in ("-y", "--yes", "--assume-yes"):
            continue
        if argument == "--no-install-recommends":
            options = [argument]
            continue
        if not re.fullmatch(r"[a-z0-9][a-z0-9+.\-]*(?::[a-z0-9]+)?", argument):
            raise ValueError("Only repository package names and optional architecture qualifiers are allowed")
        if argument.split(":", 1)[0].endswith("-"):
            raise ValueError("Package removal requests are not allowed")
        packages.append(argument)
    if not packages:
        raise ValueError("Specify at least one repository package")
    return [
        "--assume-yes", "--no-remove",
        "-o", "Dpkg::Options::=--force-confold",
        "install", *options, "--", *packages,
    ]


def main():
    try:
        arguments = apt_arguments(sys.argv[1:])
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        os.execv("/usr/bin/sudo", ["sudo", "--", "/usr/local/bin/apt", *sys.argv[1:]])
    os.chdir("/")
    os.execve("/usr/bin/apt-get", ["apt-get", *arguments], {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/root", "LC_ALL": "C", "DEBIAN_FRONTEND": "noninteractive",
    })


if __name__ == "__main__":
    sys.exit(main())
