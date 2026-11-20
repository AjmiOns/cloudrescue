"""Read-only incident viewer:  python cli.py incidents | events <id>"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from incident_logger import IncidentLogger


def _ts(value: float | None) -> str:
    if value is None:
        return "-"
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cloudrescue-incidents")
    parser.add_argument("--db", default=os.environ.get("DB_PATH", "/data/incidents.db"))
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="cmd", required=True)
    inc = sub.add_parser("incidents", help="list recent incidents")
    inc.add_argument("--limit", type=int, default=20)
    ev = sub.add_parser("events", help="show the timeline of one incident")
    ev.add_argument("incident_id", type=int)
    args = parser.parse_args(argv)

    try:
        log = IncidentLogger(args.db, readonly=True)
    except Exception as exc:  # noqa: BLE001
        print(f"cannot open {args.db}: {exc}", file=sys.stderr)
        return 1

    if args.cmd == "incidents":
        rows = log.list_incidents(args.limit)
        if args.json:
            print(json.dumps(rows))
            return 0
        print(f"{'ID':>4}  {'TARGET':<16} {'DETECTED (UTC)':<20} {'OUTCOME':<22} {'MTTR(s)':>8} {'TRIES':>5}")
        for r in rows:
            mttr = "-" if r["mttr_s"] is None else f"{r['mttr_s']:.1f}"
            print(
                f"{r['id']:>4}  {r['target']:<16} {_ts(r['detected_at']):<20} "
                f"{(r['outcome'] or 'OPEN'):<22} {mttr:>8} {r['attempts']:>5}"
            )
    else:
        events = log.get_events(args.incident_id)
        if args.json:
            print(json.dumps(events))
            return 0
        for e in events:
            print(f"{_ts(e['ts'])}  {e['kind']:<20} {e['message']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
