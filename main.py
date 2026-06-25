#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.qualifier import qualify_urls
from src.sheets import clear_results, existing_url_keys, read_urls, upsert_results, url_key, write_results

DEFAULT_URL_FILE = Path("urls.txt")


def load_urls_from_file(path: Path) -> list[str]:
    if not path.exists():
        sys.exit(f"URL file not found: {path}")
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def main():
    load_dotenv()
    if not os.getenv("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY is required — set it in .env")
    p = argparse.ArgumentParser(description="Qualify ICP accounts from website URLs")
    p.add_argument("urls", nargs="*", help="URLs to qualify")
    p.add_argument("-f", "--file", type=Path, default=None, help="Read URLs from this file instead of the sheet Input tab")
    p.add_argument("-o", "--output-dir", type=Path, default=Path("output"), help="Unused; kept for compatibility")
    p.add_argument("--json-out", type=Path, help="Save results as JSON")
    p.add_argument("--sheet-id", default=os.getenv("GOOGLE_SHEET_ID"), help="Google Sheet ID (or set GOOGLE_SHEET_ID in .env)")
    p.add_argument("--read-urls-from-sheet", action="store_true", help="Deprecated — sheet Input tab is used by default when GOOGLE_SHEET_ID is set")
    p.add_argument("--skip-traffic", action="store_true")
    p.add_argument("--force", action="store_true", help="Re-run URLs already in sheet and update their row")
    p.add_argument("--clear-sheet", action="store_true", help="Clear Qualification tab before writing")
    args = p.parse_args()

    urls = list(args.urls)
    if args.file:
        urls += load_urls_from_file(args.file)
        print(f"Reading URLs from {args.file}", file=sys.stderr)
    elif not urls and args.sheet_id:
        try:
            urls = read_urls(args.sheet_id)
            print("Reading URLs from sheet Input tab", file=sys.stderr)
        except Exception as e:
            sys.exit(f"Failed to read URLs from sheet Input tab: {e}")
    elif not urls:
        url_file = DEFAULT_URL_FILE if DEFAULT_URL_FILE.exists() else Path("icp_urls.txt")
        if url_file.exists():
            urls = load_urls_from_file(url_file)
            print(f"Reading URLs from {url_file}", file=sys.stderr)

    if not urls:
        sys.exit(
            "No URLs to qualify. Add URLs to the sheet Input tab (column A), "
            "set GOOGLE_SHEET_ID in .env, or pass URLs / -f urls.txt on the command line."
        )

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
    results = qualify_urls(urls, skip_traffic=args.skip_traffic)

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
