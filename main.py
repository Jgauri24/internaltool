#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.qualifier import qualify_urls
from src.sheets import clear_results, existing_url_keys, read_urls, upsert_results, url_key, write_results


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
    p.add_argument("--force", action="store_true", help="Re-run URLs already in sheet and update their row")
    p.add_argument("--clear-sheet", action="store_true", help="Clear Qualification tab before writing")
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

    if args.sheet_id and args.clear_sheet:
        clear_results(args.sheet_id)
        print("Cleared Qualification sheet", file=sys.stderr)

    if args.sheet_id and not args.force and not args.clear_sheet:
        already = existing_url_keys(args.sheet_id)
        pending = [u for u in urls if url_key(u) not in already]
        skipped = len(urls) - len(pending)
        if skipped:
            print(f"Skipping {skipped} URL(s) already in sheet", file=sys.stderr)
        urls = pending
        if not urls:
            print("All URLs already in sheet — use --force to re-run.", file=sys.stderr)
            sys.exit(0)

    print(f"Qualifying {len(urls)} URL(s)...", file=sys.stderr)
    results = qualify_urls(urls, args.output_dir, skip_traffic=args.skip_traffic)

    for r in results:
        print(json.dumps(r.model_dump(), indent=2))

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps([r.model_dump() for r in results], indent=2))

    if args.sheet_id:
        if args.force:
            count = upsert_results(args.sheet_id, results)
            print(f"Updated {count} row(s) in sheet", file=sys.stderr)
        else:
            count = write_results(args.sheet_id, results)
            print(f"Appended {count} new row(s) to sheet", file=sys.stderr)


if __name__ == "__main__":
    main()
