"""python -m diptych → unified stranger smoke entrypoint (also: console script ``diptych``)."""
from __future__ import annotations

from diptych.smoke import main

if __name__ == "__main__":
    raise SystemExit(main())
