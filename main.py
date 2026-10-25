"""Inicializador compatível para a aplicação única mantida em src/main.py."""

import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    source_directory = Path(__file__).resolve().parent / "src"
    sys.path.insert(0, str(source_directory))
    try:
        runpy.run_path(str(source_directory / "main.py"), run_name="__main__")
    finally:
        sys.path.pop(0)
