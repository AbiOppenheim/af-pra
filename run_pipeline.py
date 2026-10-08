#!/usr/bin/env python3
"""
Full pipeline runner: batch parse → deduplicate → unified output.

Usage:
    python3 run_pipeline.py
    python3 run_pipeline.py --pdfs ./pdfs/ --threshold 0.80
    python3 run_pipeline.py --pdfs ./pdfs/ --raw ./parsed_raw/ --out ./app/data/unified_questions.json
"""

import argparse
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> None:
    print(f"\n{'─' * 62}")
    print(f"$ {' '.join(cmd)}")
    print(f"{'─' * 62}")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        sys.exit(f"\nPipeline aborted — step failed (exit {result.returncode}).")


def main():
    parser = argparse.ArgumentParser(description="Run the full PDF → unified JSON pipeline.")
    parser.add_argument("--pdfs", default="./pdfs/", help="Input PDF directory (default: ./pdfs/)")
    parser.add_argument("--raw", default="./parsed_raw/", help="Intermediate JSON directory (default: ./parsed_raw/)")
    parser.add_argument("--out", default="./app/data/unified_questions.json", help="Final output file")
    parser.add_argument("--threshold", default="0.80", help="Dedup similarity threshold (default: 0.80)")
    args = parser.parse_args()

    pdfs_dir = Path(args.pdfs)
    if not pdfs_dir.exists() or not list(pdfs_dir.glob("*.pdf")):
        print(f"No PDF files found in {pdfs_dir}.")
        print(f"Place your exam PDF files in {pdfs_dir} and re-run.")
        sys.exit(1)

    print("═" * 62)
    print("  Stage 1: Batch PDF Parsing")
    print("═" * 62)
    run([sys.executable, "batch_parse.py", "--input", str(pdfs_dir), "--output", args.raw])

    print("\n═" * 62)
    print("  Stage 2 + 3: Fuzzy Deduplication & Unification")
    print("═" * 62)
    run([sys.executable, "deduplicate.py",
         "--input", args.raw,
         "--output", args.out,
         "--threshold", args.threshold])

    print(f"\n{'═' * 62}")
    print(f"  Pipeline complete!")
    print(f"  Output: {args.out}")
    print(f"  Serve with: python3 -m http.server 8080")
    print(f"{'═' * 62}")


if __name__ == "__main__":
    main()
