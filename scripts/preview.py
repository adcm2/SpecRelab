#!/usr/bin/env python3
"""Serve the built site at both domain root and /SpecRelab for local review."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import argparse
ROOT=Path(__file__).resolve().parents[1]
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,directory=None,**kwargs):
        super().__init__(*args,directory=str(directory or ROOT/".build/site"),**kwargs)
    def translate_path(self,path):
        parts=urlsplit(path)
        clean=parts.path
        if clean=="/SpecRelab": clean="/"
        elif clean.startswith("/SpecRelab/"): clean=clean[len("/SpecRelab"):]
        return super().translate_path(clean)
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--port",type=int,default=8000); ap.add_argument("--directory",type=Path,default=ROOT/".build/site")
    a=ap.parse_args()
    if not (a.directory/"index.html").is_file(): raise SystemExit(f"Build missing at {a.directory}; run scripts/build.py first")
    handler=lambda *args,**kwargs: Handler(*args,directory=a.directory,**kwargs)
    server=ThreadingHTTPServer(("127.0.0.1",a.port),handler)
    print(f"Preview: http://127.0.0.1:{a.port}/ and /SpecRelab/ (Ctrl-C to stop)",flush=True)
    server.serve_forever()
if __name__=="__main__": main()
