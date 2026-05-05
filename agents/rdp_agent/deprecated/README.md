# Deprecated / Legacy Code

This directory contains archived code that is no longer actively maintained.

## Contents

- **rdp_agent_legacy.py** — Previous implementation of the RDP Agent. Kept for reference and historical purposes. Do not use in production.

## Migration Notes

The legacy implementation has been superseded by the modular 8-step pipeline architecture in `../src/`. 

For all new development and usage, refer to:
- [Main README](../README.md)
- Core modules in `../src/`
- Web UI entry point: `../app.py`
- CLI entry point: `../src/cli.py`
