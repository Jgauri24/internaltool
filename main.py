#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.qualifier import qualify_urls
from src.sheets import read_urls, write_results


def main():
    load_dotenv()
    p = argparse.ArgumentParser(description="Qualify ICP accounts from website URLs")
    p.add_argument("urls", nargs="*", help="URLs to qualify")
    p.add_argument("-f", "--file", type=Path, help="File with one URL per line")
    p.add_argument("-o", "--output-dir", type=Path, default=Path("output/screenshots"))
    p.add_argument("--json-out", type=Path, help="Save results as JSON")
    p.add_argument("--sheet-id", help="Google Sheet ID to write results")
    p.add_argument("--read-urls-from-sheet", action="store_true")
    p.add_argument("--skip-traffic", action="store_true")
    args = p.parse_args()

    urls = list(args.urls)
    if args.file:
        urls += [l.strip() for l in args.file.read_text().splitlines() if l.strip()]
    if args.read_urls_from_sheet:
        if not args.sheet_id:
            sys.exit("Need --sheet-id with --read-urls-from-sheet")
        urls += read_urls(args.sheet_id)
    if not urls:
        p.print_help()
        sys.exit(1)

    urls = list(dict.fromkeys(urls))
    print(f"Qualifying {len(urls)} URL(s)...", file=sys.stderr)
    results = qualify_urls(urls, args.output_dir, skip_traffic=args.skip_traffic)

    for r in results:
        print(json.dumps(r.model_dump(), indent=2))

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps([r.model_dump() for r in results], indent=2))

    if args.sheet_id:
        write_results(args.sheet_id, results)
        print(f"Wrote {len(results)} row(s) to sheet", file=sys.stderr)


if __name__ == "__main__":
    main()
