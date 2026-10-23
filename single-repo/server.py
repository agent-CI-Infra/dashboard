#!/usr/bin/env python3
"""Loopback-only static preview. Collect data with publish.py before starting."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1', choices=('127.0.0.1','localhost'))
    parser.add_argument('--port', default=8790, type=int)
    parser.add_argument('--directory', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    if not (args.directory / 'index.html').is_file():
        parser.error('generate the site with python3 publish.py first')
    server = ThreadingHTTPServer((args.host,args.port), partial(SimpleHTTPRequestHandler,directory=str(args.directory)))
    print(f'Static preview: http://{args.host}:{args.port}/',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__ == '__main__': main()
