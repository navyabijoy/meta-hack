"""Gecko backend."""

import os as _os

# Gecko was called Repro during early development; keep old settings working.
for _k, _v in list(_os.environ.items()):
    if _k.startswith("REPRO_") and ("GECKO_" + _k[6:]) not in _os.environ:
        _os.environ["GECKO_" + _k[6:]] = _v
