#!/usr/bin/env bash
# OrthoGen launcher (Linux) — runs the CLI inside the WebODM NodeODX container on the
# ODM engine's Python. Usage: ./orthogen.sh <command> [args]
exec "$(dirname "${BASH_SOURCE[0]}")/tools/container.sh" orthogen_cli.py "$@"
