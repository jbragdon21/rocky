"""Litigation Updater closure instructions: the claim name is read out of
"move X to closed", and a quoted closure note is used word for word.

    python -m pytest tests/test_litigation_intent.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import litigation_updater as lu  # noqa: E402

NOTICE = {"legalnotices@bozzuto.com"}
FONTENOT = ("For litigation spreadsheet, please move Fontenot to the closed "
            "claims spreadsheet with the closure note “settled by "
            "insurer without contribution from BMC.”")


def _msg(body, subject=""):
    return {"subject": subject, "body": {"content": body},
            "from": {"emailAddress": {"address": "jbragdon@gallagherllp.com"}}}


def test_fontenot_instruction():
    msg = _msg(FONTENOT)
    assert lu.detect_intent(msg, NOTICE) == ("closure", "Fontenot")
    assert lu.quoted_closure_note(msg) == (
        "settled by insurer without contribution from BMC.")


def test_claim_name_variants():
    cases = {
        "please move the Fontenot claim to Closed claims": "Fontenot",
        "Please transfer Smith v. BMC into the closed sheet": "Smith v. BMC",
        "move the Johnson case to the closed claims sheet": "Johnson",
    }
    for body, hint in cases.items():
        assert lu.detect_intent(_msg(body), NOTICE) == ("closure", hint), body


def test_pronoun_is_not_a_claim_name():
    assert lu.detect_intent(_msg("please move it to closed claims"),
                            NOTICE) == ("closure", None)


def test_straight_quotes_and_no_quote():
    assert lu.quoted_closure_note(_msg(
        'move Roe to closed with the note "Dismissed with prejudice 9/1/26."'
    )) == "Dismissed with prejudice 9/1/26."
    assert lu.quoted_closure_note(_msg("move Roe to closed claims")) is None


def test_quote_in_forwarded_chain_is_ignored():
    body = ("please move Roe to closed claims\n\n"
            "From: Someone <x@bozzuto.com>\nSent: today\n"
            'Our note "do not close yet" stands.')
    assert lu.quoted_closure_note(_msg(body)) is None


def test_closure_proposal_flags_your_wording():
    ask = {"id": "L0600", "kind": "closure", "claim": "Fontenot",
           "note": "settled by insurer without contribution from BMC.",
           "note_verbatim": True}
    text = lu._proposal_text(ask)
    assert "closure note (your wording):" in text
    assert "> settled by insurer without contribution from BMC." in text
    del ask["note_verbatim"]
    assert "(your wording)" not in lu._proposal_text(ask)
