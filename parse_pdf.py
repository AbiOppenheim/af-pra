#!/usr/bin/env python3
"""
Stage 1: PDF Parser
Extracts all MCQs from the Agentforce Specialist PDF and writes questions.json.

Usage:
    python3 parse_pdf.py
    python3 parse_pdf.py --pdf /path/to/custom.pdf --out questions.json
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


DEFAULT_PDF = Path.home() / "Downloads/Agentforce notes/Salesforce.Agentforce-Specialist.v2026-08-19.q164.pdf"
DEFAULT_OUT = Path(__file__).parent / "questions.json"


def extract_full_text(pdf_path: str) -> str:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n".join(pages)


NEW_Q_RE = re.compile(r"NEW QUESTION:\s*(\d+)", re.IGNORECASE)

# Matches "A. ..." or "A) ..."
OPTION_LINE_RE = re.compile(r"^([A-F])[.)]\s+(.+)$")

# Two divider formats:
#   "Answer: (SHOW ANSWER)"  — answer is in explanation
#   "Answer: B (LEAVE A REPLY)" — answer letter is explicit
DIVIDER_RE = re.compile(
    r"Answer:\s*\(SHOW ANSWER\)|Answer:\s*([A-F])\s*\(LEAVE A REPLY\)",
    re.IGNORECASE,
)

# Lines to discard (ads, promo content)
NOISE_RE = re.compile(
    r"(edudump\.com|freecram\.net|exam dumps|dumps with test engine|"
    r"special discount code|q&as dumps|35%off|https?://www\.|# of views|"
    r"# of questions|certification provider|exam code|exam name|free question|version:)",
    re.IGNORECASE,
)

# Ordered list of patterns to extract correct answer letter from explanation text.
# Tried in order; first match wins.
ANSWER_PATTERNS = [
    # "The correct answer is C" / "correct answers are A and B"
    re.compile(r"[Tt]he correct answer(?:s)? (?:is|are)\s+([A-F])\b"),
    # "option C is correct" (case-insensitive "option")
    re.compile(r"\boption ([A-F]) is (?:the )?correct", re.IGNORECASE),
    # "Why Option A is Correct:"
    re.compile(r"Why Option ([A-F]) is [Cc]orrect", re.IGNORECASE),
    # "Therefore, Option C" / "Thus, Option A" / "Hence, Option B"
    re.compile(r"(?:Therefore|Thus|Hence)[,\s]+(?:the\s+)?(?:correct\s+)?[Oo]ption ([A-F])", re.IGNORECASE),
    # "answer is Option C"
    re.compile(r"answer is [Oo]ption ([A-F])\b", re.IGNORECASE),
    # "C is the correct answer"
    re.compile(r"([A-F]) is the correct answer", re.IGNORECASE),
    # "correct answer is C" (no "The" prefix)
    re.compile(r"correct answer[^.]*?is\s+([A-F])\b", re.IGNORECASE),
    # "(Option A)" in parens
    re.compile(r"\([Oo]ption ([A-F])\)", re.IGNORECASE),
    # "the correct action is to ... (C)" — letter in parens after description
    re.compile(r"correct (?:action|approach|answer|option) is[^.]{0,200}\(([A-F])\)", re.IGNORECASE),
    # "the answer is C" standalone
    re.compile(r"the answer is ([A-F])\b", re.IGNORECASE),
    # "Answer: C" at start of explanation (already handled via divider, fallback)
    re.compile(r"^Answer:\s*([A-F])\b", re.IGNORECASE | re.MULTILINE),
]


_OPT_SECTION_RE = re.compile(r"(?:[-*]\s*)?[Oo]ption\s*([A-F])\s*:?\s*")

_CORRECT_MARKERS = [
    re.compile(r"\bThis\s+is\s+the\s+correct\s+answer\b", re.IGNORECASE),
    re.compile(r"\bThis\s+is\s+correct\b", re.IGNORECASE),
    re.compile(r"\bis\s+the\s+correct\s+answer\b", re.IGNORECASE),
    re.compile(r"\bcorrect\s+answer\b", re.IGNORECASE),
    re.compile(r"\bThis\s+option\s+is\s+correct\b", re.IGNORECASE),
    re.compile(r"\bOption\s*[A-F]\s*is\s+(?:the\s+)?correct\b", re.IGNORECASE),
    re.compile(r"\bis\s+correct\b", re.IGNORECASE),
]

_INCORRECT_MARKERS = [
    re.compile(r"\bis\s+incorrect\b", re.IGNORECASE),
    re.compile(r"\bThis\s+option\s+is\s+incorrect\b", re.IGNORECASE),
    re.compile(r"\bincorrect\s+because\b", re.IGNORECASE),
    re.compile(r"\bnot\s+(?:the\s+)?correct\b", re.IGNORECASE),
    re.compile(r"\bdoes\s+not\s+(?:align|apply|relate|match)\b", re.IGNORECASE),
]


def parse_option_sections(explanation: str) -> dict[str, Optional[str]]:
    normed = re.sub(r"\s+", " ", explanation).strip()
    boundaries: list[tuple[str, int]] = []
    for m in _OPT_SECTION_RE.finditer(normed):
        boundaries.append((m.group(1).upper(), m.start()))
    if not boundaries:
        return {}
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
    correct_options = [k for k, v in statuses.items() if v == "correct"]
    if len(correct_options) == 1:
        return True, correct_options[0]
    return False, ""


def extract_correct_answer(text: str) -> str:
    # Try structured option-section extraction first
    statuses = parse_option_sections(text)
    if statuses:
        is_valid, letter = _validate_option_statuses(statuses)
        if is_valid:
            return letter
    for pat in ANSWER_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).upper()
    return ""


def infer_answer_from_options(explanation: str, options: dict[str, str]) -> str:
    """
    Fallback: find which option's text is most prominent in the explanation.
    Searches the first 500 chars first (fast path), then the full text (slow path).
    Returns letter with highest score, or "" if none scores >= 2.
    """
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

    # Direct phrase match: if option text (normalised) appears verbatim in explanation
    exp_lower = explanation.lower()
    for letter, opt_text in options.items():
        phrase = re.sub(r"\s+", " ", opt_text.lower().strip())
        if len(phrase) >= 4 and phrase in exp_lower:
            return letter

    # Fast path: first 500 chars, threshold >= 2
    letter, score = score_options(exp_lower[:500])
    if score >= 2:
        return letter

    # Slow path: full explanation
    letter, score = score_options(exp_lower)
    if score >= 2:
        return letter

    # Single-word fallback: when option text has only 1 significant word, 1 match is enough
    best_letter, best_score = "", 0
    for letter, opt_text in options.items():
        words = [w for w in re.findall(r"[a-z]{4,}", opt_text.lower()) if w not in stopwords]
        if len(words) == 1 and words[0] in exp_lower:
            score = 1
            if score > best_score:
                best_score, best_letter = score, letter
    return best_letter if best_score >= 1 else ""


def clean_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not NOISE_RE.search(line)
    ]


def parse_options_and_question(body: str) -> tuple[str, dict[str, str]]:
    """
    Given the body text before the answer divider, separate the question text
    from the answer options. Returns (question_text, options_dict).
    """
    lines = clean_lines(body)
    question_parts: list[str] = []
    options: dict[str, str] = {}
    current_letter: str | None = None

    for line in lines:
        # Skip "Choose 1 option." / "Choose 2 options." noise
        if re.match(r"^Choose \d+ option", line, re.IGNORECASE):
            continue
        m = OPTION_LINE_RE.match(line)
        if m:
            current_letter = m.group(1).upper()
            options[current_letter] = m.group(2).strip()
        elif current_letter:
            # Continuation of the current option
            options[current_letter] += " " + line
        else:
            question_parts.append(line)

    question_text = " ".join(question_parts).strip()
    return question_text, options


def parse_questions(full_text: str) -> list[dict]:
    # Split on NEW QUESTION markers
    segments = NEW_Q_RE.split(full_text)
    # segments layout: [pre_text, num1, block1, num2, block2, ...]

    raw_blocks: list[tuple[int, str]] = []
    i = 1
    while i + 1 < len(segments):
        raw_blocks.append((int(segments[i]), segments[i + 1]))
        i += 2

    questions: list[dict] = []

    for idx, (q_num, block) in enumerate(raw_blocks):
        divider_match = DIVIDER_RE.search(block)
        if not divider_match:
            print(f"  [WARN] Q{q_num}: no answer divider found, skipping", file=sys.stderr)
            continue

        body = block[: divider_match.start()]
        explanation_raw = block[divider_match.end():]

        # Determine correct answer — prefer explanation-based extraction over
        # the LEAVE A REPLY divider letter, which can be wrong in source data.
        explicit_answer = divider_match.group(1)  # None for (SHOW ANSWER)
        explanation_lines = clean_lines(explanation_raw)
        explanation_text = " ".join(explanation_lines)
        correct = extract_correct_answer(explanation_text)
        if not correct and explicit_answer:
            correct = explicit_answer.upper()

        # Parse question + options from body
        question_text, options = parse_options_and_question(body)

        # If no correct answer yet, try matching option text content against explanation
        if not correct and options:
            correct = infer_answer_from_options(explanation_text, options)

        # If question text is empty, try to recover it from the PREVIOUS block's tail
        # (happens when the question text ends a page before the options start on the next)
        if not question_text and idx > 0:
            _, prev_block = raw_blocks[idx - 1]
            prev_divider = DIVIDER_RE.search(prev_block)
            if prev_divider:
                prev_explanation = prev_block[prev_divider.end():]
            else:
                prev_explanation = prev_block

            # The question text for this question appears at the END of prev_explanation,
            # right before our question's options block.
            # Strategy: take the last few non-noise lines of prev_explanation that look like
            # a question (don't start with an option letter, not a reference/answer line).
            prev_lines = clean_lines(prev_explanation)
            recovered: list[str] = []
            for line in reversed(prev_lines):
                if OPTION_LINE_RE.match(line):
                    break
                if re.match(r"^(Reference|Why Option|The correct|Option [A-F])", line, re.IGNORECASE):
                    break
                if NEW_Q_RE.search(line):
                    break
                recovered.insert(0, line)

            # Only use recovered text if it looks like a question (has words, ends with ?)
            candidate = " ".join(recovered).strip()
            if candidate and (
                len(candidate) > 20
                or candidate.endswith("?")
                or re.search(r"\bwhich\b|\bwhat\b|\bhow\b|\bwhy\b", candidate, re.IGNORECASE)
            ):
                question_text = candidate

        # Clean explanation (remove noise)
        explanation_lines = clean_lines(explanation_raw)
        explanation = " ".join(explanation_lines).strip()

        if not correct:
            print(f"  [WARN] Q{q_num}: could not determine correct answer", file=sys.stderr)
        if not question_text:
            print(f"  [WARN] Q{q_num}: empty question text", file=sys.stderr)
        if not options:
            print(f"  [WARN] Q{q_num}: no options parsed", file=sys.stderr)

        questions.append(
            {
                "id": q_num,
                "question": question_text,
                "options": options,
                "correctAnswer": correct,
                "explanation": explanation,
            }
        )

    return questions


def main():
    parser = argparse.ArgumentParser(description="Extract MCQs from a Salesforce exam PDF.")
    parser.add_argument("--pdf", default=str(DEFAULT_PDF), help="Path to PDF file")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output JSON file path")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    out_path = Path(args.out)

    if not pdf_path.exists():
        sys.exit(f"PDF not found: {pdf_path}")

    print(f"Extracting text from: {pdf_path}")
    full_text = extract_full_text(str(pdf_path))
    print(f"  Extracted {len(full_text):,} characters")

    print("Parsing questions...")
    questions = parse_questions(full_text)
    print(f"  Parsed {len(questions)} questions")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(questions, f, indent=2, ensure_ascii=False)

    print(f"Written to: {out_path}")

    missing_answer = [q for q in questions if not q["correctAnswer"]]
    missing_options = [q for q in questions if not q["options"]]
    empty_question = [q for q in questions if not q["question"]]

    if missing_answer:
        print(f"  [WARN] {len(missing_answer)} questions with no parsed correct answer: ids={[q['id'] for q in missing_answer]}")
    if missing_options:
        print(f"  [WARN] {len(missing_options)} questions with no options")
    if empty_question:
        print(f"  [WARN] {len(empty_question)} questions with empty text: ids={[q['id'] for q in empty_question]}")

    print("Done.")


if __name__ == "__main__":
    main()
