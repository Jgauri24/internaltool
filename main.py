#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.qualifier import fetch_traffic_for_urls, qualify_urls
from src.sheets import (
    INPUT2_SHEET,
    INPUT_SHEET,
    clear_results,
    clear_traffic_results,
    existing_url_keys,
    read_urls,
    upsert_results,
    upsert_traffic_results,
    url_key,
    write_results,
    write_traffic_results,
)

DEFAULT_URL_FILE = Path("urls.txt")


def load_urls_from_file(path: Path) -> list[str]:
    if not path.exists():
        sys.exit(f"URL file not found: {path}")
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _filter_pending(urls: list[str], sheet_id: str, sheet: str, force: bool) -> list[str]:
    if force:
        return urls
    already = existing_url_keys(sheet_id, sheet)
    pending = [u for u in urls if url_key(u) not in already]
    skipped = len(urls) - len(pending)
    if skipped:
        print(f"Skipping {skipped} URL(s) already in {sheet}", file=sys.stderr)
    return pending


def main():
    load_dotenv()
    if not os.getenv("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY is required — set it in .env")
    p = argparse.ArgumentParser(description="Qualify ICP accounts from website URLs")
    p.add_argument("urls", nargs="*", help="URLs to qualify (Gemini analysis)")
    p.add_argument("-f", "--file", type=Path, default=None, help="Read qualification URLs from file")
    p.add_argument("-o", "--output-dir", type=Path, default=Path("output"), help="Unused; kept for compatibility")
    p.add_argument("--json-out", type=Path, help="Save qualification results as JSON")
    p.add_argument("--sheet-id", default=os.getenv("GOOGLE_SHEET_ID"), help="Google Sheet ID (or set GOOGLE_SHEET_ID in .env)")
    p.add_argument("--qualify-only", action="store_true", help="Only run Gemini qualification (Input tab)")
    p.add_argument("--traffic-only", action="store_true", help="Only run DataForSEO traffic (Input2 tab)")
    p.add_argument("--force", action="store_true", help="Re-run URLs already in sheet and update their row")
    p.add_argument("--clear-sheet", action="store_true", help="Clear Qualification tab before writing")
    p.add_argument("--clear-traffic-sheet", action="store_true", help="Clear Traffic tab before writing")
    args = p.parse_args()

    if args.qualify_only and args.traffic_only:
        sys.exit("Use only one of --qualify-only or --traffic-only")

    run_qualify = not args.traffic_only
    run_traffic = not args.qualify_only

    qual_urls: list[str] = list(args.urls)
    traffic_urls: list[str] = []

    if args.file:
        qual_urls += load_urls_from_file(args.file)
        print(f"Reading qualification URLs from {args.file}", file=sys.stderr)
    elif args.sheet_id and not args.urls:
        if run_qualify:
            try:
                qual_urls = read_urls(args.sheet_id, INPUT_SHEET)
                print(f"Reading qualification URLs from {INPUT_SHEET} tab", file=sys.stderr)
            except Exception as e:
                sys.exit(f"Failed to read URLs from {INPUT_SHEET} tab: {e}")
        if run_traffic:
            try:
                traffic_urls = read_urls(args.sheet_id, INPUT2_SHEET)
                if traffic_urls:
                    print(f"Reading traffic URLs from {INPUT2_SHEET} tab", file=sys.stderr)
            except Exception as e:
                sys.exit(f"Failed to read URLs from {INPUT2_SHEET} tab: {e}")
    elif not qual_urls and run_qualify:
        url_file = DEFAULT_URL_FILE if DEFAULT_URL_FILE.exists() else Path("icp_urls.txt")
        if url_file.exists():
            qual_urls = load_urls_from_file(url_file)
            print(f"Reading qualification URLs from {url_file}", file=sys.stderr)

    qual_urls = list(dict.fromkeys(qual_urls))
    traffic_urls = list(dict.fromkeys(traffic_urls))

    if run_qualify and not qual_urls and not (run_traffic and traffic_urls):
        sys.exit(
            f"No qualification URLs. Add them to the {INPUT_SHEET} tab, "
            "or pass URLs / -f urls.txt on the command line."
        )
    if run_traffic and not traffic_urls and not (run_qualify and qual_urls):
        sys.exit(
            f"No traffic URLs. Add them to the {INPUT2_SHEET} tab for DataForSEO lookup only."
        )

    if args.sheet_id and args.clear_sheet:
        clear_results(args.sheet_id)
        print("Cleared Qualification sheet", file=sys.stderr)

    if args.sheet_id and args.clear_traffic_sheet:
        clear_traffic_results(args.sheet_id)
        print("Cleared Traffic sheet", file=sys.stderr)

    if run_qualify and qual_urls:
        if args.sheet_id:
            qual_urls = _filter_pending(qual_urls, args.sheet_id, "Qualification", args.force)
        if qual_urls:
            print(f"Qualifying {len(qual_urls)} URL(s)...", file=sys.stderr)
            results = qualify_urls(qual_urls)
            for r in results:
                print(json.dumps(r.model_dump(), indent=2))
            if args.json_out:
                args.json_out.parent.mkdir(parents=True, exist_ok=True)
                args.json_out.write_text(json.dumps([r.model_dump() for r in results], indent=2))
            if args.sheet_id:
                if args.force:
                    count = upsert_results(args.sheet_id, results)
                    print(f"Updated {count} row(s) in Qualification sheet", file=sys.stderr)
                else:
                    count = write_results(args.sheet_id, results)
                    print(f"Appended {count} new row(s) to Qualification sheet", file=sys.stderr)
        else:
            print("All qualification URLs already in sheet — use --force to re-run.", file=sys.stderr)

    if run_traffic and traffic_urls:
        if args.sheet_id:
            traffic_urls = _filter_pending(traffic_urls, args.sheet_id, "Traffic", args.force)
        if traffic_urls:
            print(f"Fetching traffic for {len(traffic_urls)} URL(s)...", file=sys.stderr)
            traffic_results = fetch_traffic_for_urls(traffic_urls)
            for r in traffic_results:
                print(json.dumps(r.model_dump(), indent=2))
            if args.sheet_id:
                if args.force:
                    count = upsert_traffic_results(args.sheet_id, traffic_results)
                    print(f"Updated {count} row(s) in Traffic sheet", file=sys.stderr)
                else:
                    count = write_traffic_results(args.sheet_id, traffic_results)
                    print(f"Appended {count} new row(s) to Traffic sheet", file=sys.stderr)
        else:
            print("All traffic URLs already in Traffic sheet — use --force to re-run.", file=sys.stderr)


if __name__ == "__main__":
    main()
