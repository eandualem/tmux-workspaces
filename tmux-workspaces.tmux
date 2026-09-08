#!/bin/sh
# TPM entry point. Load from tmux with run-shell or through TPM.
exec python3 "$(dirname "$0")/scripts/tmux_plugin.py" install
