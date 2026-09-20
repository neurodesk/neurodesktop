"""Credential destination policy shared by Notebook Intelligence setup steps."""

from urllib.parse import urlsplit


def is_neurodesk_endpoint(value):
    if not isinstance(value, str) or any(character.isspace() for character in value):
        return False
    try:
        url = urlsplit(value)
        return (
            url.scheme == "https"
            and url.hostname == "llm.neurodesk.org"
            and url.port in (None, 443)
            and url.username is None
            and url.password is None
            and not url.fragment
            and "\\" not in value
        )
    except ValueError:
        return False
