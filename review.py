"""
Rocky Review Tool
=====================

A small companion script for walking through recent classifications and
marking each one as correct or incorrect. Reads classifications.jsonl,
lets you grade each entry, and writes the grades back to a separate
file (review.jsonl) so you can track Rocky's accuracy over time.

To run:
    python review.py             # Review unreviewed classifications, oldest first
    python review.py --recent 20 # Just review the 20 most recent
    python review.py --remy-only # Just the ones he flagged as Remy
    python review.py --stats     # Print accuracy statistics, no review
    python review.py --export    # Export wrong classifications as a markdown report

Keyboard during review:
    y         — correct (his classification matched your judgment)
    n         — wrong (he got this one wrong)
    s         — skip (not sure, come back later)
    q         — quit and save progress
    note      — add a freeform note to this entry, then prompt again
    open      — open this email in Outlook (best effort, by message ID)
"""

import argparse
import json
import sys
import textwrap
import webbrowser
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
CLASSIFICATIONS_PATH = ROOT / "classifications.jsonl"
REVIEW_PATH = ROOT / "review.jsonl"
EXPORT_PATH = ROOT / "review_export.md"


# =============================================================================
# Reading and writing the JSONL files
# =============================================================================

def load_classifications() -> list[dict]:
    """Read all classifications from disk."""
    if not CLASSIFICATIONS_PATH.exists():
        print(f"No classifications file found at {CLASSIFICATIONS_PATH}.")
        print("Run rocky.py first to generate some classifications to review.")
        sys.exit(0)

    records = []
    with open(CLASSIFICATIONS_PATH, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"Warning: skipping malformed line {i}: {e}")
    return records


def load_reviews() -> dict:
    """Read existing reviews, keyed by message_id."""
    if not REVIEW_PATH.exists():
        return {}

    reviews = {}
    with open(REVIEW_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                # Last-write-wins if a message has multiple review entries.
                reviews[r["message_id"]] = r
            except json.JSONDecodeError:
                continue
    return reviews


def append_review(review: dict) -> None:
    """Append a single review record to review.jsonl."""
    with open(REVIEW_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(review) + "\n")


# =============================================================================
# Display helpers
# =============================================================================

def clear_screen():
    # Cross-platform-ish; \x1b[2J clears, \x1b[H homes the cursor.
    print("\x1b[2J\x1b[H", end="")


def colored(text: str, color: str) -> str:
    """Wrap text in ANSI color codes."""
    codes = {
        "red": "31",
        "green": "32",
        "yellow": "33",
        "blue": "34",
        "magenta": "35",
        "cyan": "36",
        "white": "37",
        "bold": "1",
        "dim": "2",
    }
    code = codes.get(color, "0")
    return f"\x1b[{code}m{text}\x1b[0m"


def format_classification(c: dict, index: int, total: int) -> str:
    """Render a single classification record for human review."""
    is_remy = c.get("is_remy_request")
    confidence = c.get("confidence", 0)

    # Header line: which one are we looking at, and his decision.
    decision = colored("REMY", "green") if is_remy else colored("not Remy", "dim")
    conf_str = f"{confidence:.2f}"
    if confidence >= 0.8:
        conf_color = "green" if is_remy else "red"
    elif confidence >= 0.5:
        conf_color = "yellow"
    else:
        conf_color = "dim"
    conf_str = colored(conf_str, conf_color)

    header = colored(f"[{index}/{total}] ", "dim") + f"Rocky says: {decision} (confidence {conf_str})"

    # The email itself.
    received = c.get("received_at", "?")
    if received != "?":
        try:
            dt = datetime.fromisoformat(received.replace("Z", "+00:00"))
            received = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass

    sender = f"{c.get('from_name', '?')} <{c.get('from_address', '?')}>"
    subject = c.get("subject") or "(no subject)"

    attachments = c.get("attachment_names", [])
    if attachments:
        att_summary = ", ".join(attachments[:5])
        if len(attachments) > 5:
            att_summary += f" (+{len(attachments) - 5} more)"
    else:
        att_summary = colored("(none)", "dim")

    lines = [
        "",
        colored("─" * 78, "dim"),
        header,
        colored("─" * 78, "dim"),
        "",
        f"  {colored('Received:', 'bold')}    {received}",
        f"  {colored('From:', 'bold')}        {sender}",
        f"  {colored('Subject:', 'bold')}     {subject}",
        f"  {colored('Attachments:', 'bold')} {att_summary}",
        "",
    ]

    if c.get("notice_type_if_known"):
        lines.append(f"  {colored('Notice type:', 'bold')} {c['notice_type_if_known']}")
    if c.get("jurisdiction_if_known"):
        lines.append(f"  {colored('Jurisdiction:', 'bold')} {c['jurisdiction_if_known']}")

    # The reasoning is the most important field for evaluation.
    reasoning = c.get("reasoning", "(no reasoning given)")
    wrapped_reasoning = textwrap.fill(
        reasoning,
        width=72,
        initial_indent="    ",
        subsequent_indent="    ",
    )
    lines.append("")
    lines.append(f"  {colored('Reasoning:', 'bold')}")
    lines.append(colored(wrapped_reasoning, "cyan"))
    lines.append("")

    if "_error" in c:
        lines.append(colored(f"  ⚠ Classifier error: {c['_error']}", "red"))
        lines.append("")

    return "\n".join(lines)


# =============================================================================
# Statistics
# =============================================================================

def print_stats(classifications: list[dict], reviews: dict) -> None:
    """Print accuracy statistics based on completed reviews."""
    total = len(classifications)
    reviewed = sum(1 for c in classifications if c.get("message_id") in reviews)
    pending = total - reviewed

    print()
    print(colored("─" * 60, "dim"))
    print(colored("Rocky classifier accuracy report", "bold"))
    print(colored("─" * 60, "dim"))
    print()
    print(f"  Total classifications: {total}")
    print(f"  Reviewed:              {reviewed}")
    print(f"  Pending review:        {pending}")
    print()

    if reviewed == 0:
        print(colored("  No reviews yet — run without --stats to start grading.", "dim"))
        return

    # Build a confusion matrix.
    # Each row is what Rocky said; each column is what James said.
    tp = fp = tn = fn = 0
    for c in classifications:
        mid = c.get("message_id")
        if mid not in reviews:
            continue
        r = reviews[mid]
        if r.get("verdict") == "skip":
            continue
        rocky_said_remy = c.get("is_remy_request") is True
        james_said_remy = r.get("verdict") == "correct" if rocky_said_remy else r.get("verdict") == "wrong"
        # Reformulate clearly: Rocky was correct iff verdict == "correct".
        # Truth = Rocky's answer XNOR (verdict == "correct")
        # If Rocky said REMY and verdict == "correct" → true positive
        # If Rocky said REMY and verdict == "wrong" → false positive
        # If Rocky said NOT and verdict == "correct" → true negative
        # If Rocky said NOT and verdict == "wrong" → false negative
        if rocky_said_remy and r["verdict"] == "correct":
            tp += 1
        elif rocky_said_remy and r["verdict"] == "wrong":
            fp += 1
        elif not rocky_said_remy and r["verdict"] == "correct":
            tn += 1
        elif not rocky_said_remy and r["verdict"] == "wrong":
            fn += 1

    graded = tp + fp + tn + fn
    if graded == 0:
        print(colored("  No graded reviews yet (all skipped).", "dim"))
        return

    accuracy = (tp + tn) / graded
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0

    print(f"  {colored('Confusion matrix', 'bold')} ({graded} graded reviews):")
    print()
    print(f"                    Actually Remy    Actually not Remy")
    print(f"  Rocky: REMY         {colored(str(tp).rjust(3), 'green')}              {colored(str(fp).rjust(3), 'red')}")
    print(f"  Rocky: not          {colored(str(fn).rjust(3), 'red')}              {colored(str(tn).rjust(3), 'green')}")
    print()
    print(f"  Overall accuracy:  {accuracy:.1%}")
    print(f"  Precision (when he says Remy, how often is he right):  {precision:.1%}")
    print(f"  Recall (of actual Remy requests, how many did he catch): {recall:.1%}")
    print()

    if fn > 0:
        print(colored(f"  ⚠ {fn} missed Remy request(s) — these are the highest-cost errors.", "red"))
    if fp > 0:
        print(colored(f"  ⓘ {fp} false alarm(s) — annoying but lower-cost than misses.", "yellow"))
    print()


# =============================================================================
# Export tool
# =============================================================================

def export_misclassifications(classifications: list[dict], reviews: dict) -> None:
    """Write a markdown report of all classifications marked wrong."""
    wrong = []
    for c in classifications:
        mid = c.get("message_id")
        if mid in reviews and reviews[mid].get("verdict") == "wrong":
            entry = dict(c)
            entry["_review"] = reviews[mid]
            wrong.append(entry)

    if not wrong:
        print("No misclassifications to export.")
        return

    lines = [
        "# Rocky Misclassification Report",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Total misclassifications: {len(wrong)}",
        "",
        "Use this report to identify patterns and update the classifier prompt or `instructions.md`.",
        "",
        "---",
        "",
    ]

    # Group: false positives (he said Remy, wasn't) and false negatives (he missed it).
    false_positives = [c for c in wrong if c.get("is_remy_request")]
    false_negatives = [c for c in wrong if not c.get("is_remy_request")]

    if false_negatives:
        lines.append(f"## False negatives ({len(false_negatives)}) — Remy requests he missed")
        lines.append("")
        lines.append("These are the highest-cost errors. Each one is a Remy request that didn't get flagged.")
        lines.append("")
        for c in false_negatives:
            lines.extend(_format_for_export(c))
        lines.append("")

    if false_positives:
        lines.append(f"## False positives ({len(false_positives)}) — flagged as Remy, weren't")
        lines.append("")
        lines.append("Lower-cost errors but worth reviewing for patterns to refine the classifier.")
        lines.append("")
        for c in false_positives:
            lines.extend(_format_for_export(c))
        lines.append("")

    EXPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(wrong)} misclassifications to {EXPORT_PATH}")


def _format_for_export(c: dict) -> list[str]:
    received = c.get("received_at", "?")
    sender = f"{c.get('from_name', '?')} <{c.get('from_address', '?')}>"
    subject = c.get("subject") or "(no subject)"
    decision = "REMY" if c.get("is_remy_request") else "not Remy"
    confidence = c.get("confidence", 0)

    lines = [
        f"### {subject}",
        "",
        f"- **Received:** {received}",
        f"- **From:** {sender}",
        f"- **Rocky said:** {decision} (confidence {confidence:.2f})",
        f"- **Reasoning:** {c.get('reasoning', '(none)')}",
    ]

    attachments = c.get("attachment_names", [])
    if attachments:
        lines.append(f"- **Attachments:** {', '.join(attachments)}")

    review = c.get("_review", {})
    if review.get("note"):
        lines.append(f"- **Your note:** {review['note']}")

    lines.append("")
    return lines


# =============================================================================
# Interactive review loop
# =============================================================================

def review_loop(classifications: list[dict], reviews: dict, args) -> None:
    """Walk through classifications and let the user grade each one."""
    # Filter: skip already-reviewed unless --redo, optionally filter to Remy-only.
    pending = []
    for c in classifications:
        mid = c.get("message_id")
        if mid in reviews and not args.redo:
            continue
        if args.remy_only and not c.get("is_remy_request"):
            continue
        pending.append(c)

    # If --recent is set, take just the last N.
    if args.recent and args.recent > 0:
        pending = pending[-args.recent:]

    if not pending:
        print()
        print(colored("Nothing to review.", "yellow"))
        if reviews:
            print(colored(f"All {len(classifications)} classifications already reviewed.", "dim"))
            print(colored("(Use --redo to re-review previously-graded items.)", "dim"))
        else:
            print(colored("No classifications found yet — let Rocky run for a while first.", "dim"))
        return

    print()
    print(colored(f"Starting review of {len(pending)} classification(s).", "bold"))
    print(colored("Commands: y=correct  n=wrong  s=skip  note=add note  q=quit", "dim"))
    print()

    graded = {"correct": 0, "wrong": 0, "skip": 0}

    for i, c in enumerate(pending, 1):
        clear_screen()
        print(format_classification(c, i, len(pending)))

        while True:
            print(colored("Your verdict? [y/n/s/note/q]: ", "bold"), end="")
            try:
                choice = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                print(colored("Review interrupted. Progress saved.", "yellow"))
                _print_session_summary(graded)
                return

            if choice in ("y", "yes"):
                _save_verdict(c, "correct", reviews)
                graded["correct"] += 1
                break
            elif choice in ("n", "no", "wrong"):
                note = input(colored("  Why was he wrong? (optional): ", "dim")).strip()
                _save_verdict(c, "wrong", reviews, note=note)
                graded["wrong"] += 1
                break
            elif choice in ("s", "skip"):
                _save_verdict(c, "skip", reviews)
                graded["skip"] += 1
                break
            elif choice == "note":
                note = input(colored("  Note: ", "dim")).strip()
                if note:
                    # Save the note but don't change verdict; re-prompt for verdict.
                    c["_pending_note"] = note
                    print(colored("  Note attached. Now grade it:", "dim"))
                continue
            elif choice in ("q", "quit", "exit"):
                print()
                print(colored("Review session ended.", "yellow"))
                _print_session_summary(graded)
                return
            else:
                print(colored("  ? Try y, n, s, note, or q.", "dim"))

    print()
    print(colored("All caught up.", "green"))
    _print_session_summary(graded)


def _save_verdict(classification: dict, verdict: str, reviews: dict, note: str = "") -> None:
    """Save a verdict to review.jsonl and update the in-memory reviews dict."""
    # If there's a pending note from the "note" command, fold it in.
    pending_note = classification.pop("_pending_note", "")
    combined_note = "; ".join(filter(None, [pending_note, note]))

    review = {
        "message_id": classification.get("message_id"),
        "subject": classification.get("subject"),
        "verdict": verdict,
        "rocky_said_remy": classification.get("is_remy_request"),
        "rocky_confidence": classification.get("confidence"),
        "reviewed_at": datetime.now().isoformat(timespec="seconds"),
        "note": combined_note,
    }
    append_review(review)
    reviews[review["message_id"]] = review


def _print_session_summary(graded: dict) -> None:
    """Print a short summary of what was graded in this session."""
    total = sum(graded.values())
    if total == 0:
        return
    print()
    print(colored("Session summary:", "bold"))
    print(f"  Correct: {colored(str(graded['correct']), 'green')}")
    print(f"  Wrong:   {colored(str(graded['wrong']), 'red')}")
    print(f"  Skipped: {colored(str(graded['skip']), 'dim')}")
    print(f"  Total:   {total}")
    print()


# =============================================================================
# Entry point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Review Rocky's classifications.")
    parser.add_argument("--recent", type=int, default=0,
                       help="Review only the N most recent classifications.")
    parser.add_argument("--remy-only", action="store_true",
                       help="Review only classifications he flagged as Remy.")
    parser.add_argument("--stats", action="store_true",
                       help="Print accuracy stats only, no review.")
    parser.add_argument("--export", action="store_true",
                       help="Export wrong classifications as a markdown report.")
    parser.add_argument("--redo", action="store_true",
                       help="Include already-reviewed classifications in the review queue.")
    args = parser.parse_args()

    classifications = load_classifications()
    reviews = load_reviews()

    if args.stats:
        print_stats(classifications, reviews)
        return

    if args.export:
        export_misclassifications(classifications, reviews)
        return

    review_loop(classifications, reviews, args)
    print()
    print_stats(classifications, reviews)


if __name__ == "__main__":
    main()
