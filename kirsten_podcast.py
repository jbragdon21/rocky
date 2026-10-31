"""
Kirsten Podcast — "GC Updates", the Kirsten Digest as a two-host audio update
==============================================================================

Runs inside --kirsten-digest (see kirsten_digest.run_cli). After the case
cards are written, one Claude call rewrites them as a news-style update
between two hosts, at most about five minutes of speech. ElevenLabs Text to
Dialogue (eleven_v3) voices it, and the episode is published to GC
Updates (James's name for the show, 10/31), a PRIVATE Transistor.fm show that
Kirsten subscribes to (James, 10/31: it was attached to the draft as an MP3
until then). The email then says the audio version is live in her feed.

Every episode opens with PRIVILEGE, read by the first host before the
script, so no model call can drop or reword it.

The script works only from the finished cards (status, week in review, next
steps), never the raw activity, so it inherits the client-safe filtering the
card prompt already does. The hosts are not Gallagher people: they open by
saying this is an audio summary of the written report, and the written
report controls.

ElevenLabs takes at most about 2,000 characters per dialogue request, so the
script goes out in chunks of whole turns and the parts are rebuilt as one
MP3 stream (sams_podcast.finish_audio).

Config (all optional except the keys):
    elevenlabs_api_key        no key = no podcast, digest drafts as before
    transistor_api_key        shared with Sam's Podcast and the Feud
    kirsten_podcast_show_id   the private Transistor show (--kirsten-digest
                              --shows lists ids; --subscribe EMAIL adds a
                              private feed). Unset = the MP3 is saved but not
                              published, and the email says nothing of it.
    kirsten_podcast           false turns the podcast off
    kirsten_podcast_voices    two ElevenLabs voice IDs [host 1, host 2]
    kirsten_podcast_hosts     the two hosts' first names
    kirsten_podcast_model     Claude model for the script and its fact check
                              (default claude-opus-5-5)
    kirsten_podcast_root      the show folder, if not found on its own
    kirsten_podcast_intro     the intro music, if not "Cello Awakening" there
    kirsten_podcast_intro_seconds / _intro_fade / _intro_overlap
                              how much intro plays (default 14 s: 10 at
                              full volume, then the 4 s fade; 0 = the whole
                              track), its fade, and how early the hosts
                              come in (defaults 4 s and 2 s)

The show folder is laid out like Sam's Podcast's and the Feud's (James,
10/31): Podcasts\\GC Updates on the Rocky laptop's OneDrive, "Rocky's
files - Podcasts\\GC Updates" on James's. It holds the intro music
(Cello Awakening.wav), PROMPTING_GUIDE.md (ElevenLabs' guide, James's
reference) and _Episodes\\, where each episode is saved as YYYY-MM-DD.mp3,
YYYY-MM-DD script.txt (so James can check what was said) and YYYY-MM-DD
cards.html (the show notes). If the folder can't be found, the episode is
saved in <cases_root>\\_Kirsten Digests beside the report, with no intro.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

import requests

log = logging.getLogger("rocky.kirsten")

ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-dialogue"
ELEVEN_MODEL = "eleven_v3"
# 64 kbps, the rate finish_audio re-encodes at.
OUTPUT_FORMAT = "mp3_44100_64"
_BITRATE = 64_000

# ElevenLabs premade voices (George, Sarah). Override with
# kirsten_podcast_voices.
_DEFAULT_VOICES = ["JBFqnCBsd6RMkjVDRZzb", "EXAVITQu4vr4xnSDxMaL"]
_DEFAULT_HOSTS = ["George", "Sarah"]

MAX_WORDS = 700          # about 4.7 minutes at 150 words a minute
MAX_SECONDS = 300
_CHUNK_CHARS = 1900      # ElevenLabs asks for <= 2,000 per request

# The show folder, beside Sam's Podcast and the Feud. The candidates are
# relative to the home folder, as in sams_podcast._ROOT_CANDIDATES.
SHOW_NAME = "GC Updates"   # the show, its episodes and its folder
SHOW_DIRNAME = SHOW_NAME
EPISODES_DIRNAME = "_Episodes"
_ONEDRIVE = "OneDrive - gejlaw.com"
INTRO_NAME = "Cello Awakening"
# The track runs 52.8 s. James, 10/31: fade out after 10 seconds, so 10 s
# at full volume and then INTRO_FADE (4 s); the track takes a breath at 13 s.
INTRO_SECONDS = 14


class _Profile:
    """The few Show fields that Sam's engine reads to find a show folder and
    its intro music (research_root, intro_path, finish_audio). Kirsten's
    podcast keeps its own script and run and borrows only those."""
    prefix = "kirsten_podcast"
    tag = "kirsten podcast"
    intro_name = INTRO_NAME
    roots = (Path(_ONEDRIVE) / "Podcasts" / SHOW_DIRNAME,
             Path(_ONEDRIVE) / "Rocky's files - Podcasts" / SHOW_DIRNAME)

    def key(self, name: str) -> str:
        return f"{self.prefix}_{name}"


PROFILE = _Profile()

# James's wording, 10/31. The email opens with the same sentence
# (kirsten_digest.PRIVILEGE_EMAIL).
PRIVILEGE = ("This podcast is a private attorney-client communication and is "
             "protected by the attorney-client and work product privileges.")

SYSTEM_PROMPT = """You write the script for a short audio companion to the Weekly Gallagher-Bozzuto Litigation Report, a weekly report from Gallagher LLP (outside litigation counsel) to Kirsten Woelper at Bozzuto, the client. The show is called "GC Updates". Two hosts, {host1} and {host2}, deliver it as a brisk news-style update. Kirsten listens to it in a private podcast feed; the written report is emailed to her with it.

You receive the report's cards: for each case, a status line, the week's developments, and next steps. Those cards are your only source.

Return ONLY a JSON object:
{{"turns": [{{"speaker": 1 or 2, "text": "<what that host says>"}}, ...]}}

SHAPE
- A privilege notice is read before your first turn: do not repeat it or mention privilege.
- {host1} opens: the show name ("GC Updates"), the period the report covers, and one sentence saying this is an audio summary of this week's written report, which has the full detail. Then a one-line preview of the week's headlines.
- Cover the cases with developments this week first, one at a time, each a short exchange between the hosts: what happened, then what comes next and when. Hand off naturally ("Over in Bulgin..."). Speaker turns alternate; most turns are one to three sentences.
- Then the quiet cases together in one or two turns: name them and give only an upcoming date that matters, if a card states one.
- Close with anything Gallagher needs from Bozzuto (only what the cards say), then a one-line sign-off pointing to the written report for questions.

LENGTH — at most {max_words} words in total across all turns (about four and a half minutes spoken). Budget roughly 60 opening words, 50 to 90 words for each case with developments, 15 words for each quiet case, and 60 for the close. A busy week gets less detail per case, not more time: give each development its headline fact and the next date, and drop secondary detail (who said what, supporting reasons, lists of more than three items). Leave out a quiet case's status rather than run long.

RULES
- Use only facts in the cards. Do not add, infer, or calculate dates, outcomes, amounts, or parties. Keep every pending, contemplated, or conditional event pending.
- A date before TODAY is past. Never present it as upcoming or scheduled, even where a card lists it under next steps: leave it out.
- Take every weekday from the CALENDAR; never work one out yourself.
- Speak to Kirsten as "you" and to the client as "Bozzuto"; the firm is "Gallagher" or "the team". Never name Gallagher attorneys or staff. The hosts are not lawyers and do not give advice or opinions on how a case will go.
- Write for the ear: say dates the way a person says them ("November fourth"), spell out abbreviations on first use where the card does, no case numbers, no citations, no URLs, no bullet points or markdown, no stage directions, no bracketed audio tags, no emojis.
- Plain, warm, professional. No jokes about the cases, no hype.

Output the JSON object and nothing else."""


def _settings(config: dict) -> tuple[list[str], list[str]]:
    voices = list(config.get("kirsten_podcast_voices") or _DEFAULT_VOICES)
    hosts = list(config.get("kirsten_podcast_hosts") or _DEFAULT_HOSTS)
    if len(voices) < 2 or len(hosts) < 2:
        raise ValueError("kirsten_podcast_voices and kirsten_podcast_hosts "
                         "each need two entries")
    return voices[:2], hosts[:2]


def enabled(config: dict) -> bool:
    return bool(config.get("elevenlabs_api_key")) and \
        config.get("kirsten_podcast", True) is not False


# ---------------------------------------------------------------------------
# Script
# ---------------------------------------------------------------------------

def cards_text(cards: list[tuple[str, str, dict]],
               quiet: str = "No significant developments this week.") -> str:
    """The finished cards, plain, for the script prompt. Failed cards are
    left out: there is nothing reliable to say about them. quiet is what a
    card with no developments says (kirsten_digest.Period.quiet)."""
    blocks = []
    for title, subtitle, section in cards:
        if section.get("error"):
            continue
        week = section.get("week") or []
        lines = [f"CASE: {title}" + (f" — {subtitle}" if subtitle else ""),
                 f"Status: {section.get('status') or '(none)'}",
                 "Week in review:"]
        lines += [f"- {b}" for b in week] or [f"- {quiet}"]
        lines.append("Next steps:")
        lines += [f"- {b}" for b in section.get("next_steps") or []]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def word_count(turns: list[dict]) -> int:
    return sum(len(t["text"].split()) for t in turns)


def parse_turns(text: str) -> list[dict]:
    """{"turns": [...]} from the model, cleaned: speaker 1 or 2, non-empty
    text, any stray [audio tags] or markdown removed."""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    turns = []
    for t in (data.get("turns") if isinstance(data, dict) else None) or []:
        if not isinstance(t, dict):
            continue
        try:
            speaker = 2 if int(t.get("speaker")) == 2 else 1
        except (TypeError, ValueError):
            speaker = 1
        line = re.sub(r"\[[^\]]*\]|[*_#`]", "", str(t.get("text") or ""))
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            turns.append({"speaker": speaker, "text": line})
    return turns


def fit(turns: list[dict], max_words: int = MAX_WORDS,
        tag: str = "kirsten podcast") -> list[dict]:
    """Hard length guard after the model's own retry: keep turns in order
    while they fit, always keeping the sign-off."""
    if word_count(turns) <= max_words or len(turns) < 2:
        return turns
    last = turns[-1]
    budget = max_words - len(last["text"].split())
    kept, used = [], 0
    for t in turns[:-1]:
        n = len(t["text"].split())
        if used + n > budget:
            break
        kept.append(t)
        used += n
    log.warning(f"[{tag}] script ran {word_count(turns)} words; "
                f"cut to {used + len(last['text'].split())}")
    return kept + [last]


def write_script(client, model: str, cards, week_label: str,
                 hosts: list[str], quiet: str | None = None) -> list[dict]:
    """One Claude call (plus one shorten retry if it runs long), the fact
    check, then PRIVILEGE as the first turn."""
    material = cards_text(cards, quiet) if quiet else cards_text(cards)
    if not material:
        return []
    # The model runs long, so the prompt asks for less than the hard cap.
    system = SYSTEM_PROMPT.format(host1=hosts[0], host2=hosts[1],
                                  max_words=MAX_WORDS - 100)
    from sams_podcast import _ask, calendar
    today = datetime.now().strftime("%Y-%m-%d (%A)")
    messages = [{"role": "user", "content":
                 f"TODAY: {today}\n{calendar(today)}\n"
                 f"WEEK: {week_label}\n\nREPORT CARDS:\n\n{material}\n\n"
                 f"Write the JSON script."}]
    turns: list[dict] = []
    for attempt in range(2):
        raw = _ask(client, model, system, messages, max_tokens=16000,
                   temperature=0.4)
        turns = parse_turns(raw)
        if not turns:
            raise ValueError("the podcast script came back unreadable")
        words = word_count(turns)
        if words <= MAX_WORDS:
            break
        log.info(f"[kirsten podcast] script ran {words} words; asking for "
                 f"a shorter one")
        messages += [{"role": "assistant", "content": raw},
                     {"role": "user", "content":
                      f"That is {words} words. Cut it to at most "
                      f"{MAX_WORDS - 50} words: less detail per case, same "
                      f"shape. Return the JSON only."}]
    turns = check(client, model, material,
                  fit(turns, MAX_WORDS - len(PRIVILEGE.split())), today)
    # After the check, which would drop it: it is on no card.
    return [{"speaker": 1, "text": PRIVILEGE}] + turns if turns else []


CHECK_PROMPT = """You fact-check the script of a short two-host audio summary of the Weekly Gallagher-Bozzuto Litigation Report, sent by Gallagher LLP (outside counsel) to its client. The report cards are the only source of truth.

You receive TODAY, a CALENDAR, the REPORT CARDS, and the SCRIPT as numbered turns.

Check every turn, one claim at a time, including the opening preview and the close, which summarize several cases at once and are where facts get crossed. For each case, date, party, or amount a turn names, confirm the claim is on THAT case's card.

Fix every turn that: states a fact, date, amount, party, or outcome not on the cards; moves a fact to the wrong case; presents a past date as upcoming; presents something pending, contemplated, or conditional as done; names a Gallagher attorney or staff member; or gives an opinion or prediction about how a case will go. A weekday is wrong only if the CALENDAR says so: never change one from your own reckoning. Wording and style are not your concern.

Return ONLY a JSON object:
{"fixes": [{"turn": <number>, "problem": "<short reason>", "text": "<the whole turn rewritten to say only what the cards support, same speaker voice, or empty to drop the turn>"}]}

Return {"fixes": []} if every turn is supported. Output the JSON object and nothing else."""


def check(client, model: str, material: str, turns: list[dict],
          today: str) -> list[dict]:
    """Sam's Podcast's fact check with a client-facing brief: every turn
    against the cards, unsupported ones rewritten or dropped. It goes to the
    client, so it also catches firm names and opinions on outcomes. A failed
    check keeps the script."""
    from sams_podcast import check_script
    return check_script(client, model, material, turns, today, tags=[],
                        system=CHECK_PROMPT, log_tag="kirsten podcast")


def script_text(turns: list[dict], hosts: list[str]) -> str:
    return "\n\n".join(f"{hosts[t['speaker'] - 1].upper()}: {t['text']}"
                       for t in turns) + "\n"


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

def _split_long(text: str, cap: int) -> list[str]:
    """A single turn over the request cap, split at sentence ends."""
    parts, cur = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if cur and len(cur) + 1 + len(sentence) > cap:
            parts.append(cur)
            cur = sentence
        else:
            cur = f"{cur} {sentence}".strip()
    if cur:
        parts.append(cur)
    return parts


def chunk(inputs: list[dict], cap: int = _CHUNK_CHARS) -> list[list[dict]]:
    """Dialogue inputs grouped into requests of at most cap characters,
    breaking only between turns (or sentences of a very long turn)."""
    pieces = []
    for inp in inputs:
        for text in (_split_long(inp["text"], cap)
                     if len(inp["text"]) > cap else [inp["text"]]):
            pieces.append({"text": text, "voice_id": inp["voice_id"]})
    batches, cur, size = [], [], 0
    for p in pieces:
        if cur and size + len(p["text"]) > cap:
            batches.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += len(p["text"])
    if cur:
        batches.append(cur)
    return batches


def synthesize(api_key: str, turns: list[dict], voices: list[str],
               out_path: Path, max_seconds: int = MAX_SECONDS,
               tag: str = "kirsten podcast",
               settings: dict | None = None,
               parts: list | None = None, model: str = ELEVEN_MODEL,
               seed: int | None = None, stitch: bool = False) -> float:
    """Voice the script into out_path. Returns the length in seconds.
    settings is the dialogue request's "settings" (e.g. stability); seed
    asks every part to sample the same way; stitch sends each part the
    request IDs of the parts before it, so the voices carry over. If
    ElevenLabs rejects any of those, they are dropped for the rest of the
    episode; if it then rejects the model, the rest goes on ELEVEN_MODEL."""
    inputs = [{"text": t["text"], "voice_id": voices[t["speaker"] - 1]}
              for t in turns]
    audio = b""
    batches = chunk(inputs)
    extras: dict = {}
    if settings:
        extras["settings"] = settings
    if seed is not None:
        extras["seed"] = seed
    request_ids: list[str] = []
    for i, batch in enumerate(batches, 1):
        body = {"inputs": batch, "model_id": model, **extras}
        if stitch and request_ids:
            body["previous_request_ids"] = request_ids[-3:]
        for attempt in range(3):
            resp = requests.post(
                ELEVEN_URL, params={"output_format": OUTPUT_FORMAT},
                headers={"xi-api-key": api_key,
                         "Content-Type": "application/json",
                         "Accept": "audio/mpeg"},
                json=body, timeout=300)
            if resp.status_code not in (400, 422):
                break
            optional = [k for k in ("settings", "seed", "previous_request_ids")
                        if k in body]
            if optional:
                log.warning(f"[{tag}] ElevenLabs rejected "
                            f"{', '.join(optional)} (HTTP {resp.status_code}: "
                            f"{resp.text[:200]}); retrying without them")
                for k in optional:
                    body.pop(k)
                extras.clear()
                stitch = False
                continue
            if model != ELEVEN_MODEL:
                log.warning(f"[{tag}] ElevenLabs rejected model {model} "
                            f"(HTTP {resp.status_code}: {resp.text[:200]}); "
                            f"retrying on {ELEVEN_MODEL}")
                model = body["model_id"] = ELEVEN_MODEL
                continue
            break
        if resp.status_code != 200:
            raise RuntimeError(f"ElevenLabs HTTP {resp.status_code} on part "
                               f"{i}/{len(batches)}: {resp.text[:300]}")
        request_id = (getattr(resp, "headers", None) or {}).get("request-id")
        if request_id:
            request_ids.append(request_id)
        audio += resp.content
        if parts is not None:
            parts.append(resp.content)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(audio)
    seconds = len(audio) * 8 / _BITRATE
    log.info(f"[{tag}] {out_path.name}: {len(batches)} part(s) on {model}, "
             f"{len(audio) / 1_048_576:.1f} MB, about {seconds / 60:.1f} min")
    if seconds > max_seconds + 20:
        log.warning(f"[{tag}] audio runs {seconds:.0f}s, over the "
                    f"{max_seconds}s target")
    return seconds


def show_root(config: dict, home: Path | None = None) -> Path | None:
    """The show folder: kirsten_podcast_root, else the first candidate under
    this profile's OneDrive that exists."""
    from sams_podcast import research_root
    return research_root(config, home, PROFILE)


def episodes_dir(config: dict, fallback: Path,
                 home: Path | None = None) -> Path:
    root = show_root(config, home)
    if root is None:
        log.warning(f"[kirsten podcast] show folder {SHOW_DIRNAME!r} not "
                    f"found (set kirsten_podcast_root); saving the episode "
                    f"in {fallback}")
        return fallback
    return root / EPISODES_DIRNAME


def intro_music(config: dict, home: Path | None = None) -> Path | None:
    from sams_podcast import intro_path
    return intro_path(config, show_root(config, home), PROFILE)


def intro_timing(config: dict) -> dict:
    """config with the intro cut at INTRO_SECONDS unless it sets its own."""
    return {PROFILE.key("intro_seconds"): INTRO_SECONDS, **config}


def make(config: dict, cards, week_label: str, mp3_path: Path,
         script_path: Path, audio: bool = True,
         quiet: str | None = None) -> Path | None:
    """Script (always saved) and, if audio, the MP3. Returns the MP3 path,
    or None when there is nothing to publish. Never raises: the written
    report drafts either way."""
    from anthropic import Anthropic
    try:
        voices, hosts = _settings(config)
        client = Anthropic(api_key=config["anthropic_api_key"])
        from sams_podcast import DEFAULT_MODEL
        model = config.get("kirsten_podcast_model") or DEFAULT_MODEL
        turns = write_script(client, model, cards, week_label, hosts, quiet)
        if not turns:
            log.info("[kirsten podcast] no summarized cards; no podcast")
            return None
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script_text(turns, hosts), encoding="utf-8")
        log.info(f"[kirsten podcast] script: {len(turns)} turns, "
                 f"{word_count(turns)} words -> {script_path}")
        if not audio:
            return None
        parts: list[bytes] = []
        synthesize(config["elevenlabs_api_key"], turns, voices, mp3_path,
                   parts=parts)
        # Each ElevenLabs part is a whole MP3 whose first frame states only
        # its own length, so the glued file read as about two minutes in
        # header-trusting players (10/19, 10/22, 10/25 all did). Rebuild it
        # as one stream; if that fails, the glued file still has all the
        # audio.
        # The intro music goes in front in the same pass.
        from sams_podcast import finish_audio
        finish_audio(intro_timing(config), intro_music(config), mp3_path,
                     parts, word_count(turns), log_tag="kirsten podcast",
                     show=PROFILE)
        return mp3_path
    except Exception as e:
        log.warning(f"[kirsten podcast] FAILED, drafting without audio: {e}")
        return None


# ---------------------------------------------------------------------------
# Transistor (the private Gallagher-Bozzuto show)
# ---------------------------------------------------------------------------

def show_id(config: dict) -> str:
    return str(config.get("kirsten_podcast_show_id") or "").strip()


def can_publish(config: dict) -> bool:
    return bool(config.get("transistor_api_key")) and bool(show_id(config))


def show_notes(cards, week_label: str,
               quiet: str = "No significant developments this week.") -> str:
    """The episode description: the privilege notice, then each case's
    status, developments and next steps (failed cards left out)."""
    from html import escape

    def ul(items):
        return "<ul>" + "".join(f"<li>{escape(i)}</li>" for i in items) + "</ul>"

    parts = [f"<p><em>{escape(PRIVILEGE)}</em></p>",
             f"<p>{escape(week_label)}. The written report, emailed with "
             f"this episode, has the full detail.</p>"]
    for title, subtitle, section in cards:
        if section.get("error"):
            continue
        head = title + (f" — {subtitle}" if subtitle else "")
        parts.append(f"<h3>{escape(head)}</h3>")
        if section.get("status"):
            parts.append(f"<p>{escape(section['status'])}</p>")
        parts.append(ul(section.get("week") or [quiet]))
        parts.append(ul(section.get("next_steps") or []))
    return "\n".join(parts)


def publish(config: dict, mp3: Path, title: str, notes: str,
            transcript: str, record: dict) -> bool:
    """Upload, create and publish the episode. record gets episode_id as
    soon as the episode exists, so a failed publish is retried without a
    second upload. Returns True once it is live."""
    import sams_podcast as sp
    key = config["transistor_api_key"]
    try:
        if not record.get("episode_id"):
            audio_url = sp.upload_audio(key, mp3)
            record["episode_id"] = sp.create_episode(
                key, show_id(config), title,
                "Audio summary of the Gallagher-Bozzuto litigation report. "
                + PRIVILEGE, notes, audio_url, transcript)
        sp.publish_episode(key, record["episode_id"])
    except Exception as e:
        log.warning(f"[kirsten podcast] publish FAILED: {e}. The episode is "
                    f"saved at {mp3}"
                    + (f"; Transistor has it as draft episode "
                       f"{record['episode_id']}" if record.get("episode_id")
                       else ""))
        return False
    record["published"] = True
    log.info(f"[kirsten podcast] published {title!r} to Transistor "
             f"(episode {record['episode_id']})")
    return True


def feed_cli(config: dict, args: list[str]) -> None:
    """--kirsten-digest --shows | --subscribe EMAIL."""
    import sams_podcast as sp
    key = config.get("transistor_api_key")
    if not key:
        log.error("[kirsten podcast] transistor_api_key is not set in "
                  "config.json")
        return
    if "--shows" in args:
        for found in sp.list_shows(key):
            attrs = found.get("attributes") or {}
            log.info(f"[kirsten podcast] show id {found.get('id')}: "
                     f"{attrs.get('title')!r} (private: "
                     f"{attrs.get('private_show', attrs.get('private'))})")
        return
    i = args.index("--subscribe")
    email = args[i + 1] if i + 1 < len(args) else ""
    if "@" not in email or not show_id(config):
        log.error("[kirsten podcast] usage: --kirsten-digest --subscribe "
                  "EMAIL (and kirsten_podcast_show_id set)")
        return
    url = sp.subscribe(key, show_id(config), email) or \
        "(see the Transistor dashboard)"
    log.info(f"[kirsten podcast] {email} subscribed. Private feed: {url}")
