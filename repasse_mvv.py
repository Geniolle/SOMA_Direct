#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path

# Adiciona a raiz do projeto ao path
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from workflows.repasse_mvv_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
