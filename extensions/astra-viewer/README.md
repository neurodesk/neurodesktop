# Neurodesktop ASTRA viewer

`neurodesk_astra_view` is the read-only ASTRA provenance widget shipped in
Neurodesktop. It validates `astra-spec==0.0.12` projects, resolves one universe,
and optionally overlays a Lightcone run manifest, `lc status` output, or
Workflow Run RO-Crate without overstating what that evidence proves.

```python
from neurodesk_astra_view import AstraView

AstraView(
    "astra.yaml",
    universe="universes/choice-a.yaml",
    run="run-manifest.json",  # optional
)
```

The widget is fully offline. The frontend is a self-contained SVG renderer
shipped in the wheel; no frontend build or network fetch happens at
installation or import time.

