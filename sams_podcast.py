"""
Sam's Podcast — a daily two-host audio show: sports research + case activity
============================================================================

    python rocky.py --sams-podcast [--dry-run] [--audio] [--force]
                                   [--no-publish] [RRID-XXXX ...]   (6:00 AM daily)
    python rocky.py --sams-podcast --shows
    python rocky.py --sams-podcast --subscribe EMAIL

Sam is a Gallagher colleague, so this is an INTERNAL show: the case segment
may name parties, firm people and internal next steps (unlike the Kirsten
podcast, which is client-facing).

Two sources:

  RESEARCH  A separate research automation writes one dated edition per day
            into the "Sam's Podcast" OneDrive folder (RESEARCH_WORKFLOW.md
            there is the contract): <root>\\YYYY-MM-DD\\ holding one Markdown
            memo per topic plus manifest.json, written LAST. An edition is
            used only when its manifest parses, says status "complete", and
            every memo it lists exists and is nonempty. Each edition is used
            once (edition_date is the idempotency key); today's is preferred,
            yesterday's is taken if it landed after yesterday's run. No
            usable edition = a cases-only episode.

  CASES     Every open row of the Rocky Case Index whose "Sam's Podcast"
            column (any header containing both "sam" and "podcast") says
            yes, from that case folder's activity.jsonl for YESTERDAY (local
            midnight to midnight). Cases with no activity yesterday get no
            Claude call; the show names them as quiet in one line.

Cards: one Claude call per research memo and one per active case turns the
material into a card (topic: headline, developments, upcoming; case: status,
yesterday, next steps). The cards are the show notes, the archive copy, and
the ONLY source for the script, so what Sam hears matches the notes.

Script and audio: one Claude call writes a two-host script of about ten
minutes (kirsten_podcast's parser and length guard, a larger word budget);
ElevenLabs Text to Dialogue voices it (the same elevenlabs_api_key and
request chunking as the Kirsten podcast).

Feed: Transistor.fm, a PRIVATE show. The MP3 is uploaded
(GET /episodes/authorize_upload, PUT to the presigned URL), an episode is
created with the cards as its description and the script as its transcript,
then published. Sam subscribes once through a personal private-feed URL
(--subscribe EMAIL creates it and prints it). If publishing fails after the
episode was created, the next run publishes that episode instead of
uploading a second copy.

Files: <root>\\_Episodes\\YYYY-MM-DD.mp3, YYYY-MM-DD script.txt and
YYYY-MM-DD cards.html. state\\sams_podcast.json records each day's episode
and the edition it used, so a second run the same day does nothing unless
--force. A dry run writes cards and script to state\\sams_podcast_preview.*
and never publishes; add --audio to voice it (spends ElevenLabs credits).
--no-publish makes the real files but skips Transistor.

Config (config.json, secrets and paths):
    elevenlabs_api_key        shared with the Kirsten podcast; required
    transistor_api_key        Transistor Account > API key; required to publish
    sams_podcast_show_id      the private show's id or slug (--shows lists them)
    sams_podcast_root         the research folder; probed when unset
    sams_podcast_style        the style guide (default PODCAST_STYLE.md in the
                              research folder, written there on first run)
    sams_podcast_intro        intro music (default "Sam's Soundtrack" .wav,
                              .flac or .mp3 in the research folder)
    sams_podcast              false turns the whole job off

Everything else is in PODCAST_STYLE.md, read every run: a settings block at
the top (host1/2, voice1/2, listener, stability, intro_seconds,
intro_fade_seconds, intro_overlap_seconds, target_words, max_words,
audio_tags, model), which wins over config.json and falls back to the
built-in default for any bad value; "## Topic cards" and "## Case cards"
sections for the show notes; and every other section for the script.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

import requests

log = logging.getLogger("rocky.sams")

SHOW_NAME = "Sam's Podcast"
EPISODES_DIRNAME = "_Episodes"
_ONEDRIVE = "OneDrive - gejlaw.com"
# The Rocky laptop's own OneDrive holds the folder; on James's laptop it is
# rocky@'s share, named "<owner>'s files - <folder>".
_ROOT_CANDIDATES = [
    Path(_ONEDRIVE) / "Podcasts" / "Sam's Podcast",
    Path(_ONEDRIVE) / "Rocky's files - Sam's Podcast",
]

TRANSISTOR = "https://api.transistor.fm/v1"

TARGET_WORDS = 1400      # what the prompt asks for
MAX_WORDS = 1600         # code cap, about 10.7 minutes at 150 words a minute
MAX_SECONDS = 660

# The voices James picked for this show (10/23): Al and Sophia.
_DEFAULT_VOICES = ["Q1QcmfZPmFDVUWmzASdy", "LtPsVjX1k0Kl4StEMZPK"]
_DEFAULT_HOSTS = ["Al", "Sophia"]

# Pace (James, 10/23: "pretty peppy"). The dialogue API has no speed
# setting, so the energy comes from three places: stability 0.0 ("Creative",
# the most expressive; 0.5 is ElevenLabs' default, and a rejected value is
# retried without it), a few Eleven v3 audio tags the script may use, and
# the style guide's Pace section. sams_podcast_stability overrides.
DEFAULT_STABILITY = 0.0
AUDIO_TAGS = ("excited", "cheerfully", "laughs", "chuckles")


def _tag_re(tags=None):
    tags = AUDIO_TAGS if tags is None else tags
    if not tags:
        return re.compile(r"(?!x)x")          # matches nothing
    return re.compile(r"\[(" + "|".join(re.escape(t) for t in tags) + r")\]",
                      re.IGNORECASE)

INTRO_NAME = "Sam's Soundtrack"
_INTRO_EXTS = (".wav", ".flac", ".mp3")
INTRO_SECONDS = 0        # the whole track (James, 10/23); N = cut at N s
INTRO_FADE = 4.0         # a cut-short intro fades out over its last 4 s
INTRO_OVERLAP = 2.0      # and the hosts come in 2 seconds before it ends
_RATE = 44100            # the hosts' MP3 rate (OUTPUT_FORMAT mp3_44100_64)

# Sam Cowin (scowin@gallagherllp.com) works these cases and appears by name
# in their activity logs. Override with sams_podcast_listener.
_LISTENER = "Sam Cowin"

_MEMO_CAP = 12000        # characters of one research memo sent for its card
_TAG = "sams podcast"

TOPIC_CORE = """You turn one sports research memo into a card for Sam's Podcast, a daily audio show for {listener}, a lawyer at Gallagher LLP. The card is the show notes for this topic and the only source the hosts will read from.

Return ONLY a JSON object:
{
  "headline": "<one sentence>",
  "developments": ["<bullet>", ...],
  "upcoming": [{"date": "<YYYY-MM-DD or null>", "text": "<bullet>"}, ...],
  "angles": ["<bullet>", ...]
}

RULES
- Use only the memo. Do not add or calculate dates, scores, or names.
- "developments" are verified facts from the memo. Leave out anything the memo marks as rumor unless you say plainly it is unconfirmed.
- "upcoming" holds only games or events on or after TODAY. Each text leads with its date and time as the memo gives them, times Eastern ("Oct. 28, 8 p.m. Eastern — UIC exhibition, B1G+"). A conflict or uncertainty the memo flags stays flagged.
- "angles" are opinions for the hosts to discuss, not facts.
- No URLs, no markdown, no emojis inside the strings.

CARD STYLE (how many items, how long, what to favor)
{style}

Output the JSON object and nothing else."""

CASE_CORE = """You write one case's card for Sam's Podcast, a daily internal audio show for {listener}, a lawyer at Gallagher LLP. This is internal, so name parties, courts, opposing counsel, and Gallagher people where the sources do, and include internal next steps. {listener} is the listener: where a source names {listener} as the one who did something, write it as "you" ("you drafted the declaration"). Use "you" ONLY then. Work a source credits to someone else stays theirs by name; work no source attributes is "the team".

You receive the case's identity, the activity recorded on the case YESTERDAY (a line ending [by NAME] is that person's work), and background (the case overview and status memorandum, when they exist).

Return ONLY a JSON object:
{
  "status": "<one sentence>",
  "yesterday": [{"date": "<YYYY-MM-DD the development itself happened, or null>", "text": "<bullet>"}, ...],
  "next_steps": [{"date": "<YYYY-MM-DD of the date the bullet leads with, or null>", "text": "<bullet>"}, ...]
}

RULES
- "yesterday" holds only developments from yesterday's activity. Activity lines carry the day they were recorded; one can describe an older event, so check the event's own date and leave out anything before YESTERDAY.
- "next_steps" is in date order, soonest first (undated last). Every hearing or deadline on or after TODAY that the sources state belongs there. Never list a date before TODAY as upcoming: the memo may be months old. If nothing is pending, return [{"date": null, "text": "Nothing pending."}].
- Leave out case, docket, and inquiry numbers.
- Never calculate a date; use only dates the sources state. A contemplated, threatened, or conditional event stays pending. Do not invent facts.
- Never mention Rocky, AI, activity logs, case folders, file indexes, or documents being saved, renamed, or filed into folders.
- No markdown, no emojis inside the strings. Past tense for events.

CARD STYLE (how many items, how long, what to favor)
{style}

Output the JSON object and nothing else."""

# The prompts are two parts. The *_CORE prompts stay in code because the
# parser and the facts depend on them. Everything else is PODCAST_STYLE.md in
# the research folder, so James can tune the show without a new rocky.exe
# (10/24: "as many of the style and customizations to be in the .md"):
#   - a settings block at the top (hosts, voices, listener, stability, intro
#     timing, length, audio cues, model), which wins over config.json;
#   - "## Topic cards" and "## Case cards" sections, the card style;
#   - every other section, the script style.
# DEFAULT_STYLE seeds that file when it is missing and fills in whatever the
# file lacks or gets wrong. Placeholders {host1} {host2} {listener} {target}
# {max_words} are filled by plain replacement (not str.format), so other
# braces in the file are safe.
STYLE_FILENAME = "PODCAST_STYLE.md"
_STYLE_CAP = 12000

SCRIPT_CORE = """You write the script for today's episode of Sam's Podcast, a daily audio show for {listener}, a lawyer at Gallagher LLP. Two hosts present it: {host1} is speaker 1 and {host2} is speaker 2.

You receive the day's cards: sports TOPIC cards, and CASE cards for the matters Sam follows. Those cards are your only source. Follow the STYLE GUIDE below for the show's shape, tone, pacing, and how things are said. Where the style guide conflicts with these RULES, the RULES win.

Return ONLY a JSON object:
{"turns": [{"speaker": 1 or 2, "text": "<what that host says>"}, ...]}

RULES
- Use only facts in the cards. Do not add or calculate dates, scores, outcomes, or names. Keep anything pending, conditional, or unconfirmed exactly that.
- Add nothing from your own knowledge: no schedules, bye weeks, standings, records, history, or context the cards don't state. If a card gives no next game or date, say nothing about what's next for that team.
- A date before TODAY is past: never present it as upcoming.
- Cases are internal and the listener is {listener}, a colleague: name parties, courts and Gallagher people where the cards do, and keep the cards' "you" as "you" (that is {listener}'s own work); never turn anyone else's work into "you". The hosts do not predict how a case will go.
- If a section has no cards, do not mention it anywhere, including the preview.
- Never mention Rocky, AI, activity logs, cards, or research memos: the hosts just know the news.
- At most {max_words} words in total across all turns; anything longer is cut off.
- Spoken words only: no case, inquiry, or docket numbers, no citations, no URLs, no markdown, no stage directions, no emojis.
- {tag_rule}

STYLE GUIDE
{style}

Output the JSON object and nothing else."""

DEFAULT_STYLE = """---
host1: Al
voice1: Q1QcmfZPmFDVUWmzASdy
host2: Sophia
voice2: LtPsVjX1k0Kl4StEMZPK
listener: Sam Cowin
stability: 0.0
intro_file: Sam's Soundtrack.wav
intro_seconds: 0
intro_fade_seconds: 4
intro_overlap_seconds: 2
target_words: 1400
max_words: 1600
audio_tags: excited, cheerfully, laughs, chuckles
model: claude-opus-5-5
---

# Sam's Podcast — style guide

<!--
Rocky reads this file every morning when it makes the episode. Edit it
freely: the next episode follows it, with no new rocky.exe. Comments like
this one are notes for you and are never sent to Claude.

THE SETTINGS BLOCK (between the --- lines at the top). One "name: value"
per line. Rocky checks each value; a bad or missing one falls back to the
built-in default and is logged, so a typo can't stop the episode. These win
over config.json.
  host1, host2            the hosts' names (match them to the voices)
  voice1, voice2          ElevenLabs voice IDs
  listener                Sam's full name as the case logs write it; Sam's
                          own work is spoken as "you"
  stability               0 = most expressive (peppiest) to 1 = steadiest;
                          ElevenLabs' own default is 0.5; "default" sends none
  intro_file              the intro music: a .wav, .flac or .mp3 in this
                          folder (or a full path); none = no intro
  intro_seconds           how much of it plays; 0 = all of it, unfaded (use
                          the clip's length to play it all with a fade-out)
  intro_fade_seconds      fade-out when the intro is cut short
  intro_overlap_seconds   how early the hosts start before the music ends
  target_words            the length to aim for (about 150 words a minute)
  max_words               hard cap; anything longer is cut
  audio_tags              Eleven v3 delivery cues the script may use, comma
                          separated (e.g. excited, laughs, whispers), or none
  model                   the Claude model that writes cards and script

THE SECTIONS. "## Topic cards" and "## Case cards" style the show notes
(one card per sports memo and per active case). Every other section styles
the script. Rocky fills in {host1}, {host2}, {listener} and {target}.

Fixed rules live in Rocky's code and win over this file: only facts from
the day's research and case logs, no past date as upcoming, only Sam's own
work is "you", never mention Rocky or AI, no case numbers or URLs, and a
fact check on every script. Delete this file and Rocky writes a fresh copy
of the original on the next run.
-->

## The show

A warm, quick, conversational sports-and-docket morning show for Sam, who listens on the commute. {host1} and {host2} walk Sam through it. It is a daily show, weekends included.

## Shape

- **Opening.** {host1} opens: "Sam's Podcast", the day and date, and a one-line preview of the headlines.
- **Sports.** Each topic card in the order given, as a short back-and-forth: the headline, the key developments, then what's next and when. Times are Eastern; say "Eastern" once per segment. The hosts may discuss the card's angles briefly, as their take.
- **Cases.** Hand off clearly ("Now to the docket."). For each active case: what happened yesterday, then what comes next and when. Then the quiet cases in one turn: name them, nothing more. If there are no case cards, skip this segment.
- **Close.** One line on the next upcoming game and the next case date, then a sign-off ("see you tomorrow").

## Length

About {target} words in total (about ten minutes spoken). Budget roughly 60 words for the opening, 200 to 250 per sports topic, 100 to 150 per active case, 30 for the quiet cases, and 50 for the close. A busy day gets less detail per item, not more time.

## Pace

Peppy and upbeat: a quick morning show, not a newscast.

- Short, punchy sentences. One idea per sentence. No long wind-ups or recaps.
- Quick hand-offs: the other host jumps in with a reaction or the next fact ("Huge night." "And it gets better."). Most turns are one or two sentences.
- Energy in the words: strong verbs, a little enthusiasm for good news, a bit of banter between segments. Keep it warm, never goofy, and calm for anything serious on the docket.
- Delivery cues: open the show with [excited], and use a cue about once every four or five turns where it fits. Never on a case turn.

## Delivery

- Speaker turns alternate.
- The hosts' takes are conversation, not caveats: say a sample-size or not-yet-official caution at most once per topic.
- Write for the ear. Say dates as spoken ("November fourth"), and give every upcoming game its day or date.
- Say numbers the way a broadcaster does: "a hundred and five to ninety-six", "six hundred and eight yards", "a hundred sixteen point seven". Never digit by digit, like "one oh five".

## Topic cards

- **Headline:** one sentence, at most 25 words: the most important thing right now.
- **Developments:** 0 to 5, newest and most important first, one or two sentences each: results, scores, injuries, transactions, recruiting, coaching news. Label older context as older ("Last week, ...").
- **Upcoming:** 0 to 4 games or events, soonest first.
- **Angles:** 0 to 3 of the memo's discussion angles, in your own words.

## Case cards

- **Status:** one sentence, at most 25 words: where the case stands (forum, stage, next key date if known).
- **Yesterday:** 0 to 5 substantive developments, one or two sentences each: filings and orders, hearings, discovery, settlement talk, deadlines set or moved, significant correspondence, significant work completed.
- **Next steps:** 1 to 4 bullets: what happens next and who does it.
"""


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

# Opus 5.5 by default (10/23): on Sonnet 4.5, Rocky's default, the script
# kept crossing facts between cards and miscounting weekdays. Current models
# take an effort level instead of temperature, may think before answering
# (the reply's first block can be a thinking block), and can decline, so
# every call goes through _ask. sams_podcast_model can still name an older
# model; those get the old temperature call.
DEFAULT_MODEL = "claude-opus-5-5"
_LEGACY_MODELS = ("claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-5",
                  "claude-opus-4-1", "claude-sonnet-4-", "claude-3")


def _ask(client, model: str, system: str, messages: list[dict],
         max_tokens: int = 16000, effort: str = "medium",
         temperature: float = 0.0) -> str:
    """One Claude call; returns the reply's text."""
    kw = dict(model=model, max_tokens=max_tokens, system=system,
              messages=messages)
    if model.startswith(_LEGACY_MODELS):
        resp = client.messages.create(temperature=temperature, **kw)
    else:
        # fallbacks "default": if the model declines, the API re-runs the
        # request on a model chosen for the refusal category.
        resp = client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": effort}, **kw)
    if getattr(resp, "stop_reason", None) == "refusal":
        raise RuntimeError("the model declined the request")
    return "".join(b.text for b in resp.content
                   if getattr(b, "type", "text") == "text")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def enabled(config: dict) -> bool:
    return config.get("sams_podcast", True) is not False


def voice_settings(config: dict) -> dict | None:
    value = config.get("sams_podcast_stability", DEFAULT_STABILITY)
    return None if value is None else {"stability": float(value)}


def _protect(text: str, tags=None) -> str:
    return _tag_re(tags).sub(
        lambda m: "\u27e6" + m.group(1).lower() + "\u27e7", text)


def _restore(text: str) -> str:
    return re.sub("\u27e6([^\u27e7]+)\u27e7", r"[\1]", text)


def parse_turns(raw: str, tags=None) -> list[dict]:
    """kirsten_podcast.parse_turns, keeping the allowed audio tags (it strips
    every bracket, which the Kirsten podcast still wants)."""
    import kirsten_podcast as kp
    return [{**t, "text": _restore(t["text"])}
            for t in kp.parse_turns(_protect(raw, tags))]


def clean_text(text: str, tags=None) -> str:
    """A turn's text with markdown and stray brackets removed, allowed audio
    tags kept."""
    text = re.sub(r"\[[^\]]*\]|[*_#`]", "", _protect(text, tags))
    return _restore(re.sub(r"\s+", " ", text).strip())


def strip_tags(text: str, tags=None) -> str:
    """Audio tags out, for the transcript and anything read rather than
    heard."""
    text = re.sub(r"[ \t]{2,}", " ", _tag_re(tags).sub("", text))
    return re.sub(r" +([.,!?])", r"\1", text)


def _settings(config: dict) -> tuple[list[str], list[str]]:
    voices = list(config.get("sams_podcast_voices") or _DEFAULT_VOICES)
    hosts = list(config.get("sams_podcast_hosts") or _DEFAULT_HOSTS)
    if len(voices) < 2 or len(hosts) < 2:
        raise ValueError("sams_podcast_voices and sams_podcast_hosts each "
                         "need two entries")
    return voices[:2], hosts[:2]


def listener(config: dict) -> str:
    """The listener's full name, so the cards can turn their own work into
    "you" (the activity logs name Gallagher people in full)."""
    return str(config.get("sams_podcast_listener") or _LISTENER).strip()


def intro_path(config: dict, root: Path | None) -> Path | None:
    """The intro music: the style file's intro_file (a name in the research
    folder, or a full path; empty = no intro), else config
    sams_podcast_intro, else "Sam's Soundtrack" in the research folder (WAV,
    FLAC or MP3, lossless first)."""
    chosen = config.get("sams_podcast_intro_file")
    if chosen == "":
        log.info(f"[{_TAG}] intro_file is none; no intro music")
        return None
    if chosen:
        path = Path(chosen)
        if not path.is_absolute() and root:
            path = root / path
        if path.is_file():
            return path
        log.warning(f"[{_TAG}] intro_file {chosen} not found; using the "
                    f"usual intro instead")
    configured = config.get("sams_podcast_intro")
    if configured and Path(configured).is_file():
        return Path(configured)
    for ext in _INTRO_EXTS if root else ():
        path = root / f"{INTRO_NAME}{ext}"
        if path.is_file():
            return path
    if configured:
        log.warning(f"[{_TAG}] sams_podcast_intro {configured} not found; "
                    f"no intro music")
    return None


def _pcm(path: Path, rate: int = _RATE):
    """Mono 16-bit samples (array('h')) at rate, from MP3, WAV or FLAC."""
    import miniaudio
    return miniaudio.decode_file(
        str(path), output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=1, sample_rate=rate).samples


def mix_intro(music, speech, seconds: float, fade: float, overlap: float,
              rate: int = _RATE):
    """The first `seconds` of music (all of it if seconds <= 0), faded out
    over its last `fade` seconds, with the hosts coming in `overlap` seconds
    before it ends."""
    n = len(music) if seconds <= 0 else min(len(music), int(seconds * rate))
    f = min(int(fade * rate), n)
    out = music[:n]
    for i in range(n - f, n):
        out[i] = out[i] * (n - i) // f
    start = max(0, n - int(overlap * rate))
    head = min(len(speech), n - start)
    for j in range(head):
        out[start + j] = max(-32768, min(32767, out[start + j] + speech[j]))
    out.extend(speech[head:])
    return out


def encode_mp3(samples, rate: int = _RATE, kbps: int = 64) -> bytes:
    import lameenc
    enc = lameenc.Encoder()
    enc.set_bit_rate(kbps)
    enc.set_in_sample_rate(rate)
    enc.set_channels(1)
    enc.set_quality(2)
    return bytes(enc.encode(samples.tobytes()) + enc.flush())


def _pcm_parts(parts: list[bytes], rate: int = _RATE):
    """Each ElevenLabs part decoded on its own, then joined. Every part is a
    complete MP3 whose first frame states that part's length (a Xing/Info
    header), so decoding the parts glued together stops after part one:
    10/24's episode was 2:42 from a nine-minute script."""
    import miniaudio
    from array import array
    out = array("h")
    for data in parts:
        out.extend(miniaudio.decode(
            data, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
            sample_rate=rate).samples)
    return out


def finish_audio(config: dict, intro: Path | None, mp3: Path,
                 parts: list[bytes] | None = None,
                 words: int | None = None) -> float | None:
    """The episode's final audio: the hosts (from the ElevenLabs parts, each
    decoded separately), the intro music in front if there is one, all
    re-encoded as one 44.1 kHz stream, so podcast apps read the true length.
    Returns its length in seconds. If the hosts can't be decoded, mp3 stays
    as ElevenLabs returned it; if only the intro fails, it is left out."""
    try:
        speech = _pcm_parts(parts) if parts else _pcm(mp3)
    except Exception as e:
        log.warning(f"[{_TAG}] could not decode the hosts' audio ({e}); the "
                    f"episode goes out as ElevenLabs returned it, without "
                    f"intro music")
        return None
    out, seconds = speech, float(
        config.get("sams_podcast_intro_seconds", INTRO_SECONDS))
    if intro:
        try:
            # The whole track ends on its own; only a cut-short one fades.
            fade = float(config.get("sams_podcast_intro_fade", INTRO_FADE))
            overlap = float(config.get("sams_podcast_intro_overlap",
                                       INTRO_OVERLAP))
            out = mix_intro(_pcm(intro), speech, seconds,
                            fade if seconds > 0 else 0, overlap)
            log.info(f"[{_TAG}] intro music {intro.name} added "
                     f"({'whole track' if seconds <= 0 else f'{seconds:g}s'})")
        except Exception as e:
            log.warning(f"[{_TAG}] intro music FAILED ({e}); the episode goes "
                        f"out without it")
            out = speech
    try:
        mp3.write_bytes(encode_mp3(out))
    except Exception as e:
        log.warning(f"[{_TAG}] could not re-encode the episode ({e}); it goes "
                    f"out as ElevenLabs returned it")
        return None
    total = len(out) / _RATE
    hosts = len(speech) / _RATE
    log.info(f"[{_TAG}] episode audio {total / 60:.1f} min (hosts "
             f"{hosts / 60:.1f} min from {len(parts or [])} part(s))")
    if words and hosts < words / 150 * 60 * 0.6:
        log.warning(f"[{_TAG}] the hosts' audio is {hosts:.0f}s for a "
                    f"{words}-word script (about {words / 150:.0f} min "
                    f"expected): it may be cut short")
    return total


def add_intro(config: dict, intro: Path, mp3: Path,
              parts: list[bytes] | None = None) -> None:
    """finish_audio with intro music (kept for callers and tests)."""
    finish_audio(config, intro, mp3, parts)


def style_path(config: dict, root: Path | None) -> Path | None:
    configured = config.get("sams_podcast_style")
    if configured:
        return Path(configured)
    return root / STYLE_FILENAME if root else None


def _uncomment(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()


_BLOCK_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)


def split_settings(text: str) -> tuple[dict, str]:
    """The leading --- settings block as raw {name: value}, and the rest."""
    m = _BLOCK_RE.match(text.lstrip())
    if not m:
        return {}, text
    raw = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        name, value = line.split(":", 1)
        raw[name.strip().lower()] = value.strip().strip('"').strip("'").strip()
    return raw, text.lstrip()[m.end():]


def _num(value: str, lo: float, hi: float) -> float:
    n = float(value)
    if not lo <= n <= hi:
        raise ValueError(f"must be between {lo:g} and {hi:g}")
    return n


def _name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z .'\-]{0,39}", value):
        raise ValueError("must be a plain name")
    return value


def _voice(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9]{15,40}", value):
        raise ValueError("doesn't look like an ElevenLabs voice ID")
    return value


def _tags(value: str) -> list[str]:
    if value.lower() in ("none", "off", ""):
        return []
    tags = [t.strip().strip("[]").lower() for t in value.split(",")]
    tags = [t for t in tags if t]
    if len(tags) > 12 or not all(re.fullmatch(r"[a-z][a-z \-]{1,29}", t)
                                 for t in tags):
        raise ValueError("must be a comma-separated list of short words")
    return tags


def _model(value: str) -> str:
    if not re.fullmatch(r"claude-[a-z0-9.\-]+", value):
        raise ValueError("must be a Claude model id like claude-opus-5-5")
    return value


def _stability(value: str):
    if value.lower() in ("default", "none"):
        return None
    return _num(value, 0, 1)


def _intro_file(value: str) -> str:
    if value.lower() in ("none", "off"):
        return ""
    if not value.lower().endswith(_INTRO_EXTS) or "\n" in value:
        raise ValueError("must be a .wav, .flac or .mp3 file name, or none")
    return value


def _intro(value: str) -> float:
    return 0.0 if value.lower() in ("all", "whole") else _num(value, 0, 600)


# Settings-block name -> (config key, checker). host/voice 1 and 2 are
# combined into the two-entry config lists by apply_settings.
_SETTINGS = {
    "host1": ("host1", _name), "host2": ("host2", _name),
    "voice1": ("voice1", _voice), "voice2": ("voice2", _voice),
    "listener": ("sams_podcast_listener", _name),
    "stability": ("sams_podcast_stability", _stability),
    "intro_seconds": ("sams_podcast_intro_seconds", _intro),
    "intro_fade_seconds": ("sams_podcast_intro_fade",
                           lambda v: _num(v, 0, 30)),
    "intro_overlap_seconds": ("sams_podcast_intro_overlap",
                              lambda v: _num(v, 0, 30)),
    "target_words": ("sams_podcast_target_words",
                     lambda v: int(_num(v, 200, 3000))),
    "max_words": ("sams_podcast_max_words",
                  lambda v: int(_num(v, 250, 3500))),
    "audio_tags": ("sams_podcast_audio_tags", _tags),
    "model": ("sams_podcast_model", _model),
    "intro_file": ("sams_podcast_intro_file", _intro_file),
}


def parse_settings(raw: dict) -> dict:
    """Checked values from the settings block, keyed by config name. A bad or
    unknown entry is logged and skipped (the default or config.json value
    then stands), so a typo can't stop the episode."""
    out = {}
    for name, value in raw.items():
        if name not in _SETTINGS:
            log.warning(f"[{_TAG}] style file setting {name!r} is not one "
                        f"Rocky knows; ignored")
            continue
        key, check = _SETTINGS[name]
        try:
            out[key] = check(value)
        except (TypeError, ValueError) as e:
            log.warning(f"[{_TAG}] style file setting {name}: {value!r} "
                        f"{e}; using the default")
    return out


def apply_settings(config: dict, settings: dict) -> dict:
    """config with the style file's settings laid over it (the file wins)."""
    out = dict(config)
    voices, hosts = _settings(config)
    for i in (1, 2):
        if f"voice{i}" in settings:
            voices[i - 1] = settings[f"voice{i}"]
        if f"host{i}" in settings:
            hosts[i - 1] = settings[f"host{i}"]
    out["sams_podcast_voices"], out["sams_podcast_hosts"] = voices, hosts
    for key, value in settings.items():
        if key.startswith("sams_podcast_"):
            out[key] = value
    target, cap = word_limits(out)
    if cap < target:
        log.warning(f"[{_TAG}] max_words {cap} is under target_words "
                    f"{target}; using {target + 200}")
        out["sams_podcast_max_words"] = target + 200
    return out


def split_sections(text: str) -> dict:
    """{"script", "topic", "case"}: the "## Topic cards" and "## Case cards"
    sections, and everything else (the script style)."""
    parts = {"script": [], "topic": [], "case": []}
    where = "script"
    for line in text.splitlines():
        heading = re.match(r"##\s+(.+?)\s*$", line)
        if heading:
            title = heading.group(1).lower()
            where = ("topic" if title.startswith("topic card") else
                     "case" if title.startswith("case card") else "script")
            if where != "script":
                continue
        parts[where].append(line)
    return {k: "\n".join(v).strip() for k, v in parts.items()}


def _read_style_file(config: dict, root: Path | None) -> str:
    path = style_path(config, root)
    if path is None:
        log.info(f"[{_TAG}] no research folder; built-in style guide")
        return DEFAULT_STYLE
    try:
        text = path.read_text(encoding="utf-8-sig")
        log.info(f"[{_TAG}] style guide: {path}")
        return text
    except FileNotFoundError:
        if config.get("sams_podcast_style"):
            log.warning(f"[{_TAG}] sams_podcast_style {path} not found; "
                        f"built-in style guide")
        else:
            try:
                path.write_text(DEFAULT_STYLE, encoding="utf-8")
                log.info(f"[{_TAG}] wrote the starting style guide to {path} "
                         f"(edit it to change the show)")
            except OSError as e:
                log.warning(f"[{_TAG}] could not write {path}: {e}")
    except (OSError, UnicodeDecodeError) as e:
        log.warning(f"[{_TAG}] cannot read {path} ({e}); built-in style guide")
    return DEFAULT_STYLE


def load_style(config: dict, root: Path | None) -> dict:
    """PODCAST_STYLE.md read and split: {"settings", "script", "topic",
    "case"}. Notes in <!-- --> are dropped. Any part the file lacks (or
    leaves empty) comes from DEFAULT_STYLE."""
    raw, body = split_settings(_uncomment(_read_style_file(config, root)))
    default_raw, default_body = split_settings(_uncomment(DEFAULT_STYLE))
    sections, defaults = split_sections(body), split_sections(default_body)
    out = {"settings": parse_settings(raw)}
    for part in ("script", "topic", "case"):
        text = sections[part]
        if not text:
            if part == "script":
                log.warning(f"[{_TAG}] style guide has no script style; "
                            f"built-in one used")
            text = defaults[part]
        if len(text) > _STYLE_CAP:
            log.warning(f"[{_TAG}] {part} style is {len(text)} characters; "
                        f"using the first {_STYLE_CAP}")
            text = text[:_STYLE_CAP]
        out[part] = text
    return out


def style_guide(config: dict, root: Path | None) -> str:
    """The script part of the style guide (see load_style)."""
    return load_style(config, root)["script"]


def word_limits(config: dict) -> tuple[int, int]:
    return (int(config.get("sams_podcast_target_words") or TARGET_WORDS),
            int(config.get("sams_podcast_max_words") or MAX_WORDS))


def audio_tags(config: dict) -> list[str]:
    tags = config.get("sams_podcast_audio_tags")
    return list(AUDIO_TAGS) if tags is None else list(tags)


def fill(text: str, values: dict) -> str:
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def research_root(config: dict, home: Path | None = None) -> Path | None:
    """The research folder: config sams_podcast_root if it exists, else the
    first candidate under this profile's OneDrive that does."""
    configured = config.get("sams_podcast_root")
    if configured and Path(configured).is_dir():
        return Path(configured)
    home = home or Path.home()
    for rel in _ROOT_CANDIDATES:
        if (home / rel).is_dir():
            if configured:
                log.info(f"[{_TAG}] sams_podcast_root {configured} not found; "
                         f"using {home / rel}")
            return home / rel
    return None


# ---------------------------------------------------------------------------
# Research editions
# ---------------------------------------------------------------------------

_DATE_DIR = re.compile(r"\d{4}-\d{2}-\d{2}")


def strip_front_matter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def load_edition(folder: Path) -> dict | None:
    """A complete edition, or None (with the reason logged). The research
    contract: manifest.json is written last; trust only status complete with
    every listed memo present and nonempty."""
    manifest = folder / "manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.info(f"[{_TAG}] {folder.name}: no manifest yet (still writing?)")
        return None
    except (OSError, ValueError) as e:
        log.warning(f"[{_TAG}] {folder.name}: unreadable manifest ({e})")
        return None
    if not isinstance(data, dict) or data.get("status") != "complete":
        log.info(f"[{_TAG}] {folder.name}: manifest not complete")
        return None
    memos = []
    for entry in data.get("memos") or []:
        if not isinstance(entry, dict) or not entry.get("file"):
            continue
        path = folder / str(entry["file"])
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError as e:
            log.warning(f"[{_TAG}] {folder.name}: cannot read {path.name} "
                        f"({e}); edition skipped")
            return None
        if not text:
            log.warning(f"[{_TAG}] {folder.name}: {path.name} is empty; "
                        f"edition skipped")
            return None
        memos.append({"topic": str(entry.get("topic") or path.stem),
                      "file": path.name, "text": strip_front_matter(text)})
    if not memos:
        log.warning(f"[{_TAG}] {folder.name}: manifest lists no memos")
        return None
    return {"date": str(data.get("edition_date") or folder.name),
            "folder": folder, "memos": memos}


def pick_edition(root: Path, today: datetime, used: set[str]) -> dict | None:
    """Today's edition, else yesterday's if it was never used. Older ones are
    stale news and are left alone."""
    for day in (today, today - timedelta(days=1)):
        name = day.strftime("%Y-%m-%d")
        if name in used:
            continue
        folder = root / name
        if folder.is_dir():
            edition = load_edition(folder)
            if edition:
                return edition
    return None


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

def sam_column(cases: list[dict]) -> str | None:
    """The case-index header that holds the Sam's Podcast flag."""
    for case in cases:
        for key in case:
            low = str(key or "").lower()
            if "sam" in low and "podcast" in low:
                return key
    return None


def yesterday_window(now: datetime) -> tuple[datetime, datetime]:
    """Local midnight to midnight, as aware datetimes."""
    now = now.astimezone()
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return end - timedelta(days=1), end


def attributed_lines(events: list[dict]) -> list[str]:
    """kirsten_digest.activity_lines, plus who did each item. The card turns
    the listener's own work into "you", so it has to know whose work each
    line is (activity_lines leaves the actor out: the client report never
    names firm people)."""
    from kirsten_digest import activity_lines
    lines = []
    for ev in sorted(events, key=lambda e: str(e.get("timestamp") or "")):
        actor = re.sub(r"\s*\(.*?\)", "", str(ev.get("actor") or "")).strip()
        by = f" [by {actor}]" if actor and actor.lower() != "rocky" else ""
        lines += [line + by for line in activity_lines([ev])]
    return lines


def events_between(events: list[dict], start: datetime, end: datetime
                   ) -> list[dict]:
    import rocky
    out = []
    for ev in events:
        ts = rocky._parse_iso(ev.get("timestamp"))
        if ts and start <= ts < end:
            out.append(ev)
    return out


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

def _dated_texts(items, today: str, drop_before: str | None = None
                 ) -> list[str]:
    """Bullet texts from [{date, text}] (or plain strings), dropping any dated
    before drop_before (default today)."""
    floor = drop_before or today
    out = []
    for item in items or []:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            date = str(item.get("date") or "")[:10]
            if floor and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) \
                    and date < floor:
                log.info(f"[{_TAG}] dropped item dated {date}: {text[:80]}")
                continue
        else:
            text = str(item).strip()
        if text:
            out.append(text)
    return out


def _strings(items, cap: int) -> list[str]:
    return [str(i).strip() for i in (items or []) if str(i).strip()][:cap]


def card_prompt(core: str, style: str | None, part: str,
                listener: str) -> str:
    if style is None:
        style = load_style({}, None)[part]
    values = {"listener": listener}
    return fill(core, values).replace("{style}", fill(style, values))


def topic_card(client, model: str, memo: dict, today: str,
               style: str | None = None, listener: str = _LISTENER) -> dict:
    """One Claude call. {kind, title, headline, developments, upcoming,
    angles} or {kind, title, error}."""
    from kirsten_digest import _parse_json
    card = {"kind": "topic", "title": memo["topic"]}
    prompt = (f"TODAY: {today}\nTOPIC: {memo['topic']}\n\nMEMO:\n"
              f"{memo['text'][:_MEMO_CAP]}\n\nWrite the JSON card.")
    try:
        data = _parse_json(_ask(client, model,
                                card_prompt(TOPIC_CORE, style, "topic",
                                            listener),
                                [{"role": "user", "content": prompt}],
                                max_tokens=8000))
    except Exception as e:
        return {**card, "error": str(e)}
    if not data:
        return {**card, "error": "the card came back unreadable"}
    return {**card,
            "headline": str(data.get("headline") or "").strip(),
            "developments": _strings(data.get("developments"), 8),
            "upcoming": _dated_texts(data.get("upcoming"), today[:10])[:6],
            "angles": _strings(data.get("angles"), 5)}


def case_card(client, model: str, case: dict, lines: list[str], memo: str,
              background: str, today: str, yesterday: str,
              listener: str = "Sam", style: str | None = None) -> dict:
    """One Claude call for a case with activity yesterday."""
    from kirsten_digest import _parse_json
    card = {"kind": "case", "title": str(case.get("File Name") or
                                         case.get("RRID#") or "").strip(),
            "subtitle": str(case.get("Description") or "").strip()}
    prompt = f"""TODAY: {today}
YESTERDAY: {yesterday}

CASE
Name: {case.get('File Name')}
Client: {case.get('Client')}
Description: {case.get('Description') or '(none)'}

ACTIVITY YESTERDAY ({len(lines)} items):
{chr(10).join(lines)}

CASE OVERVIEW (background):
{background or '(none)'}

CASE STATUS MEMORANDUM (background, may be out of date):
{memo or '(no memo on file)'}

Write the JSON card."""
    try:
        data = _parse_json(_ask(client, model,
                                card_prompt(CASE_CORE, style, "case",
                                            listener),
                                [{"role": "user", "content": prompt}],
                                max_tokens=8000))
    except Exception as e:
        return {**card, "error": str(e)}
    if not data:
        return {**card, "error": "the card came back unreadable"}
    return {**card,
            "status": str(data.get("status") or "").strip(),
            "yesterday": _dated_texts(data.get("yesterday"), today[:10],
                                      drop_before=yesterday[:10])[:8],
            "next_steps": (_dated_texts(data.get("next_steps"), today[:10])
                           or ["Nothing pending."])[:6]}


def quiet_card(case: dict) -> dict:
    return {"kind": "case", "quiet": True,
            "title": str(case.get("File Name") or case.get("RRID#") or "").strip(),
            "subtitle": str(case.get("Description") or "").strip()}


def collect_cases(config: dict, client, model: str, now: datetime,
                  rrids: list[str] | None = None) -> tuple[list[dict], str]:
    """Case cards for every flagged open case (quiet ones without a Claude
    call). Returns (cards, note) where note explains an empty list."""
    import rocky
    from kirsten_digest import find_folder, select_cases

    # rocky.exe runs rocky.py as __main__, so this "import rocky" is a second
    # copy whose paths still hold the dev-laptop default (10/19 Kirsten
    # Digest). Apply the config to it.
    rocky._init_cases_paths(config)
    cases = rocky.load_case_index()
    if not cases:
        return [], "case index unreadable (see warning above)"
    col = sam_column(cases)
    if not col:
        return [], ("no \"Sam's Podcast\" column in the Rocky Case Index "
                    "(add it with yes/no per case)")
    selected = select_cases(cases, rrids, col=col)
    if not selected:
        return [], "no open case is marked yes for Sam's Podcast"

    start, end = yesterday_window(now)
    today_s = now.strftime("%Y-%m-%d (%A)")
    yesterday_s = start.strftime("%Y-%m-%d (%A)")
    cards = []
    for case in selected:
        rrid = str(case.get("RRID#") or "").upper()
        folder = find_folder(rocky.ROCKY_CASES_ROOT, case)
        if folder is None:
            log.warning(f"[{_TAG}] {rrid}: no case folder found")
            cards.append({**quiet_card(case), "quiet": False,
                          "error": "no case folder found under Rocky Cases"})
            continue
        events = events_between(rocky._read_activity_since(folder, start),
                                start, end)
        lines = attributed_lines(events)
        if not lines:
            cards.append(quiet_card(case))
            continue
        card = case_card(client, model, case, lines,
                         rocky._extract_status_memo_text(folder),
                         rocky._read_case_digest_instructions(folder),
                         today_s, yesterday_s, listener(config),
                         config.get("_sams_case_style"))
        if card.get("error"):
            log.warning(f"[{_TAG}] {rrid}: {card['error']}")
        log.info(f"[{_TAG}] {rrid} {folder.name}: {len(lines)} activity "
                 f"line(s) yesterday")
        cards.append(card)
    # Active cases first, quiet ones last.
    cards.sort(key=lambda c: (bool(c.get("quiet")), c["title"].lower()))
    return cards, "ok"


# ---------------------------------------------------------------------------
# Script
# ---------------------------------------------------------------------------

def cards_text(topics: list[dict], cases: list[dict]) -> str:
    """The cards, plain, for the script prompt. Failed cards are left out."""
    blocks = []
    for c in topics:
        if c.get("error"):
            continue
        lines = [f"TOPIC: {c['title']}", f"Headline: {c.get('headline') or ''}",
                 "Developments:"]
        lines += [f"- {b}" for b in c.get("developments") or []] or ["- (none)"]
        lines.append("Upcoming:")
        lines += [f"- {b}" for b in c.get("upcoming") or []] or ["- (none)"]
        if c.get("angles"):
            lines.append("Angles (opinion, for discussion):")
            lines += [f"- {b}" for b in c["angles"]]
        blocks.append("\n".join(lines))
    active = [c for c in cases if not c.get("quiet") and not c.get("error")]
    for c in active:
        lines = [f"CASE: {c['title']}" + (f" — {c['subtitle']}"
                                          if c.get("subtitle") else ""),
                 f"Status: {c.get('status') or ''}", "Yesterday:"]
        lines += [f"- {b}" for b in c.get("yesterday") or []] or \
            ["- No significant developments."]
        lines.append("Next steps:")
        lines += [f"- {b}" for b in c.get("next_steps") or []]
        blocks.append("\n".join(lines))
    quiet = [c["title"] for c in cases if c.get("quiet")]
    if quiet:
        # One per line: case names carry commas ("Bulgin, Alexander"), and a
        # comma-joined list read as "Alexander Bulgin's DC AG fees matter".
        blocks.append("QUIET CASES (no activity yesterday; each line is one "
                      "separate case):\n" + "\n".join(f"- {q}" for q in quiet))
    return "\n\n".join(blocks)


def calendar(day_label: str, days: int = 14, back: int = 10) -> str:
    """Weekdays from `back` days ago to two weeks ahead. Models miscount
    weekdays (10/23: the fact check "corrected" Wednesday Oct. 28 to Tuesday,
    and Opus called Sunday Oct. 18 "last Saturday"), so the prompts look them
    up instead."""
    try:
        today = datetime.strptime(day_label[:10], "%Y-%m-%d")
    except ValueError:
        today = datetime.now()
    return "CALENDAR (use it for every weekday, past or upcoming): " + "; ".join(
        f"{d.strftime('%a %b')} {d.day}" + (" (TODAY)" if i == 0 else "")
        for i, d in ((i, today + timedelta(days=i))
                     for i in range(-back, days)))


def tag_rule(tags) -> str:
    if not tags:
        return "No bracketed text at all."
    cues = " ".join(f"[{t}]" for t in tags)
    return (f"The only brackets allowed are these delivery cues, placed right "
            f"before the words they color: {cues}. Use them as the style "
            f"guide says. No other bracketed text.")


def script_prompt(style: str, hosts: list[str], listener: str,
                  target: int = TARGET_WORDS, max_words: int = MAX_WORDS,
                  tags=None) -> str:
    values = {"host1": hosts[0], "host2": hosts[1], "listener": listener,
              "target": f"{target:,}", "max_words": f"{max_words:,}",
              "tag_rule": tag_rule(AUDIO_TAGS if tags is None else tags)}
    # The style is filled on its own and dropped in last, so a placeholder
    # typed into the file can't pull the rules in twice.
    return fill(SCRIPT_CORE, values).replace("{style}", fill(style, values))


def write_script(client, model: str, material: str, day_label: str,
                 hosts: list[str], listener: str = "Sam",
                 style: str | None = None, target: int = TARGET_WORDS,
                 max_words: int = MAX_WORDS, tags=None) -> list[dict]:
    """One Claude call, plus one shorten retry if it runs long."""
    import kirsten_podcast as kp
    if not material:
        return []
    system = script_prompt(load_style({}, None)["script"] if style is None
                           else style, hosts, listener, target, max_words,
                           tags)
    if "CASE: " not in material and "QUIET CASES" not in material:
        material += ("\n\n(No case cards today: no docket segment, and do not "
                     "mention the docket or cases at all.)")
    elif "TOPIC: " not in material:
        material += ("\n\n(No sports cards today: no sports segment, and do "
                     "not mention sports at all.)")
    messages = [{"role": "user", "content":
                 f"TODAY: {day_label}\n{calendar(day_label)}\n\n"
                 f"CARDS:\n\n{material}\n\nWrite the JSON script."}]
    turns: list[dict] = []
    for _ in range(2):
        raw = _ask(client, model, system, messages, max_tokens=16000,
                   temperature=0.4)
        turns = parse_turns(raw, tags)
        if not turns:
            raise ValueError("the podcast script came back unreadable")
        words = kp.word_count(turns)
        if words <= max_words:
            break
        log.info(f"[{_TAG}] script ran {words} words; asking for a shorter one")
        messages += [{"role": "assistant", "content": raw},
                     {"role": "user", "content":
                      f"That is {words} words. Cut it to at most "
                      f"{target} words: less detail per item, same "
                      f"shape. Return the JSON only."}]
    turns = kp.fit(turns, max_words, tag=_TAG)
    return check_script(client, model, material, turns, day_label, tags)


CHECK_PROMPT = """You fact-check the script of a daily two-host audio show against the cards it was written from. The cards are the only source of truth.

You receive TODAY, a CALENDAR, the CARDS, and the SCRIPT as numbered turns.

Check every turn, one claim at a time, including the opening preview and the close, which summarize several cards at once and are where facts get crossed. For each team, case, or person a turn names, confirm the claim is on THAT card.

Find every turn that states something the cards do not support: a fact, date, score, opponent, schedule, bye week, record, or name not in the cards; a fact moved to the wrong team, case, or person (for example one team's game credited to another); a past date presented as upcoming; or something pending or unconfirmed presented as done. A weekday is wrong only if the CALENDAR says so: never change one from your own reckoning. Opinions and discussion of a card's angles are fine. Delivery cues in brackets ([excited], [laughs], ...) are not claims: leave them. Wording and style are not your concern.

Return ONLY a JSON object:
{"fixes": [{"turn": <number>, "problem": "<short reason>", "text": "<the whole turn rewritten to say only what the cards support, same speaker voice, or empty to drop the turn>"}]}

Return {"fixes": []} if every turn is supported. Output the JSON object and nothing else."""


def check_script(client, model: str, material: str, turns: list[dict],
                 day_label: str, tags=None) -> list[dict]:
    """One more Claude call: every turn checked against the cards, and any
    unsupported or misattributed claim rewritten (10/23 dry runs: an invented
    bye week, and Illinois' Oregon game credited to the Bears). A failed
    check keeps the script as written."""
    from kirsten_digest import _parse_json
    numbered = "\n".join(f"{i}. [speaker {t['speaker']}] {t['text']}"
                         for i, t in enumerate(turns, 1))
    try:
        raw = _ask(
            client, model, CHECK_PROMPT, effort="high", max_tokens=16000,
            messages=[{"role": "user", "content":
                       f"TODAY: {day_label}\n{calendar(day_label)}\n\n"
                       f"CARDS:\n\n{material}\n\n"
                       f"SCRIPT:\n{numbered}\n\nReturn the JSON."}])
        data = _parse_json(raw)
    except Exception as e:
        log.warning(f"[{_TAG}] fact check FAILED ({e}); script kept as written")
        return turns
    if not data:
        log.warning(f"[{_TAG}] fact check came back unreadable; script kept "
                    f"as written")
        return turns
    out = [dict(t) for t in turns]
    for fix in data.get("fixes") or []:
        try:
            i = int(fix.get("turn")) - 1
        except (TypeError, ValueError):
            continue
        if not 0 <= i < len(out):
            continue
        text = clean_text(str(fix.get("text") or ""), tags)
        log.info(f"[{_TAG}] fact check, turn {i + 1} "
                 f"({fix.get('problem') or 'unsupported'}): "
                 f"{out[i]['text'][:100]!r} -> {text[:100]!r}")
        out[i]["text"] = text
    return [t for t in out if t["text"]]


# ---------------------------------------------------------------------------
# Show notes (the cards as simple HTML: podcast apps drop styling)
# ---------------------------------------------------------------------------

def _ul(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{escape(i)}</li>" for i in items) + "</ul>" \
        if items else ""


def render_notes(topics: list[dict], cases: list[dict]) -> str:
    parts = []
    if topics:
        parts.append("<h2>Sports</h2>")
    for c in topics:
        parts.append(f"<h3>{escape(c['title'])}</h3>")
        if c.get("error"):
            parts.append(f"<p><em>No card ({escape(c['error'])}).</em></p>")
            continue
        if c.get("headline"):
            parts.append(f"<p>{escape(c['headline'])}</p>")
        parts.append(_ul(c.get("developments") or []))
        if c.get("upcoming"):
            parts.append("<p><strong>Upcoming</strong></p>"
                         + _ul(c["upcoming"]))
    active = [c for c in cases if not c.get("quiet")]
    quiet = [c["title"] for c in cases if c.get("quiet")]
    if cases:
        parts.append("<h2>Cases</h2>")
    for c in active:
        parts.append(f"<h3>{escape(c['title'])}</h3>")
        if c.get("error"):
            parts.append(f"<p><em>No card ({escape(c['error'])}).</em></p>")
            continue
        if c.get("status"):
            parts.append(f"<p><strong>Status:</strong> {escape(c['status'])}</p>")
        parts.append("<p><strong>Yesterday</strong></p>"
                     + (_ul(c.get("yesterday") or [])
                        or "<p>No significant developments.</p>"))
        parts.append("<p><strong>Next steps</strong></p>"
                     + _ul(c.get("next_steps") or []))
    if quiet:
        parts.append(f"<p><strong>Quiet yesterday:</strong> "
                     f"{escape(', '.join(quiet))}</p>")
    return "\n".join(p for p in parts if p)


def notes_page(notes: str, title: str) -> str:
    return (f'<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
            f'<title>{escape(title)}</title></head><body style="font-family:'
            f'Segoe UI,Helvetica,Arial,sans-serif;max-width:680px;margin:24px '
            f'auto;line-height:1.5;color:#1f2933;"><h1>{escape(title)}</h1>\n'
            f'{notes}\n</body></html>\n')


# ---------------------------------------------------------------------------
# Transistor.fm
# ---------------------------------------------------------------------------

def _check(resp, what: str) -> dict:
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Transistor {what}: HTTP {resp.status_code} "
                           f"{resp.text[:300]}")
    try:
        return resp.json() if resp.content else {}
    except ValueError:
        return {}


def upload_audio(api_key: str, path: Path) -> str:
    """Upload the MP3; returns the audio_url for the episode."""
    head = {"x-api-key": api_key}
    data = _check(requests.get(f"{TRANSISTOR}/episodes/authorize_upload",
                               params={"filename": path.name}, headers=head,
                               timeout=60), "authorize_upload")
    att = data["data"]["attributes"]
    _check(requests.put(att["upload_url"], data=path.read_bytes(),
                        headers={"Content-Type": att.get("content_type")
                                 or "audio/mpeg"}, timeout=600),
           "audio upload")
    return att["audio_url"]


def create_episode(api_key: str, show_id: str, title: str, summary: str,
                   description: str, audio_url: str, transcript: str) -> str:
    """A draft episode; returns its id."""
    data = _check(requests.post(
        f"{TRANSISTOR}/episodes", headers={"x-api-key": api_key}, timeout=60,
        data={"episode[show_id]": show_id, "episode[title]": title,
              "episode[summary]": summary,
              "episode[description]": description,
              "episode[audio_url]": audio_url,
              "episode[transcript_text]": transcript,
              "episode[type]": "full"}), "create episode")
    return str(data["data"]["id"])


def publish_episode(api_key: str, episode_id: str) -> None:
    _check(requests.patch(f"{TRANSISTOR}/episodes/{episode_id}/publish",
                          headers={"x-api-key": api_key}, timeout=60,
                          data={"episode[status]": "published"}),
           "publish")


def list_shows(api_key: str) -> list[dict]:
    data = _check(requests.get(f"{TRANSISTOR}/shows", timeout=60,
                               headers={"x-api-key": api_key},
                               params={"pagination[per]": 50}), "list shows")
    return data.get("data") or []


def subscribe(api_key: str, show_id: str, email: str) -> str | None:
    """Add a private-feed subscriber; returns their feed URL."""
    head = {"x-api-key": api_key}
    data = _check(requests.post(f"{TRANSISTOR}/subscribers", headers=head,
                                timeout=60, data={"show_id": show_id,
                                                  "email": email}),
                  "add subscriber")
    sub_id = data["data"]["id"]
    data = _check(requests.get(f"{TRANSISTOR}/subscribers/{sub_id}",
                               headers=head, timeout=60), "get subscriber")
    return (data.get("data") or {}).get("attributes", {}).get("feed_url")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def _state_path(data_dir: Path) -> Path:
    return data_dir / "state" / "sams_podcast.json"


def _read_state(data_dir: Path) -> dict:
    try:
        return json.loads(_state_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(data_dir: Path, state: dict) -> None:
    episodes = state.get("episodes") or {}
    state["episodes"] = dict(sorted(episodes.items())[-90:])
    try:
        path = _state_path(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning(f"[{_TAG}] could not record state: {e}")


def day_label(now: datetime) -> str:
    return f"{now.strftime('%A, %B')} {now.day}, {now.year}"


def _feed_cli(config: dict, args: list[str]) -> None:
    key = config.get("transistor_api_key")
    if not key:
        log.error(f"[{_TAG}] transistor_api_key is not set in config.json")
        return
    if "--shows" in args:
        for show in list_shows(key):
            attrs = show.get("attributes") or {}
            log.info(f"[{_TAG}] show id {show.get('id')}: {attrs.get('title')!r}"
                     f" (private: {attrs.get('private_show', attrs.get('private'))})")
        return
    i = args.index("--subscribe")
    email = args[i + 1] if i + 1 < len(args) else ""
    show_id = config.get("sams_podcast_show_id")
    if "@" not in email or not show_id:
        log.error(f"[{_TAG}] usage: --sams-podcast --subscribe EMAIL "
                  f"(and sams_podcast_show_id set)")
        return
    url = subscribe(key, show_id, email) or "(see the Transistor dashboard)"
    log.info(f"[{_TAG}] {email} subscribed. Private feed: {url}")


def run_cli(config: dict, data_dir: Path) -> None:
    import rocky

    args = sys.argv[1:]
    if "--shows" in args or "--subscribe" in args:
        _feed_cli(config, args)
        return
    if not enabled(config):
        log.info(f"[{_TAG}] off (sams_podcast false)")
        return
    dry_run = "--dry-run" in args
    force = "--force" in args
    no_publish = "--no-publish" in args
    rrids = [a.upper() for a in args if rocky.RRID_PATTERN.fullmatch(a)]

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    state = _read_state(data_dir)
    episodes = state.setdefault("episodes", {})
    mine = episodes.get(today) or {}

    log.info("=" * 60)
    log.info(f"Rocky — {SHOW_NAME}, {day_label(now)}"
             f"{' — DRY RUN' if dry_run else ''}")
    log.info("=" * 60)

    # An episode created but not published last time: publish that one.
    if not dry_run and not force and mine.get("episode_id") \
            and not mine.get("published") and not no_publish \
            and config.get("transistor_api_key"):
        try:
            publish_episode(config["transistor_api_key"], mine["episode_id"])
            mine["published"] = True
            _write_state(data_dir, state)
            log.info(f"[{_TAG}] published episode {mine['episode_id']} "
                     f"(created on the earlier run)")
        except Exception as e:
            log.warning(f"[{_TAG}] publish retry FAILED: {e}")
        return
    if not dry_run and not force and mine.get("published"):
        log.info(f"[{_TAG}] today's episode is already published; --force "
                 f"to make another")
        return

    root = research_root(config)
    # PODCAST_STYLE.md: its settings block wins over config.json, and its
    # card sections ride along for collect_cases.
    style = load_style(config, root)
    config = apply_settings(config, style["settings"])
    config["_sams_case_style"] = style["case"]
    if style["settings"]:
        log.info(f"[{_TAG}] style file settings: "
                 f"{', '.join(sorted(style['settings']))}")

    from anthropic import Anthropic
    client = Anthropic(api_key=config["anthropic_api_key"])
    model = config.get("sams_podcast_model") or DEFAULT_MODEL
    today_s = now.strftime("%Y-%m-%d (%A)")
    target, max_words = word_limits(config)
    tags = audio_tags(config)
    used = {e.get("edition") for d, e in episodes.items()
            if e.get("edition") and not (force and d == today)}
    edition = None
    if root is None:
        log.warning(f"[{_TAG}] research folder not found (set "
                    f"sams_podcast_root); cases only")
    else:
        edition = pick_edition(root, now, used)
        if edition is None:
            log.warning(f"[{_TAG}] no complete, unused research edition for "
                        f"today or yesterday under {root}; cases only")
    topics = [topic_card(client, model, m, today_s, style["topic"],
                         listener(config))
              for m in (edition["memos"] if edition else [])]
    for c in topics:
        if c.get("error"):
            log.warning(f"[{_TAG}] topic {c['title']}: {c['error']}")

    cases, note = collect_cases(config, client, model, now, rrids)
    if note != "ok":
        log.warning(f"[{_TAG}] no case segment: {note}")
    active = [c for c in cases if not c.get("quiet") and not c.get("error")]
    good_topics = [c for c in topics if not c.get("error")]
    if not good_topics and not active:
        log.warning(f"[{_TAG}] nothing to report today (no research cards, no "
                    f"case activity); no episode")
        return

    title = f"{SHOW_NAME} — {day_label(now)}"
    notes = render_notes(topics, cases)
    voices, hosts = _settings(config)
    turns = write_script(client, model, cards_text(topics, cases),
                         today_s, hosts, listener(config), style["script"],
                         target, max_words, tags)
    if not turns:
        log.warning(f"[{_TAG}] the script came back empty; no episode")
        return
    import kirsten_podcast as kp
    script = kp.script_text(turns, hosts)
    log.info(f"[{_TAG}] script: {len(turns)} turns, {kp.word_count(turns)} "
             f"words; {len(good_topics)} topic(s), {len(active)} active "
             f"case(s), {sum(1 for c in cases if c.get('quiet'))} quiet")

    if dry_run:
        out = data_dir / "state"
        out.mkdir(parents=True, exist_ok=True)
        (out / "sams_podcast_preview.html").write_text(
            notes_page(notes, title), encoding="utf-8")
        (out / "sams_podcast_preview.txt").write_text(script, encoding="utf-8")
        mp3 = None
        if "--audio" in args:
            if not config.get("elevenlabs_api_key"):
                log.warning(f"[{_TAG}] --audio needs elevenlabs_api_key")
            else:
                mp3 = out / "sams_podcast_preview.mp3"
                parts: list[bytes] = []
                kp.synthesize(config["elevenlabs_api_key"], turns, voices,
                              mp3, int(max_words * 0.4) + 20, tag=_TAG,
                              settings=voice_settings(config), parts=parts)
                finish_audio(config, intro_path(config, root), mp3, parts,
                             kp.word_count(turns))
        log.info(f"[{_TAG}] DRY RUN — would publish {title!r}. Preview: "
                 f"{out / 'sams_podcast_preview.html'}, script "
                 f"{out / 'sams_podcast_preview.txt'}"
                 + (f", audio {mp3}" if mp3 else ""))
        return

    if not config.get("elevenlabs_api_key"):
        log.warning(f"[{_TAG}] elevenlabs_api_key is not set; no audio, no "
                    f"episode")
        return
    archive = (root or data_dir / "sams_podcast") / EPISODES_DIRNAME
    archive.mkdir(parents=True, exist_ok=True)
    (archive / f"{today} cards.html").write_text(notes_page(notes, title),
                                                 encoding="utf-8")
    (archive / f"{today} script.txt").write_text(script, encoding="utf-8")
    mp3 = archive / f"{today}.mp3"
    parts: list[bytes] = []
    kp.synthesize(config["elevenlabs_api_key"], turns, voices, mp3,
                  int(max_words * 0.4) + 20, tag=_TAG,
                  settings=voice_settings(config), parts=parts)
    finish_audio(config, intro_path(config, root), mp3, parts,
                 kp.word_count(turns))

    record = {"edition": edition["date"] if edition else None,
              "title": title, "mp3": str(mp3),
              "cases": [c["title"] for c in cases if not c.get("quiet")],
              "published": False}
    episodes[today] = record
    _write_state(data_dir, state)

    key, show_id = config.get("transistor_api_key"), \
        config.get("sams_podcast_show_id")
    if no_publish:
        log.info(f"[{_TAG}] --no-publish: episode saved to {mp3}, not uploaded")
        return
    if not key or not show_id:
        log.warning(f"[{_TAG}] transistor_api_key / sams_podcast_show_id not "
                    f"set; episode saved to {mp3}, not published")
        return
    summary = "; ".join([c["title"] for c in good_topics]
                        + [c["title"] for c in active])[:250]
    try:
        audio_url = upload_audio(key, mp3)
        record["episode_id"] = create_episode(key, str(show_id), title,
                                              summary, notes, audio_url,
                                              strip_tags(script, tags))
        _write_state(data_dir, state)
        publish_episode(key, record["episode_id"])
    except Exception as e:
        log.warning(f"[{_TAG}] publish FAILED: {e}. The episode is saved at "
                    f"{mp3}"
                    + ("; rerun to publish the draft episode already created"
                       if record.get("episode_id") else ""))
        return
    record["published"] = True
    _write_state(data_dir, state)
    log.info(f"[{_TAG}] published {title!r} to Transistor (episode "
             f"{record['episode_id']})")
