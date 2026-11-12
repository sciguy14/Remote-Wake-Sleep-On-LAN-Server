# Python tests

Standard library only (`unittest`); no root, network, Apache or cloudflared needed. Run from the repo root:

    python -m unittest discover -s tests/python -v

`fakes.py` holds the test doubles: an in-memory Cloudflare API (zones, tunnels, DNS, Access), a fake command
runner that records every call, and a scripted UI. The tests exercise the `rwsols_setup` package, not `setup.py`.
