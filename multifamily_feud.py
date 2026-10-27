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

Four sources (James, 10/26). Not every one reports every day; a folder
with nothing new just leaves that source out of the episode.

  REPORT    Three folders in the Multifamily Feud case folder (Rocky Cases\\
            Multifamily Feud (RRID-0045), found by RRID):
              Daily ChatGPT research                   the overnight research memo
              Courthouse News\\Hit Reports             hits from the CNS brief screen
              Tracked Cases\\Daily Summary of Tracked Cases
                                                       what changed in tracked cases
            Each readable file (.md .txt .docx .pdf, top level only; names
            starting with . _ or ~ skipped) dated today or yesterday counts,
            by the YYYY-MM-DD in its name, else the day it was last saved. A
            file is used once: the episode records it, and a file saved
            again later counts as new. A long file is split at its "## "
            headings into cards of at most 12,000 characters.

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
    multifamily_feud_folder    the Multifamily Feud case folder; found by RRID
                               when unset
    multifamily_feud_style     the style guide (default PODCAST_STYLE.md in the
                               show folder, written there on first run)
    multifamily_feud           false turns the whole job off
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
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
AUDIO_TAGS = ("excited", "curious", "sarcastic", "mischievously", "surprised",
              "chuckles", "laughs", "sighs", "jumping in", "interrupting")


# The report folders in the case folder, in the order the show runs them.
SOURCES = (
    ("Daily research memo", ("Daily ChatGPT research",)),
    ("Courthouse News hits", ("Courthouse News", "Hit Reports")),
    ("Tracked cases summary", ("Tracked Cases",
                               "Daily Summary of Tracked Cases")),
)
_READABLE = (".md", ".txt", ".docx", ".pdf")
_SKIP_NAME = re.compile(r"^[.~_]|\.(pending|partial|tmp)\.", re.IGNORECASE)
_DATE_IN_NAME = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_SETTLE_SECONDS = 120     # a file saved this recently may still be writing


def tracker_root(config: dict) -> Path | None:
    """The Multifamily Feud case folder: config multifamily_feud_folder if
    it exists, else the RRID-0045 folder under Rocky Cases."""
    configured = config.get("multifamily_feud_folder")
    if configured and Path(configured).is_dir():
        return Path(configured)
    import rocky
    from kirsten_digest import find_folder
    rocky._init_cases_paths(config)
    found = find_folder(rocky.ROCKY_CASES_ROOT, {"RRID#": TRACKER_RRID})
    if found and found.is_dir():
        if configured:
            log.info(f"[{_TAG}] multifamily_feud_folder {configured} not "
                     f"found; using {found}")
        return found
    log.warning(f"[{_TAG}] no {TRACKER_RRID} case folder under "
                f"{rocky.ROCKY_CASES_ROOT}")
    return None


def file_day(path: Path) -> date:
    """The day a report is for: the YYYY-MM-DD in its name, else the day it
    was last saved."""
    m = _DATE_IN_NAME.search(path.name)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime).date()


def input_key(folder: Path, path: Path) -> str:
    """How an episode records a file it used: the path inside the case
    folder and when it was saved, so a file saved again counts as new."""
    rel = path.relative_to(folder).as_posix()
    return f"{rel}@{int(path.stat().st_mtime)}"


def read_report(path: Path) -> str:
    if path.suffix.lower() in (".md", ".txt"):
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        import rocky
        text = rocky.extract_text_from_path(path) or ""
    return sp.strip_front_matter(text.strip()).strip()


def split_report(text: str, cap: int = sp._MEMO_CAP) -> list[str]:
    """A report cut at its "## " headings into pieces of at most cap
    characters (a single longer section is cut at cap)."""
    pieces, current = [], ""
    for section in re.split(r"(?m)^(?=## )", text):
        if not section.strip():
            continue
        while len(section) > cap:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(section[:cap])
            section = section[cap:]
        if current and len(current) + len(section) > cap:
            pieces.append(current)
            current = ""
        current += section
    if current.strip():
        pieces.append(current)
    return [p.strip() for p in pieces]


def gather(config: dict, now: datetime, used: set[str],
           tag: str = _TAG) -> dict | None:
    """The report cards' material: every new readable file dated today or
    yesterday in the three report folders, each split into pieces.
    {"date", "memos", "inputs"}, or None when no folder has anything new."""
    folder = tracker_root(config)
    if folder is None:
        return None
    days = {now.date(), now.date() - timedelta(days=1)}
    memos: list[dict] = []
    inputs: list[str] = []
    for label, parts in SOURCES:
        where = folder.joinpath(*parts)
        if not where.is_dir():
            log.warning(f"[{tag}] {label}: no folder {where}")
            continue
        found = 0
        for path in sorted(where.iterdir(), key=lambda p: p.name):
            if not path.is_file() or path.suffix.lower() not in _READABLE \
                    or _SKIP_NAME.search(path.name):
                continue
            try:
                if file_day(path) not in days:
                    continue
                if now.timestamp() - path.stat().st_mtime < _SETTLE_SECONDS:
                    log.info(f"[{tag}] {label}: {path.name} was just saved; "
                             f"left for the next run")
                    continue
                key = input_key(folder, path)
                if key in used:
                    continue
                text = read_report(path)
            except OSError as e:
                log.warning(f"[{tag}] {label}: cannot read {path.name} ({e})")
                continue
            if not text:
                log.warning(f"[{tag}] {label}: {path.name} is empty or "
                            f"unreadable; skipped")
                continue
            pieces = split_report(text)
            for i, piece in enumerate(pieces, 1):
                topic = label if len(pieces) == 1 else \
                    f"{label} ({i} of {len(pieces)})"
                memos.append({"topic": topic, "file": path.name,
                              "text": piece})
            inputs.append(key)
            found += 1
        log.info(f"[{tag}] {label}: "
                 + (f"{found} new file(s)" if found else "nothing new"))
    if not memos:
        return None
    return {"date": now.strftime("%Y-%m-%d"), "memos": memos,
            "inputs": inputs}


TOPIC_CORE = """# Goal

Turn one of today's reports (or one part of a long one) into a card for Multifamily Feud, Gallagher LLP's internal daily audio show on fee and multifamily class action defense. The card is the show notes for this report and the only source the hosts will read from.

TOPIC names the report:
- "Daily research memo": the overnight research memo on fee and multifamily class actions (decisions, filings, legislation, follow-up for our matters).
- "Courthouse News hits": fee or multifamily class action items flagged from the day's Courthouse News brief.
- "Tracked cases summary": what changed in the outside cases we track.

MEMO is that report's text.

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
- "angles": the memo's own analysis of why an item matters to us (its "Why it matters", relevance or defense follow-up notes), as our view, tied to our matters by name where the memo ties them.
- When the memo reports nothing new, the headline says so and the lists are empty.

# Guardrails

- Use only the memo. Do not add or calculate dates, holdings, names, amounts or outcomes. This step is important.
- A motion, petition or appeal stays pending unless the memo reports the ruling.
- An item the memo bases only on a news report stays "reported".
- An item the memo labels as older or newly discovered keeps its own date: never present it as new overnight.
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

You receive the day's cards. REPORT cards come from the day's outside reports: the overnight research memo, Courthouse News hits and the daily summary of tracked cases. Not every report comes every day. CASE cards are yesterday's activity on the matters Gallagher defends. The cards are your only source. Follow the STYLE GUIDE below for the show's shape, tone, pacing and delivery. Where it conflicts with these guardrails, the guardrails win.

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
- Never mention Rocky, AI, ChatGPT, activity logs, cards, memos, the research memo or the hit reports: the hosts just know the news. Crediting a news outlet for an item that rests on its reporting is fine.
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
{"fixes": [{"turn": <number>, "problem": "<short reason>", "text": "<the whole turn rewritten to say only what the cards support, same speaker voice and tone, swearing included, or empty to drop the turn>"}]}

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
audio_tags: excited, curious, sarcastic, mischievously, surprised, chuckles, laughs, sighs, jumping in, interrupting
model: claude-opus-5-5
repeat_lookback_days: 3
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
  stability               0 = most expressive and most responsive to the
                          delivery cues, 1 = steadiest; ElevenLabs' own
                          default is 0.5; "default" sends none. 0.5
                          (Natural) keeps each voice closest to its
                          sample; 0.0 (Creative) let both voices drift
                          away from theirs on 10/26/2026
  intro_file              the intro music: a .wav, .flac or .mp3 in this
                          folder (or a full path); none = no intro
  intro_seconds           how much of it plays; 18 ends on the swing track's
                          first phrase; 0 = all 2:21 of it
  intro_fade_seconds      fade-out over the end of the intro
  intro_overlap_seconds   how early the hosts start before the music ends
  target_words            the length to aim for (about 150 words a minute)
  max_words               hard cap; anything longer is cut
  audio_tags              Eleven v3 delivery cues the script may use, comma
                          separated, at most 12 (see below), or none
  model                   the Claude model that writes cards and script
  repeat_lookback_days    how many earlier days' episodes the script sees as
                          already covered (only episodes whose audio came
                          out full length); 0 = off

THE SECTIONS. "## Report cards" and "## Case cards" style the show notes
(one card per report, or per part of a long one, and one per active case).
The reports come from three folders in the Multifamily Feud case folder:
Daily ChatGPT research, Courthouse News\\Hit Reports and Tracked Cases\\
Daily Summary of Tracked Cases. Not every folder has a report every day.
Every other
section styles the script. Rocky fills in {host1}, {host2}, {opener},
{other} and {target}: {opener} is the host who opens today and {other} is
the second host.

"## Joe's preferences" is where Joe's feedback goes as it comes in: add a
dated bullet, newest last. The script follows those bullets over anything
earlier in the file, so a new preference doesn't need the older sections
rewritten.

HOW THE SCRIPT IS ANNOTATED FOR ELEVENLABS. ElevenLabs receives only the
hosts' lines, one turn at a time, so every bit of direction rides in the
text. From ElevenLabs' v3 guidance
(elevenlabs.io/docs/overview/capabilities/text-to-speech/best-practices
and elevenlabs.io/blog/v3-audiotags):
  audio tags   a cue in square brackets right before the words it colors:
               "[sarcastic] Bold theory." Only the tags listed in
               audio_tags survive; Rocky deletes any other bracketed text
               before voicing. Light tagging sounds more natural than a
               cue on every line.
  cut-ins      one turn stops mid-sentence on a dash and the next opens
               with [jumping in] or [interrupting]
  ellipses     a pause with weight; they slow the read, so this show
               saves them for the beat before a punchline
  CAPS         stress on one word ("dismissed WITH prejudice")
  ! and ?      energy and lift; v3 reads both
  stability    lower values follow the cues more closely and vary more
               from take to take
A tag that doesn't suit a voice sometimes gets read out as words. If an
episode says "jumping in" aloud, take that tag off audio_tags. Other v3
cues worth trying: annoyed, dismissive, cheeky, thoughtful, laughs harder,
clears throat, short pause. Test a change on the Rocky laptop with
  rocky.exe --multifamily-feud --dry-run --audio
(add --force once that day's episode has run). It spends ElevenLabs
credits, publishes nothing, and leaves the audio at
C:\\Rocky\\state\\multifamily_feud_preview.mp3.

Fixed rules live in Rocky's code and win over this file: only facts from the
day's report and case logs, nothing from the model's own knowledge, no past
date as upcoming, nobody's work is "you", never mention Rocky or AI, no case
numbers or URLs, and a fact check on every script. Delete this file and
Rocky writes a fresh copy of the original on the next run.

PROMPTING_GUIDE.md in this folder is ElevenLabs' guide to writing agent
prompts. The sections below follow its format: short rules under clear
headings. The annotation rules come from the v3 pages above.
-->

## Personality

{host1} and {host2} host Multifamily Feud, Gallagher's internal morning show on fee and multifamily class action defense. They are two defense litigators who read every opinion and give each other a hard time about it.

- {host1} is dry and skeptical: no plaintiff win counts until the reasoning holds up, and a clean procedural knockout makes the day.
- {host2} is quick and exacting: straight to the holding and the standard, and the first to say when a ruling is thinner than its headline.

## Audience

Joe, a class action defense lawyer, is the main listener. Over time the whole class action defense and multifamily group will listen. Everyone listening is a specialist.

- Keep it technical. Use terms of art without defining them, and keep each card's legal detail: the procedural posture, the rule or standard applied, the ground the ruling turned on, what survived and what didn't, with or without prejudice, leave to amend.
- Never explain basics: no defining a motion to dismiss, class certification, predominance or standing.
- The technical depth comes from the cards. Never add a doctrine, a standard or a case that isn't on one.
- The hosts talk to each other. Never address the listener, and never name Joe on air.

## Goal

Get the group through the day's class action news and our own docket in about ten minutes, with the ground each ruling turned on and what it means for our cases.

## Shape

- **Opening.** {opener} opens: "Multifamily Feud", the day and date, and a one-line preview of the headlines. {other} answers in the second turn, ideally with a quip.
- **Outside news.** The research memo's decisions, filings and legislation first, then the Courthouse News hits, then the tracked-case updates. For each decision give the case, the court, what it decided and on what ground, and why it matters to us. A source with no card today gets no mention.
- **Our docket.** Hand off clearly ("Now to our own docket."). For each active case: what happened yesterday, who did it, and what comes next and when. Then the quiet cases in one turn: name them, nothing more. If there are no case cards, skip this segment.
- **Close.** The next key date in the outside news and on our docket, then a quick sign-off ("That's the Feud. Back tomorrow.").
- The bold labels name the segments for you. A host never reads one out ("Close."): move between segments in conversation.

## Length

About {target} words in total (about ten minutes spoken). Budget roughly 60 words for the opening, 150 to 250 per decision, 100 to 150 per active case, 30 for the quiet cases, and 50 for the close. A busy day gets less detail per item and keeps the same length.

## Tone

- Quick and quippy: dry wit, fast reactions, and a running joke when the day hands them one.
- Light profanity is welcome a few times an episode: damn, hell, crap, ass, and once or twice "shit".
- Every episode gets at least one "fuck" (or "fucking"), and two at most, where a host reacts to something that earns it: a brutal ruling, a nine-figure verdict, an argument that had no business surviving a motion to dismiss. Never in the opening turn or the sign-off, and no slurs.
- Aim the humor and the swearing at rulings, arguments, statutes and deadlines. Never at a named person: judges, clients, opposing counsel and colleagues are off limits.
- The hosts enjoy a good ruling for the defense side and wince at a bad one.
- Our own cases stay precise. Quick is fine; jokes at a client's expense are not.

## Interplay

- Every item gets a real exchange. One host gives the news; the other reacts, pushes back, or asks what a defense lawyer would ask next ("On what ground?" "With prejudice?").
- Short reaction turns keep it moving: "Oof." "Damn." "Ha."
- Now and then a host cuts in: the cut-off turn stops mid-sentence on a dash (—), and the next turn opens with [jumping in] or [interrupting]. Two to four a show, and never in the middle of a fact.
- The hosts can weigh a card's angles differently, one finding it bigger than the other does. Both stay on the card's facts, and neither predicts an outcome.
- Callbacks to an earlier item or a running joke are good.

## Pace

- Short sentences, one idea each.
- Quick handoffs: most turns are one or two sentences, and a technical point gets three at most. Break anything longer into an exchange.
- Ellipses slow the voice down. Use one only for the beat before a punchline.

## Voice cues

- A cue goes in square brackets right before the words it colors: "[sarcastic] Bold theory."
- Open the show with one, then about one every three or four turns where a real reaction fits. A cue on every turn sounds forced.
- [sarcastic] and [mischievously] carry the quips. [laughs] and [chuckles] go on the funny moments, [sighs] on a bad ruling, [surprised] on a real surprise, [curious] on a real question and [excited] on a big defense win. [jumping in] and [interrupting] only open a turn that cuts in.
- CAPS on a single word for stress, a couple per segment at most: "dismissed WITH prejudice."
- Exclamation marks only for real excitement.
- On our own docket, a light cue at most ([chuckles], [sighs], [excited]), and no sarcasm about our clients or their cases.

## Repeats

ALREADY COVERED holds the last few episodes the team heard.

- Don't re-report a development already covered. If it still matters, give it one short callback ("As we said yesterday, ...") and move on.
- Something new in an old story leads with the new part.
- Upcoming hearings and deadlines may come up again in one line. Always mention anything in the next three days.
- Background already given (a case's posture, the claims) gets a few words at most.

## Delivery

- Speaker turns alternate.
- Say case names the way lawyers do in conversation ("Hettinger", "the Bulgin case") and courts in words ("the Eastern District of Virginia", "the D.C. Circuit").
- Say rules the way lawyers say them aloud ("Rule twelve b six", "twenty-three b three").
- Name a statute in full the first time ("the Virginia Consumer Protection Act"), then by its short name.
- Say dates as spoken ("November ninth"), with the weekday from the calendar.
- Write numbers and money as words ("a hundred and twenty-five dollars", "twenty-three million dollars").

## Joe's preferences

Joe's feedback on the show, newest last. Where a bullet here conflicts with anything above, the bullet wins.

- (10/26/2026) Quick and quippy, with light profanity and real interplay between the hosts.
- (10/26/2026) Technical throughout. Never dumbed down for nonlawyers or nonspecialists.

## Report cards

- **Headline:** one sentence, at most 25 words: the most important thing in this section today.
- **Developments:** 0 to 6, most important first, one or two sentences each: the case, the court and judge, what happened, and the ground it turned on (the rule, standard or element) in the memo's own legal terms. Say "reported" when the memo has only a news report.
- **Upcoming:** 0 to 4 dates, soonest first.
- **Angles:** 0 to 3 lines from the report's own analysis of why it matters to us, tied to our matters by name.

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
    no_topics_note=("(No report cards today: no outside news segment, and "
                    "do not mention research, Courthouse News, outside "
                    "decisions or tracked cases at all.)"),
    topics_heading="Outside news", cases_heading="Our docket",
    edition_word="report", gather=gather,
    stability=0.5, audio_tags=AUDIO_TAGS)


def run_cli(config: dict, data_dir: Path) -> None:
    sp.run_cli(config, data_dir, show=FEUD)
