"""Make :mod:`testlib` importable from both test tiers.

``tests/unit/`` and ``tests/container/`` are plain directories, and the image
installs the container tier next to this file at ``/opt/tests/``. Putting this
directory on ``sys.path`` explicitly means ``from testlib import ...`` resolves
the same way in every layout.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
