#!/usr/bin/env python3
"""
Stage 1: Batch PDF Parser
Processes all PDFs in ./pdfs/ and writes individual JSON files to ./parsed_raw/.

Usage:
    python3 batch_parse.py
    python3 batch_parse.py --input ./pdfs/ --output ./parsed_raw/
"""

import argparse
import json
import re
import sys
from pathlib import Path

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


def extract_correct_answer(text: str) -> str:
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


def parse_questions(full_text: str, source_file: str) -> list[dict]:
    segments = NEW_Q_RE.split(full_text)
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

        explicit_answer = divider_match.group(1)
        if explicit_answer:
            correct = explicit_answer.upper()
        else:
            explanation_text = " ".join(clean_lines(explanation_raw))
            correct = extract_correct_answer(explanation_text)

        question_text, options = parse_options_and_question(body)

        if not correct and options:
            correct = infer_answer_from_options(" ".join(clean_lines(explanation_raw)), options)

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

        if not correct:
            print(f"  [WARN] Q{q_num}: could not determine correct answer", file=sys.stderr)
        if not question_text:
            print(f"  [WARN] Q{q_num}: empty question text", file=sys.stderr)

        questions.append({
            "id": q_num,
            "question": question_text,
            "options": options,
            "correctAnswer": correct,
            "explanation": explanation,
            "sourceFile": source_file,
        })

    return questions


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Batch-parse multiple exam PDFs into JSON.")
    parser.add_argument("--input", default="./pdfs/", help="Directory of input PDF files")
    parser.add_argument("--output", default="./parsed_raw/", help="Directory for output JSON files")
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)

    if not input_dir.exists():
        sys.exit(f"Input directory not found: {input_dir}\nCreate it and place PDF files inside.")

    pdfs = sorted(input_dir.glob("*.pdf"))
    if not pdfs:
        sys.exit(f"No PDF files found in {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    total_questions = 0
    for pdf_path in pdfs:
        print(f"\nProcessing: {pdf_path.name}")
        full_text = extract_full_text(pdf_path)
        print(f"  Extracted {len(full_text):,} characters")

        questions = parse_questions(full_text, source_file=pdf_path.name)
        print(f"  Parsed {len(questions)} questions")

        out_file = output_dir / (pdf_path.stem + ".json")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(questions, f, indent=2, ensure_ascii=False)
        print(f"  Written to: {out_file}")
        total_questions += len(questions)

    print(f"\nBatch complete: {len(pdfs)} PDF(s) → {total_questions} total questions in {output_dir}")


if __name__ == "__main__":
    main()
