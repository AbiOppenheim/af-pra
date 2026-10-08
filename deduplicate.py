#!/usr/bin/env python3
"""
Stage 2: Fuzzy Deduplication Engine
Reads all JSON files from ./parsed_raw/, removes near-duplicate questions
(>= 80% similarity), and writes ./app/data/unified_questions.json.

Usage:
    python3 deduplicate.py
    python3 deduplicate.py --input ./parsed_raw/ --output ./app/data/unified_questions.json
    python3 deduplicate.py --threshold 0.85
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Use rapidfuzz if available (much faster), fall back to difflib
try:
    from rapidfuzz.fuzz import token_sort_ratio
    _FUZZY = "rapidfuzz"
    def similarity(a: str, b: str) -> float:
        return token_sort_ratio(a, b) / 100.0
except ImportError:
    import difflib
    _FUZZY = "difflib"
    def similarity(a: str, b: str) -> float:
        return difflib.SequenceMatcher(None, a, b).ratio()


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def metadata_score(q: dict) -> int:
    """Higher = more complete metadata. Used to pick the canonical duplicate."""
    score = 0
    if q.get("explanation") and len(q["explanation"]) > 50:
        score += 2
    if q.get("correctAnswer"):
        score += 2
    if q.get("options") and len(q["options"]) >= 3:
        score += 1
    if q.get("question") and len(q["question"]) > 30:
        score += 1
    return score


def load_all_questions(input_dir: Path) -> list[dict]:
    questions = []
    json_files = sorted(input_dir.glob("*.json"))
    if not json_files:
        sys.exit(f"No JSON files found in {input_dir}\nRun batch_parse.py first.")
    for json_file in json_files:
        with open(json_file, encoding="utf-8") as f:
            batch = json.load(f)
        print(f"  Loaded {len(batch):>4} questions from {json_file.name}")
        questions.extend(batch)
    return questions


def deduplicate(questions: list[dict], threshold: float) -> tuple[list[dict], list[dict]]:
    """
    O(n²) comparison with early exits. Returns (unique_questions, duplicate_records).
    duplicate_records is a list of log dicts for the summary report.
    """
    normalized = [normalize(q.get("question", "")) for q in questions]
    kept = [True] * len(questions)
    duplicate_log: list[dict] = []

    for i in range(len(questions)):
        if not kept[i]:
            continue
        for j in range(i + 1, len(questions)):
            if not kept[j]:
                continue

            # Skip comparison if normalised texts are very different in length
            len_i, len_j = len(normalized[i]), len(normalized[j])
            if len_i == 0 or len_j == 0:
                continue
            ratio = min(len_i, len_j) / max(len_i, len_j)
            if ratio < 0.5:
                continue

            score = similarity(normalized[i], normalized[j])
            if score >= threshold:
                # Keep the one with better metadata; ties go to i (lower index = earlier source)
                score_i = metadata_score(questions[i])
                score_j = metadata_score(questions[j])
                if score_j > score_i:
                    kept_idx, removed_idx = j, i
                else:
                    kept_idx, removed_idx = i, j

                kept[removed_idx] = False
                duplicate_log.append({
                    "kept_id": questions[kept_idx].get("id"),
                    "kept_source": questions[kept_idx].get("sourceFile", ""),
                    "removed_id": questions[removed_idx].get("id"),
                    "removed_source": questions[removed_idx].get("sourceFile", ""),
                    "similarity_pct": round(score * 100, 1),
                })

    unique = [q for q, keep in zip(questions, kept) if keep]
    return unique, duplicate_log


def reindex(questions: list[dict]) -> list[dict]:
    for new_id, q in enumerate(questions, start=1):
        q["id"] = new_id
    return questions


def main():
    parser = argparse.ArgumentParser(description="Deduplicate and unify parsed question JSON files.")
    parser.add_argument("--input", default="./parsed_raw/", help="Directory of parsed JSON files")
    parser.add_argument("--output", default="./app/data/unified_questions.json", help="Output unified JSON")
    parser.add_argument("--threshold", type=float, default=0.80, help="Similarity threshold (0–1, default 0.80)")
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_path = Path(args.output)
    threshold = args.threshold

    print(f"Fuzzy engine : {_FUZZY}")
    print(f"Threshold    : {threshold:.0%}")
    print(f"Input dir    : {input_dir}")
    print(f"Output file  : {output_path}\n")

    print("Loading questions...")
    questions = load_all_questions(input_dir)
    total_loaded = len(questions)
    print(f"\n  Total loaded: {total_loaded}\n")

    print("Deduplicating...")
    unique, dup_log = deduplicate(questions, threshold)
    n_removed = total_loaded - len(unique)

    print(f"\n── Deduplication Summary ─────────────────────────────────────")
    print(f"  Questions evaluated : {total_loaded}")
    print(f"  Duplicates removed  : {n_removed}")
    print(f"  Unique questions    : {len(unique)}")

    if dup_log:
        print(f"\n  High-confidence matches (top 10 shown):")
        for entry in sorted(dup_log, key=lambda x: -x["similarity_pct"])[:10]:
            print(
                f"    Matched Q{entry['removed_id']} ({entry['removed_source']}) "
                f"with Q{entry['kept_id']} ({entry['kept_source']}) "
                f"at {entry['similarity_pct']}% similarity — "
                f"keeping Q{entry['kept_id']}"
            )
    print(f"──────────────────────────────────────────────────────────────\n")

    unique = reindex(unique)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(unique, f, indent=2, ensure_ascii=False)
    print(f"Written {len(unique)} questions to: {output_path}")
    print("Done.")


if __name__ == "__main__":
    main()
