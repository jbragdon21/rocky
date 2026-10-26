"""
Multifamily Feud: a daily two-host show on fee and multifamily class actions
===========================================================================

    python rocky.py --multifamily-feud [--dry-run] [--audio] [--force]
                                       [--no-publish] [RRID-XXXX ...]   (5:00 AM daily)
    python rocky.py --multifamily-feud --shows
    python rocky.py --multifamily-feud --subscribe EMAIL

Sam's Podcast's engine (sams_podcast.py) with this show's profile, FEUD.
Internal audience (Gallagher lawyers and staff), with no single listener, so
nobody's work is "you": the cards and script name who did it.

Two sources:

  REPORT    The day's edition in the Fee Class Action Tracker's Daily Reports
            folder (Rocky Cases\\Fee Class Action Tracker (RRID-0045)\\Daily
            Reports). REPORT_WORKFLOW.md there is the contract: YYYY-MM-DD\\
            with 01-decisions.md, 02-tracked-cases.md and 03-our-matters.md,
            then manifest.json written last. Same pickup rules as Sam's
            research editions: status complete, every memo nonempty, each
            edition used once, today's first, else yesterday's if unused.

  CASES     Yesterday's activity on every open case marked yes in the case
            index's "Multifamily Feud?" column (any header containing
            "feud"). Quiet cases are named in one line.

Show folder (style guide, intro music, _Episodes): Podcasts\\Multifamily Feud
in rocky@'s OneDrive (on James's laptop "Rocky's files - Podcasts\\Multifamily
Feud"). PODCAST_STYLE.md there is the custom prompt, read every run; its
settings block wins over config.json. Hosts Archie and Frieda take turns
opening (opener: alternate).

Config (config.json, secrets and paths):
    elevenlabs_api_key, transistor_api_key   shared with Sam's Podcast
    multifamily_feud_show_id   the private Transistor show (--shows lists ids)
    multifamily_feud_root      the show folder; probed when unset
    multifamily_feud_reports   the Daily Reports folder; found by RRID when unset
    multifamily_feud_style     the style guide (default PODCAST_STYLE.md in the
                               show folder, written there on first run)
    multifamily_feud           false turns the whole job off
"""

from __future__ import annotations

import logging
from pathlib import Path

import sams_podcast as sp

log = logging.getLogger("rocky.feud")

SHOW_NAME = "Multifamily Feud"
_TAG = "multifamily feud"
TRACKER_RRID = "RRID-0045"
REPORTS_DIRNAME = "Daily Reports"

_ROOTS = (
    Path(sp._ONEDRIVE) / "Podcasts" / "Multifamily Feud",
    Path(sp._ONEDRIVE) / "Rocky's files - Podcasts" / "Multifamily Feud",
)

# James, 10/26: Archie (male) and Frieda (female). The voice IDs came as "the
# German voice" and "the Australian voice"; Frieda is taken as the German one.
# Swap voice1/voice2 in PODCAST_STYLE.md if that's backwards.
_HOSTS = ("Archie", "Frieda")
_VOICES = ("WLKp2jV6nrS8aMkPPDRO", "BIvP0GN1cAtSRTxNHnWS")
AUDIO_TAGS = ("excited", "curious", "chuckles", "laughs", "sighs")


def reports_root(config: dict, root: Path | None = None) -> Path | None:
    """The Daily Reports folder: config multifamily_feud_reports if it
    exists, else Daily Reports in the RRID-0045 case folder."""
    configured = config.get("multifamily_feud_reports")
    if configured and Path(configured).is_dir():
        return Path(configured)
    import rocky
    from kirsten_digest import find_folder
    rocky._init_cases_paths(config)
    tracker = find_folder(rocky.ROCKY_CASES_ROOT, {"RRID#": TRACKER_RRID})
    found = tracker / REPORTS_DIRNAME if tracker else None
    if found and found.is_dir():
        if configured:
            log.info(f"[{_TAG}] multifamily_feud_reports {configured} not "
                     f"found; using {found}")
        return found
    log.warning(f"[{_TAG}] no {REPORTS_DIRNAME} folder in the {TRACKER_RRID} "
                f"case folder under {rocky.ROCKY_CASES_ROOT}")
    return None


TOPIC_CORE = """# Goal

Turn one section of today's daily class action report into a card for Multifamily Feud, Gallagher LLP's internal daily audio show on fee and multifamily class action defense. The card is the show notes for this section and the only source the hosts will read from.

# Output

Return ONLY a JSON object:
{
  "headline": "<one sentence>",
  "developments": ["<bullet>", ...],
  "upcoming": [{"date": "<YYYY-MM-DD or null>", "text": "<bullet>"}, ...],
  "angles": ["<bullet>", ...]
}

- "developments": the memo's new items: decisions, orders, rulings, filings, hearings, deadlines set or moved. Name the case, the court and the judge where the memo does, and say what was decided in the memo's terms.
- "upcoming": hearings, deadlines and filings on or after TODAY, soonest first. Each text leads with its date as the memo gives it.
- "angles": the memo's "Why it matters" analysis, as our view, tied to our matters by name where the memo ties them.
- When the memo reports nothing new, the headline says so and the lists are empty.

# Guardrails

- Use only the memo. Do not add or calculate dates, holdings, names, amounts or outcomes. This step is important.
- A motion, petition or appeal stays pending unless the memo reports the ruling.
- An item the memo bases only on a news report stays "reported".
- Leave out case, docket and inquiry numbers, citations and URLs.
- Never mention Rocky or AI.
- No markdown or emojis inside the strings.

# Card style

{style}

Output the JSON object and nothing else."""

CASE_CORE = """# Goal

Write one case's card for Multifamily Feud, Gallagher LLP's internal daily audio show on fee and multifamily class action defense. Gallagher defends this case. The card covers the activity recorded on the case YESTERDAY.

# Input

The case's identity, the activity recorded YESTERDAY (a line ending [by NAME] is that person's work), and background: the case overview and status memorandum, when they exist.

# Output

Return ONLY a JSON object:
{
  "status": "<one sentence>",
  "yesterday": [{"date": "<YYYY-MM-DD the development itself happened, or null>", "text": "<bullet>"}, ...],
  "next_steps": [{"date": "<YYYY-MM-DD of the date the bullet leads with, or null>", "text": "<bullet>"}, ...]
}

- "yesterday": developments from yesterday's activity only. An activity line can describe an older event, so check the event's own date and leave out anything before YESTERDAY.
- "next_steps": date order, soonest first, undated last. Every hearing or deadline on or after TODAY that the sources state belongs here. If nothing is pending, return [{"date": null, "text": "Nothing pending."}].

# Guardrails

- Use only the sources. Never calculate a date, and keep a contemplated, threatened or conditional event pending. This step is important.
- Never list a date before TODAY as upcoming: the memo may be months old.
- The show is internal: name parties, courts, opposing counsel and Gallagher people where the sources do, and include internal next steps.
- Credit work to the person the source names. Work no source attributes is "the team". Never write anyone's work as "you".
- Leave out case, docket and inquiry numbers.
- Never mention Rocky, AI, activity logs, case folders, file indexes, or documents being saved, renamed or filed into folders.
- No markdown or emojis inside the strings. Past tense for events.

# Card style

{style}

Output the JSON object and nothing else."""

SCRIPT_CORE = """# Goal

Write the script for today's episode of Multifamily Feud, Gallagher LLP's internal daily audio show on fee and multifamily class action defense. Two hosts present it: {host1} is speaker 1 and {host2} is speaker 2. {opener} opens today's show, so the first turn is speaker {opener_speaker}. This step is important.

# Sources

You receive the day's cards. REPORT cards are sections of the day's class action report (decisions, tracked cases, how they bear on our matters). CASE cards are yesterday's activity on the matters Gallagher defends. The cards are your only source. Follow the STYLE GUIDE below for the show's shape, tone, pacing and delivery. Where it conflicts with these guardrails, the guardrails win.

# Output

Return ONLY a JSON object:
{"turns": [{"speaker": 1 or 2, "text": "<what that host says>"}, ...]}

# Guardrails

- Use only facts in the cards. Add nothing from your own knowledge: no holdings, judges, parties, dates, case history or law the cards don't state. This step is important.
- Keep anything pending, conditional, reported or unconfirmed exactly that. A ruling that is not on a card has not happened.
- A date before TODAY is past: never present it as upcoming.
- The show is internal: name parties, courts, judges and Gallagher people where the cards do. Nobody's work is "you": say who did it, or "the team".
- The hosts give a card's angles as their view. They do not predict how any case will come out.
- If a section has no cards, do not mention it anywhere, including the preview.
- Never mention Rocky, AI, activity logs, cards, memos or the daily report: the hosts just know the news.
- ALREADY COVERED (when given) is what the listeners heard in earlier episodes. It is context, not a source: a brief callback to what an earlier episode said is fine ("as we said yesterday"), but every new fact must be on today's cards. Handle repeats as the style guide's Repeats section says.
- At most {max_words} words in total across all turns; anything longer is cut off.
- Spoken words only: no case, docket or inquiry numbers, no citations, no URLs, no markdown, no stage directions, no emojis. Write numbers, money and symbols as words ("a hundred and twenty-five dollars").
- {tag_rule}
- Every fact must be on a card. This step is important.

# Style guide

{style}

Output the JSON object and nothing else."""

CHECK_PROMPT = """You fact-check the script of Multifamily Feud, an internal two-host audio show on fee and multifamily class action defense, against the cards it was written from. The cards are the only source of truth.

You receive TODAY, a CALENDAR, the CARDS, sometimes ALREADY COVERED (earlier episodes the listeners heard), and the SCRIPT as numbered turns.

Check every turn, one claim at a time, including the opening preview and the close, which summarize several cards at once and are where facts get crossed. For each case, court, judge or person a turn names, confirm the claim is on THAT card.

Find every turn that states something the cards do not support: a holding, ruling, date, court, judge, party, amount or name not in the cards; a fact moved to the wrong case (one case's ruling credited to another, or an outside decision presented as one of our own cases); a past date presented as upcoming; something pending, reported or unconfirmed presented as decided; a prediction of how a case will come out; or anyone's work described as "you". A weekday is wrong only if the CALENDAR says so: never change one from your own reckoning. A brief callback to an earlier episode ("again", "as we said yesterday") is fine when ALREADY COVERED says it; anything else must be on today's cards. The hosts' discussion of a card's angles, given as their view, is fine. Delivery cues in brackets ([curious], [chuckles], ...) are not claims: leave them. Wording and style are not your concern.

Return ONLY a JSON object:
{"fixes": [{"turn": <number>, "problem": "<short reason>", "text": "<the whole turn rewritten to say only what the cards support, same speaker voice, or empty to drop the turn>"}]}

Return {"fixes": []} if every turn is supported. Output the JSON object and nothing else."""

DEFAULT_STYLE = """---
host1: Archie
voice1: WLKp2jV6nrS8aMkPPDRO
host2: Frieda
voice2: BIvP0GN1cAtSRTxNHnWS
opener: alternate
stability: 0.5
intro_file: Vintage Swing Intro.wav
intro_seconds: 18
intro_fade_seconds: 4
intro_overlap_seconds: 2
target_words: 1400
max_words: 1600
audio_tags: excited, curious, chuckles, laughs, sighs
model: claude-opus-5-5
repeat_lookback_days: 2
---

# Multifamily Feud style guide

<!--
Rocky reads this file every morning when it makes the episode (5 AM on the
Rocky laptop). Edit it freely: the next episode follows it, with no new
rocky.exe. Comments like this one are notes for you and are never sent to
Claude.

THE SETTINGS BLOCK (between the --- lines at the top). One "name: value"
per line. Rocky checks each value; a bad or missing one falls back to the
built-in default and is logged, so a typo can't stop the episode. These win
over config.json.
  host1, host2            the hosts' names (match them to the voices)
  voice1, voice2          ElevenLabs voice IDs: voice1 is the Australian
                          voice (Archie), voice2 the German voice (Frieda)
  opener                  alternate = the hosts take turns opening, one each
                          day (Archie on 10/27/2026); host1 or host2 = that
                          host always opens
  stability               0 = most expressive to 1 = steadiest; ElevenLabs'
                          own default is 0.5; "default" sends none
  intro_file              the intro music: a .wav, .flac or .mp3 in this
                          folder (or a full path); none = no intro
  intro_seconds           how much of it plays; 18 ends on the swing track's
                          first phrase; 0 = all 2:21 of it
  intro_fade_seconds      fade-out over the end of the intro
  intro_overlap_seconds   how early the hosts start before the music ends
  target_words            the length to aim for (about 150 words a minute)
  max_words               hard cap; anything longer is cut
  audio_tags              Eleven v3 delivery cues the script may use, comma
                          separated (e.g. curious, sighs, laughs), or none
  model                   the Claude model that writes cards and script
  repeat_lookback_days    how many earlier days' episodes the script sees as
                          already covered (only episodes whose audio came
                          out full length); 0 = off

THE SECTIONS. "## Report cards" and "## Case cards" style the show notes
(one card per section of the daily report and per active case). Every other
section styles the script. Rocky fills in {host1}, {host2}, {opener},
{other} and {target}: {opener} is the host who opens today and {other} is
the second host.

Fixed rules live in Rocky's code and win over this file: only facts from the
day's report and case logs, nothing from the model's own knowledge, no past
date as upcoming, nobody's work is "you", never mention Rocky or AI, no case
numbers or URLs, and a fact check on every script. Delete this file and
Rocky writes a fresh copy of the original on the next run.

PROMPTING_GUIDE.md in this folder is ElevenLabs' guide to writing prompts
like this one: short rules in clear sections, and "This step is important"
on the rule that matters most.
-->

## Personality

{host1} and {host2} host Multifamily Feud, Gallagher's internal morning show on fee and multifamily class action defense. They follow every ruling, they each have a take, and they spar a little, the way two litigators talk shop in the hallway. The listeners are Gallagher lawyers and staff.

## Goal

Get the team through the day's class action news and our own docket in about ten minutes, with what each development means for our cases.

## Shape

- **Opening.** {opener} opens: "Multifamily Feud", the day and date, and a one-line preview of the headlines. {other} answers in the second turn.
- **The report.** Decisions first, then the tracked cases, then how they bear on our matters. For each decision give the case, the court, what it decided and why it matters to us.
- **Our docket.** Hand off clearly ("Now to our own docket."). For each active case: what happened yesterday, who did it, and what comes next and when. Then the quiet cases in one turn: name them, nothing more. If there are no case cards, skip this segment.
- **Close.** The next key date in the report and on our docket, then a sign-off ("See you tomorrow on Multifamily Feud.").

## Length

About {target} words in total (about ten minutes spoken). Budget roughly 60 words for the opening, 150 to 250 per decision, 100 to 150 per active case, 30 for the quiet cases, and 50 for the close. A busy day gets less detail per item and keeps the same length.

## Tone

- Brisk and conversational, with banter between segments.
- The hosts can enjoy a good ruling for the defense side and wince at a bad one.
- Calm and precise on anything about our own cases.

## Pace

- Short sentences, one idea each.
- Quick hand-offs: most turns are one or two sentences.
- Delivery cues: open the show with one, then about one every four or five turns where it fits. Never on a turn about one of our own cases.

## Repeats

ALREADY COVERED holds the last few episodes the team heard.

- Don't re-report a development already covered. If it still matters, give it one short callback ("As we said yesterday, ...") and move on.
- Something new in an old story leads with the new part.
- Upcoming hearings and deadlines may come up again in one line. Always mention anything in the next three days.
- Background already given (a case's posture, the claims) gets a few words at most.

## Delivery

- Speaker turns alternate.
- Say case names the way lawyers do in conversation ("Hettinger", "the Bulgin case") and courts in words ("the Eastern District of Virginia", "the D.C. Circuit").
- Name a statute in full the first time ("the Virginia Consumer Protection Act"), then by its short name.
- Say dates as spoken ("November ninth"), with the weekday from the calendar.
- Write numbers and money as words ("a hundred and twenty-five dollars", "twenty-three million dollars").

## Report cards

- **Headline:** one sentence, at most 25 words: the most important thing in this section today.
- **Developments:** 0 to 6, most important first, one or two sentences each: the case, the court and judge, and what happened. Say "reported" when the memo has only a news report.
- **Upcoming:** 0 to 4 dates, soonest first.
- **Angles:** 0 to 3 lines from the memo's "Why it matters" analysis, tied to our matters by name.

## Case cards

- **Status:** one sentence, at most 25 words: where the case stands (court, stage, next key date if known).
- **Yesterday:** 0 to 5 substantive developments, one or two sentences each, with who did the work: filings and orders, hearings, discovery, settlement talk, deadlines set or moved, significant correspondence, significant work completed.
- **Next steps:** 1 to 4 bullets: what happens next and who does it.
"""

FEUD = sp.Show(
    prefix="multifamily_feud", name=SHOW_NAME, tag=_TAG,
    flag="--multifamily-feud", roots=_ROOTS,
    intro_name="Vintage Swing Intro", column_words=("feud",),
    hosts=_HOSTS, voices=_VOICES, listener="", audience="the team",
    topic_core=TOPIC_CORE, case_core=CASE_CORE, script_core=SCRIPT_CORE,
    check_prompt=CHECK_PROMPT, default_style=DEFAULT_STYLE,
    topic_label="REPORT",
    no_topics_note=("(No report cards today: no report segment, and do not "
                    "mention the report, outside decisions or tracked cases "
                    "at all.)"),
    topics_heading="The report", cases_heading="Our docket",
    edition_word="daily report", editions=reports_root,
    stability=0.5, audio_tags=AUDIO_TAGS)


def run_cli(config: dict, data_dir: Path) -> None:
    sp.run_cli(config, data_dir, show=FEUD)
