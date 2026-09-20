"""Public Hub identity for T3's Linux friendly-hostname probe."""
import os
import re
import tempfile
from urllib.parse import urlsplit


def public_host(value):
    if any(ord(c) <= 32 or ord(c) == 127 for c in value):
        return None
    try:
        url = urlsplit(value if '://' in value else 'https://' + value)
        host = url.hostname or ''
        if url.scheme not in ('http', 'https') or url.username or url.password:
            return None
        if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', host):
            return None
        return host.lower()
    except ValueError:
        return None


def environment_label(policy, environ):
    explicit = environ.get('NEURODESKTOP_T3_CODE_LABEL', '').strip()
    if explicit:
        return explicit
    user = environ.get('JUPYTERHUB_USER', '').strip()
    if not user or any(ord(c) < 32 for c in user):
        return ''
    host = None
    for key in ('JUPYTERHUB_PUBLIC_HUB_URL', 'JUPYTERHUB_PUBLIC_URL', 'JUPYTERHUB_HOST'):
        if environ.get(key):
            host = public_host(environ[key])
            if host:
                break
    if not host:
        try:
            host = public_host((policy.base_dir / 'neurodesktop-public-host').read_text().strip())
        except (OSError, UnicodeError):
            pass
    if not host:
        return ''  # Preserve T3's native hostname fallback outside JupyterHub.
    server = environ.get('JUPYTERHUB_SERVER_NAME', '').strip()
    return f'{user}{"/" + server if server else ""}@{host}'


def remember_public_host(policy, environ, host):
    """Called only after Jupyter authentication and XSRF validation."""
    if not environ.get('JUPYTERHUB_USER') or not public_host(host):
        return
    target = policy.base_dir / 'neurodesktop-public-host'
    value = public_host(host) + '\n'
    try:
        if target.read_text() == value:
            return
    except FileNotFoundError:
        pass
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.public-host-')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(value)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
