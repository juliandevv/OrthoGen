"""Bootstrap launcher for OrthoGen.

The bundled ODM venv pins sys.path via a ``python._pth`` (ignoring PYTHONPATH
and cwd), so ``python -m orthogen`` cannot find the package. Running this file
inserts the project root at runtime, then dispatches to the CLI.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orthogen.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
