"""
Deterministic Writing Style Evaluator — Metrics 1-9
====================================================
Scores a single writing sample on binary/count and structural metrics.
Pure Python, no API calls, fully deterministic.

Usage:
    python3 tools/eval_deterministic.py --sample-path .tmp/samples/baseline/sample_0.txt
    python3 tools/eval_deterministic.py --sample-path sample.txt --output-path eval.json
"""

import argparse
import json
import re
import statistics
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BANNED_WORDS = [
    "genuinely", "extraordinary", "significantly", "straightforward",
    "delve", "paradigm", "pivotal", "groundbreaking",
    "cutting-edge", "cutting edge", "seamless", "seamlessly",
    "utilise", "utilised", "utilises", "utilising",
    "synergy", "synergise", "synergised",
    "holistic", "ecosystem",
    "it's worth noting", "it's important to note",
    "in conclusion", "to summarise",
    "landscape",
]

# These only count when they open a sentence
BANNED_OPENERS = ["moreover", "furthermore", "additionally"]

# US spelling -> AU spelling pairs
US_AU_PAIRS = {
    "organization": "organisation", "organizations": "organisations",
    "organize": "organise", "organized": "organised", "organizing": "organising",
    "color": "colour", "colors": "colours", "colored": "coloured",
    "favor": "favour", "favors": "favours", "favorable": "favourable",
    "honor": "honour", "honors": "honours", "honored": "honoured",
    "humor": "humour",
    "labor": "labour", "labors": "labours",
    "behavior": "behaviour", "behaviors": "behaviours",
    "neighbor": "neighbour", "neighbors": "neighbours",
    "defense": "defence",
    "offense": "offence",
    "license": "licence",  # noun form
    "analyze": "analyse", "analyzed": "analysed", "analyzing": "analysing",
    "recognize": "recognise", "recognized": "recognised", "recognizing": "recognising",
    "customize": "customise", "customized": "customised",
    "optimize": "optimise", "optimized": "optimised", "optimizing": "optimising",
    "maximize": "maximise", "minimize": "minimise",
    "center": "centre", "centers": "centres",
    "fiber": "fibre",
    "catalog": "catalogue", "catalogs": "catalogues",
    "dialog": "dialogue", "dialogs": "dialogues",
    "program": "programme",  # non-computing context
}

# Uncontracted forms to check against contractions
CONTRACTION_PAIRS = [
    ("it is", "it's"),
    ("it has", "it's"),
    ("that is", "that's"),
    ("there is", "there's"),
    ("here is", "here's"),
    ("what is", "what's"),
    ("who is", "who's"),
    ("he is", "he's"),
    ("she is", "she's"),
    ("do not", "don't"),
    ("does not", "doesn't"),
    ("did not", "didn't"),
    ("is not", "isn't"),
    ("are not", "aren't"),
    ("was not", "wasn't"),
    ("were not", "weren't"),
    ("will not", "won't"),
    ("would not", "wouldn't"),
    ("could not", "couldn't"),
    ("should not", "shouldn't"),
    ("can not", "can't"),
    ("cannot", "can't"),
    ("have not", "haven't"),
    ("has not", "hasn't"),
    ("had not", "hadn't"),
    ("I am", "I'm"),
    ("I have", "I've"),
    ("I will", "I'll"),
    ("I would", "I'd"),
    ("we are", "we're"),
    ("we have", "we've"),
    ("we will", "we'll"),
    ("they are", "they're"),
    ("they have", "they've"),
    ("they will", "they'll"),
    ("you are", "you're"),
    ("you have", "you've"),
    ("you will", "you'll"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def split_sentences(text):
    """Split text into sentences. Handles common abbreviations."""
    # Replace common abbreviations to avoid false splits
    cleaned = text
    for abbr in ["Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "Sr.", "Jr.", "vs.", "etc.", "e.g.", "i.e."]:
        cleaned = cleaned.replace(abbr, abbr.replace(".", "<DOT>"))

    # Split on sentence-ending punctuation followed by space or end
    parts = re.split(r'(?<=[.!?])\s+', cleaned.strip())

    # Restore abbreviations and filter empty
    sentences = []
    for p in parts:
        p = p.replace("<DOT>", ".").strip()
        if p:
            sentences.append(p)
    return sentences


def word_count(text: str) -> int:
    return len(text.split())


# ---------------------------------------------------------------------------
# Metric functions
# ---------------------------------------------------------------------------

def metric_banned_words(text: str) -> dict:
    """Metric 1: Banned word occurrences per 500 words."""
    text_lower = text.lower()
    wc = word_count(text)
    total_hits = 0
    found = []

    # Check always-banned words/phrases
    for word in BANNED_WORDS:
        count = len(re.findall(re.escape(word), text_lower))
        if count > 0:
            total_hits += count
            found.append({"word": word, "count": count})

    # Check sentence-opener-only banned words
    sentences = split_sentences(text)
    for sentence in sentences:
        first_word = sentence.strip().split()[0].lower().rstrip(".,;:") if sentence.strip() else ""
        if first_word in BANNED_OPENERS:
            total_hits += 1
            found.append({"word": first_word + " (opener)", "count": 1})

    per_500 = (total_hits / max(wc, 1)) * 500
    score = max(0.0, 1.0 - 0.25 * per_500)

    return {
        "metric": "banned_words",
        "raw_count": total_hits,
        "per_500_words": round(per_500, 3),
        "word_count": wc,
        "score": round(score, 4),
        "found": found,
    }


def metric_em_dashes(text: str) -> dict:
    """Metric 2: Em dash count (target: 0)."""
    em_dash_count = text.count("\u2014")  # —
    # Also count double hyphens used as em dashes (but not single hyphens)
    double_hyphen_count = len(re.findall(r'(?<!\-)\-\-(?!\-)', text))
    total = em_dash_count + double_hyphen_count

    score = 1.0 if total == 0 else max(0.0, 1.0 - 0.2 * total)

    return {
        "metric": "em_dashes",
        "raw_count": total,
        "em_dash_chars": em_dash_count,
        "double_hyphens": double_hyphen_count,
        "score": round(score, 4),
    }


def metric_contraction_ratio(text: str) -> dict:
    """Metric 3: Uncontracted vs contracted form ratio."""
    text_lower = text.lower()
    uncontracted_total = 0
    contracted_total = 0

    for expanded, contracted in CONTRACTION_PAIRS:
        exp_count = len(re.findall(r'\b' + re.escape(expanded) + r'\b', text_lower))
        con_count = len(re.findall(re.escape(contracted), text_lower))
        uncontracted_total += exp_count
        contracted_total += con_count

    total = uncontracted_total + contracted_total
    if total == 0:
        ratio = 0.0
    else:
        ratio = uncontracted_total / total

    score = round(1.0 - ratio, 4)

    return {
        "metric": "contraction_ratio",
        "uncontracted_count": uncontracted_total,
        "contracted_count": contracted_total,
        "ratio_uncontracted": round(ratio, 4),
        "score": score,
    }


def metric_not_x_is_y(text: str) -> dict:
    """Metric 4: 'This is not X, it is Y' pattern hits."""
    patterns = [
        r'[Tt]his is not .{1,60},?\s*it is .{1,60}',
        r'[Tt]his isn\'t .{1,60},?\s*it\'s .{1,60}',
        r'[Tt]hat is not .{1,60},?\s*it is .{1,60}',
        r'[Tt]hat isn\'t .{1,60},?\s*it\'s .{1,60}',
        r'[Ii]t is not .{1,60},?\s*it is .{1,60}',
        r'[Ii]t isn\'t .{1,60},?\s*it\'s .{1,60}',
        r'[Tt]his is not about .{1,60},?\s*it\'?s? about .{1,60}',
    ]

    total_hits = 0
    matches = []
    for pattern in patterns:
        found = re.findall(pattern, text)
        total_hits += len(found)
        matches.extend(found)

    score = 1.0 if total_hits == 0 else max(0.0, 1.0 - 0.5 * total_hits)

    return {
        "metric": "not_x_is_y",
        "raw_count": total_hits,
        "matches": matches[:5],  # cap for readability
        "score": round(score, 4),
    }


def metric_au_spelling(text: str) -> dict:
    """Metric 5: Australian spelling violations."""
    violations = []

    for us_spelling, au_spelling in US_AU_PAIRS.items():
        # Word boundary match, case-insensitive
        pattern = r'\b' + re.escape(us_spelling) + r'\b'
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            # Make sure we're not matching the AU spelling itself
            # (e.g., "programme" shouldn't match "program" pattern)
            au_pattern = r'\b' + re.escape(au_spelling) + r'\b'
            # Filter out cases where the match is actually the AU form
            for m in matches:
                if not re.match(au_pattern, m, re.IGNORECASE):
                    violations.append({"us": us_spelling, "au": au_spelling, "found": m})

    score = max(0.0, 1.0 - 0.2 * len(violations))

    return {
        "metric": "au_spelling",
        "violation_count": len(violations),
        "violations": violations[:10],  # cap for readability
        "score": round(score, 4),
    }


def metric_mean_sentence_length(text: str) -> dict:
    """Metric 6: Mean sentence length in words (target: under 18-20)."""
    sentences = split_sentences(text)
    if not sentences:
        return {"metric": "mean_sent_len", "mean": 0, "sentence_count": 0, "score": 1.0}

    lengths = [word_count(s) for s in sentences]
    mean = statistics.mean(lengths)

    if mean <= 18:
        score = 1.0
    elif mean >= 30:
        score = 0.0
    else:
        score = 1.0 - (mean - 18) / (30 - 18)

    return {
        "metric": "mean_sent_len",
        "mean": round(mean, 2),
        "sentence_count": len(sentences),
        "min_length": min(lengths),
        "max_length": max(lengths),
        "score": round(score, 4),
    }


def metric_sentence_length_variance(text: str) -> dict:
    """Metric 7: Sentence length variance (higher = better rhythm variation)."""
    sentences = split_sentences(text)
    if len(sentences) < 2:
        return {"metric": "sent_len_var", "stdev": 0, "score": 0.0}

    lengths = [word_count(s) for s in sentences]
    stdev = statistics.stdev(lengths)

    score = min(1.0, stdev / 8.0)

    return {
        "metric": "sent_len_var",
        "stdev": round(stdev, 2),
        "score": round(score, 4),
    }


def metric_opener_variety(text: str) -> dict:
    """Metric 8: Sentence-opener variety (unique first words / total sentences)."""
    sentences = split_sentences(text)
    if not sentences:
        return {"metric": "opener_variety", "ratio": 0, "score": 0.0}

    openers = []
    for s in sentences:
        words = s.strip().split()
        if words:
            openers.append(words[0].lower().strip("\"'("))

    unique = len(set(openers))
    total = len(openers)
    ratio = unique / total if total > 0 else 0

    return {
        "metric": "opener_variety",
        "unique_openers": unique,
        "total_sentences": total,
        "ratio": round(ratio, 4),
        "score": round(ratio, 4),
    }


def metric_parallel_clusters(text: str) -> dict:
    """Metric 9: Parallel sentence cluster detection (3+ same-opener in a row)."""
    sentences = split_sentences(text)
    if len(sentences) < 3:
        return {"metric": "parallel_clusters", "cluster_count": 0, "score": 1.0}

    openers = []
    for s in sentences:
        words = s.strip().split()
        if words:
            openers.append(words[0].lower().strip("\"'("))
        else:
            openers.append("")

    cluster_count = 0
    i = 0
    while i <= len(openers) - 3:
        if openers[i] == openers[i + 1] == openers[i + 2] and openers[i] != "":
            cluster_count += 1
            # Skip past this cluster
            j = i + 3
            while j < len(openers) and openers[j] == openers[i]:
                j += 1
            i = j
        else:
            i += 1

    score = max(0.0, 1.0 - 0.33 * cluster_count)

    return {
        "metric": "parallel_clusters",
        "cluster_count": cluster_count,
        "score": round(score, 4),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def evaluate_sample(text: str) -> dict:
    """Run all 9 deterministic metrics on a text sample."""
    return {
        "banned_words": metric_banned_words(text),
        "em_dashes": metric_em_dashes(text),
        "contraction_ratio": metric_contraction_ratio(text),
        "not_x_is_y": metric_not_x_is_y(text),
        "au_spelling": metric_au_spelling(text),
        "mean_sent_len": metric_mean_sentence_length(text),
        "sent_len_var": metric_sentence_length_variance(text),
        "opener_variety": metric_opener_variety(text),
        "parallel_clusters": metric_parallel_clusters(text),
    }


def main():
    parser = argparse.ArgumentParser(description="Deterministic writing style evaluator (metrics 1-9)")
    parser.add_argument("--sample-path", required=True, help="Path to the text sample file")
    parser.add_argument("--output-path", help="Path to write JSON output (default: stdout)")
    args = parser.parse_args()

    sample_path = Path(args.sample_path)
    if not sample_path.exists():
        print(f"Error: Sample file not found: {sample_path}", file=sys.stderr)
        sys.exit(1)

    text = sample_path.read_text(encoding="utf-8")
    results = evaluate_sample(text)

    output = json.dumps(results, indent=2)

    if args.output_path:
        Path(args.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_path).write_text(output, encoding="utf-8")
        print(f"Wrote evaluation to {args.output_path}")
    else:
        print(output)


if __name__ == "__main__":
    main()
