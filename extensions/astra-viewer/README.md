# Neurodesktop ASTRA viewer

`neurodesk_astra_view` is the read-only ASTRA provenance widget shipped in
Neurodesktop. It validates `astra-spec==0.0.12` projects, resolves one universe,
and optionally overlays a Lightcone run or Neurodesktop pilot receipt without
upgrading the module pilot beyond its amber trust level.

```python
from neurodesk_astra_view import AstraView

AstraView(
    "astra.yaml",
    universe="universes/bet-f-0-5.yaml",
    run="../receipt/receipt.json",  # optional
)
```

The widget is fully offline. Cytoscape.js is vendored in the wheel and no
frontend build or network fetch happens at installation or import time.

