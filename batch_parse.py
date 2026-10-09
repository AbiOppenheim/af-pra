#!/usr/bin/env python3
"""
Stage 1: Batch PDF Parser
Processes all PDFs in ./pdfs/ and writes individual JSON files to ./parsed_raw/.

Usage:
    python3 batch_parse.py
    python3 batch_parse.py --input ./pdfs/ --output ./parsed_raw/
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

try:
    import pdfplumber
except ImportError:
    sys.exit("pdfplumber not found. Run: pip3 install pdfplumber")

# ── Regex patterns (shared with parse_pdf.py) ────────────────────────────────

NEW_Q_RE = re.compile(r"NEW QUESTION:\s*(\d+)", re.IGNORECASE)
OPTION_LINE_RE = re.compile(r"^([A-F])[.)]\s+(.+)$")
DIVIDER_RE = re.compile(
    r"Answer:\s*\(SHOW ANSWER\)|Answer:\s*([A-F])\s*\(LEAVE A REPLY\)",
    re.IGNORECASE,
)
NOISE_RE = re.compile(
    r"(edudump\.com|freecram\.net|exam dumps|dumps with test engine|"
    r"special discount code|q&as dumps|35%off|https?://www\.|# of views|"
    r"# of questions|certification provider|exam code|exam name|free question|version:)",
    re.IGNORECASE,
)
ANSWER_PATTERNS = [
    re.compile(r"[Tt]he correct answer(?:s)? (?:is|are)\s+([A-F])\b"),
    re.compile(r"\boption ([A-F]) is (?:the )?correct", re.IGNORECASE),
    re.compile(r"Why Option ([A-F]) is [Cc]orrect", re.IGNORECASE),
    re.compile(r"(?:Therefore|Thus|Hence)[,\s]+(?:the\s+)?(?:correct\s+)?[Oo]ption ([A-F])", re.IGNORECASE),
    re.compile(r"answer is [Oo]ption ([A-F])\b", re.IGNORECASE),
    re.compile(r"([A-F]) is the correct answer", re.IGNORECASE),
    re.compile(r"correct answer[^.]*?is\s+([A-F])\b", re.IGNORECASE),
    re.compile(r"\([Oo]ption ([A-F])\)", re.IGNORECASE),
    re.compile(r"correct (?:action|approach|answer|option) is[^.]{0,200}\(([A-F])\)", re.IGNORECASE),
    re.compile(r"the answer is ([A-F])\b", re.IGNORECASE),
    re.compile(r"^Answer:\s*([A-F])\b", re.IGNORECASE | re.MULTILINE),
]

# ── Core parsing helpers ──────────────────────────────────────────────────────

def extract_full_text(pdf_path: Path) -> str:
    pages = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n".join(pages)


def clean_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not NOISE_RE.search(line)
    ]


def _normalize_explanation(text: str) -> str:
    """Strip extra whitespace and normalize for fuzzy matching."""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# Option section boundary: "- Option A:", "* Option A:", or just "Option A:" at start of segment
_OPT_SECTION_RE = re.compile(
    r"(?:[-*]\s*)?[Oo]ption\s*([A-F])\s*:?\s*",
)

# Markers indicating an option is correct (case-insensitive, handles missing-space artifacts)
_CORRECT_MARKERS = [
    re.compile(r"\bThis\s+is\s+the\s+correct\s+answer\b", re.IGNORECASE),
    re.compile(r"\bThis\s+is\s+correct\b", re.IGNORECASE),
    re.compile(r"\bis\s+the\s+correct\s+answer\b", re.IGNORECASE),
    re.compile(r"\bcorrect\s+answer\b", re.IGNORECASE),
    re.compile(r"\bThis\s+option\s+is\s+correct\b", re.IGNORECASE),
    re.compile(r"\bOption\s*[A-F]\s*is\s+(?:the\s+)?correct\b", re.IGNORECASE),
    re.compile(r"\bis\s+correct\b", re.IGNORECASE),
]

# Markers indicating an option is incorrect
_INCORRECT_MARKERS = [
    re.compile(r"\bis\s+incorrect\b", re.IGNORECASE),
    re.compile(r"\bThis\s+option\s+is\s+incorrect\b", re.IGNORECASE),
    re.compile(r"\bincorrect\s+because\b", re.IGNORECASE),
    re.compile(r"\bnot\s+(?:the\s+)?correct\b", re.IGNORECASE),
    re.compile(r"\bdoes\s+not\s+(?:align|apply|relate|match)\b", re.IGNORECASE),
]


def parse_option_sections(explanation: str) -> dict[str, Optional[str]]:
    """
    Parse explanation text into per-option status dicts.

    Splits the explanation on option-boundary markers and evaluates each
    segment for correctness/incorrectness keywords.

    Returns {"A": "correct"/"incorrect"/None, "B": ..., "C": ...}.
    Handles PDF text-extraction artifacts (e.g., "Option Bis correct").
    """
    normed = _normalize_explanation(explanation)

    # Find all option boundary positions (letter + start index in normed)
    boundaries: list[tuple[str, int]] = []
    for m in _OPT_SECTION_RE.finditer(normed):
        boundaries.append((m.group(1).upper(), m.start()))

    if not boundaries:
        return {}

    # Slice the explanation into per-option segments
    segments: dict[str, str] = {}
    for i, (letter, start) in enumerate(boundaries):
        end = boundaries[i + 1][1] if i + 1 < len(boundaries) else len(normed)
        segments[letter] = normed[start:end]

    result: dict[str, Optional[str]] = {}
    for letter, seg in segments.items():
        is_correct = any(p.search(seg) for p in _CORRECT_MARKERS)
        is_incorrect = any(p.search(seg) for p in _INCORRECT_MARKERS)

        if is_correct and not is_incorrect:
            result[letter] = "correct"
        elif is_incorrect and not is_correct:
            result[letter] = "incorrect"
        else:
            result[letter] = None

    return result


def _validate_option_statuses(statuses: dict[str, Optional[str]]) -> tuple[bool, str]:
    """
    Returns (is_valid, correct_letter).
    Valid = exactly one option is "correct" and no other is also "correct".
    """
    correct_options = [k for k, v in statuses.items() if v == "correct"]
    if len(correct_options) == 1:
        return True, correct_options[0]
    return False, ""


def extract_correct_answer(text: str) -> str:
    # First try the structured option-section parser
    statuses = parse_option_sections(text)
    if statuses:
        is_valid, letter = _validate_option_statuses(statuses)
        if is_valid:
            return letter

    # Fall back to regex pattern matching
    for pat in ANSWER_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).upper()
    return ""


def infer_answer_from_options(explanation: str, options: dict[str, str]) -> str:
    stopwords = {"that", "this", "with", "from", "have", "been", "will", "when",
                 "they", "their", "which", "each", "then", "what", "also", "your"}

    def score_options(snippet: str) -> tuple[str, int]:
        best_letter, best_score = "", 0
        for letter, opt_text in options.items():
            words = [w for w in re.findall(r"[a-z]{4,}", opt_text.lower()) if w not in stopwords]
            if not words:
                continue
            score = sum(1 for w in words if w in snippet)
            if score > best_score:
                best_score, best_letter = score, letter
        return best_letter, best_score

    exp_lower = explanation.lower()
    for letter, opt_text in options.items():
        phrase = re.sub(r"\s+", " ", opt_text.lower().strip())
        if len(phrase) >= 4 and phrase in exp_lower:
            return letter

    letter, score = score_options(exp_lower[:500])
    if score >= 2:
        return letter

    letter, score = score_options(exp_lower)
    if score >= 2:
        return letter

    best_letter, best_score = "", 0
    for letter, opt_text in options.items():
        words = [w for w in re.findall(r"[a-z]{4,}", opt_text.lower()) if w not in stopwords]
        if len(words) == 1 and words[0] in exp_lower:
            if 1 > best_score:
                best_score, best_letter = 1, letter
    return best_letter if best_score >= 1 else ""


def parse_options_and_question(body: str) -> tuple[str, dict[str, str]]:
    lines = clean_lines(body)
    question_parts: list[str] = []
    options: dict[str, str] = {}
    current_letter: str | None = None

    for line in lines:
        if re.match(r"^Choose \d+ option", line, re.IGNORECASE):
            continue
        m = OPTION_LINE_RE.match(line)
        if m:
            current_letter = m.group(1).upper()
            options[current_letter] = m.group(2).strip()
        elif current_letter:
            options[current_letter] += " " + line
        else:
            question_parts.append(line)

    return " ".join(question_parts).strip(), options


def parse_questions(full_text: str, source_file: str) -> tuple[list[dict], list[dict]]:
    """
    Returns (valid_questions, unresolved_questions).
    Unresolved questions are those where answer extraction fails validation.
    """
    segments = NEW_Q_RE.split(full_text)
    raw_blocks: list[tuple[int, str]] = []
    i = 1
    while i + 1 < len(segments):
        raw_blocks.append((int(segments[i]), segments[i + 1]))
        i += 2

    questions: list[dict] = []
    unresolved: list[dict] = []

    for idx, (q_num, block) in enumerate(raw_blocks):
        divider_match = DIVIDER_RE.search(block)
        if not divider_match:
            print(f"  [WARN] Q{q_num}: no answer divider found, skipping", file=sys.stderr)
            continue

        body = block[: divider_match.start()]
        explanation_raw = block[divider_match.end():]
        explanation_text = " ".join(clean_lines(explanation_raw))

        # Attempt structured option-section extraction first
        explicit_answer = divider_match.group(1)
        option_statuses: dict[str, Optional[str]] = {}
        is_valid_extraction = False
        correct = ""

        # Always attempt structured extraction from explanation text first —
        # the LEAVE A REPLY divider letter can be wrong in source data.
        option_statuses = parse_option_sections(explanation_text)
        if option_statuses:
            is_valid_extraction, correct = _validate_option_statuses(option_statuses)

        # If structured extraction succeeded, we're done.
        # Otherwise fall back to: explicit divider letter > regex patterns.
        if not correct:
            if explicit_answer:
                correct = explicit_answer.upper()
                is_valid_extraction = True
            else:
                for pat in ANSWER_PATTERNS:
                    m = pat.search(explanation_text)
                    if m:
                        correct = m.group(1).upper()
                        is_valid_extraction = True
                        break

        question_text, options = parse_options_and_question(body)

        if not correct and options:
            correct = infer_answer_from_options(explanation_text, options)
            if correct:
                is_valid_extraction = True

        if not question_text and idx > 0:
            _, prev_block = raw_blocks[idx - 1]
            prev_divider = DIVIDER_RE.search(prev_block)
            prev_tail = prev_block[prev_divider.end():] if prev_divider else prev_block
            prev_lines = clean_lines(prev_tail)
            recovered: list[str] = []
            for line in reversed(prev_lines):
                if OPTION_LINE_RE.match(line):
                    break
                if re.match(r"^(Reference|Why Option|The correct|Option [A-F])", line, re.IGNORECASE):
                    break
                if NEW_Q_RE.search(line):
                    break
                recovered.insert(0, line)
            candidate = " ".join(recovered).strip()
            if candidate and (
                len(candidate) > 20
                or candidate.endswith("?")
                or re.search(r"\bwhich\b|\bwhat\b|\bhow\b|\bwhy\b", candidate, re.IGNORECASE)
            ):
                question_text = candidate

        explanation = " ".join(clean_lines(explanation_raw)).strip()

        # Validate: exactly one correct option; flag multi-correct as unresolved
        if option_statuses:
            correct_options = [k for k, v in option_statuses.items() if v == "correct"]
            if len(correct_options) > 1:
                is_valid_extraction = False
                print(
                    f"  [WARN] Q{q_num}: multiple correct options found {correct_options}, quarantining",
                    file=sys.stderr,
                )

        record = {
            "id": q_num,
            "question": question_text,
            "options": options,
            "correctAnswer": correct,
            "explanation": explanation,
            "sourceFile": source_file,
        }
        if option_statuses:
            record["parsedOptions"] = {k: v for k, v in option_statuses.items()}

        if not correct or not is_valid_extraction:
            reason = "no_correct_found" if not correct else "ambiguous_extraction"
            print(f"  [WARN] Q{q_num}: quarantining ({reason})", file=sys.stderr)
            record["quarantine_reason"] = reason
            unresolved.append(record)
        else:
            questions.append(record)

        if not question_text:
            print(f"  [WARN] Q{q_num}: empty question text", file=sys.stderr)

    return questions, unresolved


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Batch-parse multiple exam PDFs into JSON.")
    parser.add_argument("--input", default="./pdfs/", help="Directory of input PDF files")
    parser.add_argument("--output", default="./parsed_raw/", help="Directory for output JSON files")
    parser.add_argument(
        "--unresolved",
        default="./unresolved_questions.json",
        help="Output file for quarantined questions requiring manual review",
    )
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    unresolved_path = Path(args.unresolved)

    if not input_dir.exists():
        sys.exit(f"Input directory not found: {input_dir}\nCreate it and place PDF files inside.")

    pdfs = sorted(input_dir.glob("*.pdf"))
    if not pdfs:
        sys.exit(f"No PDF files found in {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    total_questions = 0
    all_unresolved: list[dict] = []

    for pdf_path in pdfs:
        print(f"\nProcessing: {pdf_path.name}")
        full_text = extract_full_text(pdf_path)
        print(f"  Extracted {len(full_text):,} characters")

        questions, unresolved = parse_questions(full_text, source_file=pdf_path.name)
        print(f"  Parsed {len(questions)} valid questions, {len(unresolved)} quarantined")

        out_file = output_dir / (pdf_path.stem + ".json")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(questions, f, indent=2, ensure_ascii=False)
        print(f"  Written to: {out_file}")

        total_questions += len(questions)
        all_unresolved.extend(unresolved)

    with open(unresolved_path, "w", encoding="utf-8") as f:
        json.dump(all_unresolved, f, indent=2, ensure_ascii=False)

    print(f"\nBatch complete: {len(pdfs)} PDF(s) → {total_questions} valid questions in {output_dir}")
    if all_unresolved:
        print(f"  {len(all_unresolved)} quarantined questions written to: {unresolved_path}")


if __name__ == "__main__":
    main()
