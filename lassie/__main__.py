# lassie - the web front-end for rex
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# SPDX-License-Identifier: MIT
"""Command line: python -m lassie [--config FILE] [--port N] [--open]"""
import argparse
import logging
import sys
import webbrowser

from lassie import __version__, config
from lassie.server import LassieServer


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lassie", description="Lassie - a browser window for the rex agent")
    ap.add_argument("--config", help="lassie.ini to read (absolute path)")
    ap.add_argument("--port", help="port on 127.0.0.1 (default 4100; 0 = any free port)")
    ap.add_argument("--open", action="store_true", help="open the start link in the default browser")
    ap.add_argument("--verbose", action="store_true", help="log every request")
    ap.add_argument("--version", action="version", version="lassie " + __version__)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="lassie: %(message)s")
    try:
        cfg = config.load(args.config, overrides={"port": args.port})
    except config.ConfigError as exc:
        print("lassie: %s" % exc, file=sys.stderr)
        return 2
    try:
        srv = LassieServer(cfg)
    except OSError as exc:
        print("lassie: cannot listen on 127.0.0.1:%d: %s" % (cfg.port, exc.strerror), file=sys.stderr)
        return 1
    srv.watch()
    print("Lassie %s on 127.0.0.1:%d%s" % (__version__, srv.port, (" (config %s)" % cfg.source) if cfg.source else ""))
    print("Open: %s" % srv.url, flush=True)
    if not srv.merman:
        print("merman-cli not found: mermaid blocks stay as code (set MERMAN_HOME or PATH)")
    if args.open:
        webbrowser.open(srv.url)
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.close_all()
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
