"""CLI:
    python -m strong_app import [FILE ...]   # default: scan strong-extracts/
    python -m strong_app serve [--host H] [--port P]
"""
import argparse
import json
import sys


def main(argv=None):
    p = argparse.ArgumentParser(prog="strong_app")
    sub = p.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("import", help="import Strong CSV export(s)")
    pi.add_argument("files", nargs="*")
    ps = sub.add_parser("serve", help="run API + web UI")
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--port", type=int, default=8765)
    a = p.parse_args(argv)

    if a.cmd == "import":
        from .db import connect
        from .ingest import import_file, scan_folder
        conn = connect()
        res = [import_file(conn, f) for f in a.files] if a.files else scan_folder(conn)
        for r in res:
            r.pop("new_workout_dates", None)
        json.dump(res, sys.stdout, indent=2)
        print()
    else:
        import uvicorn
        uvicorn.run("strong_app.api:app", host=a.host, port=a.port)


if __name__ == "__main__":
    main()
