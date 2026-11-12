# Python tests

Standard library only (`unittest`); no root, network, Apache or cloudflared needed. Run from the repo root:

    python -m unittest discover -s tests/python -v

`fakes.py` holds the test doubles: an in-memory Cloudflare API (zones, tunnels, DNS, Access), a fake command
runner that records every call, and a scripted UI. `test_ui.py` covers the prompt layer (plain text UI driven by scripted input, the multi-select, rich/plain selection, the apt bootstrap) and the allowlist candidates. The `rich` smoke test is skipped when `rich` is not installed.
