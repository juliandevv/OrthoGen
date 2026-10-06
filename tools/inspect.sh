#!/usr/bin/env bash
# Launcher (Linux) for the cross-band inspection tool — runs inspect_pair.py inside the
# WebODM NodeODX container. Usage from the project root:
#   tools/inspect.sh --capture 0188 --band NIR --detector all
exec "$(dirname "${BASH_SOURCE[0]}")/container.sh" tools/inspect_pair.py "$@"
