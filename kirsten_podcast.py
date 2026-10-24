"""
Kirsten Podcast — the Kirsten Digest as a two-host audio week update
====================================================================

Runs inside --kirsten-digest (see kirsten_digest.run_cli). After the case
cards are written, one Claude call rewrites them as a news-style "week
update" between two hosts, at most about five minutes of speech. ElevenLabs
Text to Dialogue (eleven_v3) voices it, and the MP3 is attached to the same
draft. James hears it before he sends, just as he reads the cards.

The script works only from the finished cards (status, week in review, next
steps), never the raw activity, so it inherits the client-safe filtering the
card prompt already does. The hosts are not Gallagher people: they open by
saying this is an audio summary of the written report, and the written
report controls.

ElevenLabs takes at most about 2,000 characters per dialogue request, so the
script goes out in chunks of whole turns and the MP3s are joined (same
format, so plain concatenation plays as one file).

Config (all optional except the key):
    elevenlabs_api_key        no key = no podcast, digest drafts as before
    kirsten_podcast           false turns the podcast off
    kirsten_podcast_voices    two ElevenLabs voice IDs [host 1, host 2]
    kirsten_podcast_hosts     the two hosts' first names
    kirsten_podcast_model     Claude model for the script

Files sit next to the report: <cases_root>\\_Kirsten Digests\\YYYY-MM-DD.mp3
and YYYY-MM-DD podcast.txt (the script, so James can check what was said).
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
# 64 kbps keeps five minutes near 2.4 MB, under Graph's 3 MB simple-attach
# limit. Bigger files still attach, through an upload session.
OUTPUT_FORMAT = "mp3_44100_64"
_BITRATE = 64_000

# ElevenLabs premade voices (George, Sarah). Override with
# kirsten_podcast_voices.
_DEFAULT_VOICES = ["JBFqnCBsd6RMkjVDRZzb", "EXAVITQu4vr4xnSDxMaL"]
_DEFAULT_HOSTS = ["George", "Sarah"]

MAX_WORDS = 700          # about 4.7 minutes at 150 words a minute
MAX_SECONDS = 300
_CHUNK_CHARS = 1900      # ElevenLabs asks for <= 2,000 per request

_GRAPH = "https://graph.microsoft.com/v1.0"
_SIMPLE_ATTACH_MAX = 3 * 1024 * 1024

SYSTEM_PROMPT = """You write the script for a short audio companion to the Weekly Gallagher-Bozzuto Litigation Report, a weekly report from Gallagher LLP (outside litigation counsel) to Kirsten Woelper at Bozzuto, the client. Two hosts, {host1} and {host2}, deliver it as a brisk news-style "week update". Kirsten listens to it; the written report goes with it.

You receive the report's cards: for each case, a status line, the week's developments, and next steps. Those cards are your only source.

Return ONLY a JSON object:
{{"turns": [{{"speaker": 1 or 2, "text": "<what that host says>"}}, ...]}}

SHAPE
- {host1} opens: the show name ("the Gallagher-Bozzuto week update"), the week, and one sentence saying this is an audio summary of this week's written report, which has the full detail. Then a one-line preview of the week's headlines.
- Cover the cases with developments this week first, one at a time, each a short exchange between the hosts: what happened, then what comes next and when. Hand off naturally ("Over in Bulgin..."). Speaker turns alternate; most turns are one to three sentences.
- Then the quiet cases together in one or two turns: name them and give only an upcoming date that matters, if a card states one.
- Close with anything Gallagher needs from Bozzuto (only what the cards say), then a one-line sign-off pointing to the written report for questions.

LENGTH — at most {max_words} words in total across all turns (about four and a half minutes spoken). Budget roughly 60 opening words, 50 to 90 words for each case with developments, 15 words for each quiet case, and 60 for the close. A busy week gets less detail per case, not more time: give each development its headline fact and the next date, and drop secondary detail (who said what, supporting reasons, lists of more than three items). Leave out a quiet case's status rather than run long.

RULES
- Use only facts in the cards. Do not add, infer, or calculate dates, outcomes, amounts, or parties. Keep every pending, contemplated, or conditional event pending.
- A date before TODAY is past. Never present it as upcoming or scheduled, even where a card lists it under next steps: leave it out.
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

def cards_text(cards: list[tuple[str, str, dict]]) -> str:
    """The finished cards, plain, for the script prompt. Failed cards are
    left out: there is nothing reliable to say about them."""
    blocks = []
    for title, subtitle, section in cards:
        if section.get("error"):
            continue
        week = section.get("week") or []
        lines = [f"CASE: {title}" + (f" — {subtitle}" if subtitle else ""),
                 f"Status: {section.get('status') or '(none)'}",
                 "Week in review:"]
        lines += [f"- {b}" for b in week] or ["- No significant developments this week."]
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
                 hosts: list[str]) -> list[dict]:
    """One Claude call (plus one shorten retry if it runs long)."""
    material = cards_text(cards)
    if not material:
        return []
    # The model runs long, so the prompt asks for less than the hard cap.
    system = SYSTEM_PROMPT.format(host1=hosts[0], host2=hosts[1],
                                  max_words=MAX_WORDS - 100)
    messages = [{"role": "user", "content":
                 f"TODAY: {datetime.now().strftime('%Y-%m-%d (%A)')}\n"
                 f"WEEK: {week_label}\n\nREPORT CARDS:\n\n{material}\n\n"
                 f"Write the JSON script."}]
    turns: list[dict] = []
    for attempt in range(2):
        resp = client.messages.create(model=model, max_tokens=4000,
                                      temperature=0.4, system=system,
                                      messages=messages)
        raw = resp.content[0].text
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
    return fit(turns)


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
               settings: dict | None = None) -> float:
    """Voice the script into out_path. Returns the length in seconds.
    settings is the dialogue request's "settings" (e.g. stability); if
    ElevenLabs rejects it, that part is retried once without it."""
    inputs = [{"text": t["text"], "voice_id": voices[t["speaker"] - 1]}
              for t in turns]
    audio = b""
    batches = chunk(inputs)
    for i, batch in enumerate(batches, 1):
        body = {"inputs": batch, "model_id": ELEVEN_MODEL}
        if settings:
            body["settings"] = settings
        for attempt in range(2):
            resp = requests.post(
                ELEVEN_URL, params={"output_format": OUTPUT_FORMAT},
                headers={"xi-api-key": api_key,
                         "Content-Type": "application/json",
                         "Accept": "audio/mpeg"},
                json=body, timeout=300)
            if resp.status_code in (400, 422) and "settings" in body:
                log.warning(f"[{tag}] ElevenLabs rejected settings "
                            f"{settings} (HTTP {resp.status_code}: "
                            f"{resp.text[:200]}); retrying without them")
                body.pop("settings")
                continue
            break
        if resp.status_code != 200:
            raise RuntimeError(f"ElevenLabs HTTP {resp.status_code} on part "
                               f"{i}/{len(batches)}: {resp.text[:300]}")
        audio += resp.content
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(audio)
    seconds = len(audio) * 8 / _BITRATE
    log.info(f"[{tag}] {out_path.name}: {len(batches)} part(s), "
             f"{len(audio) / 1_048_576:.1f} MB, about {seconds / 60:.1f} min")
    if seconds > max_seconds + 20:
        log.warning(f"[{tag}] audio runs {seconds:.0f}s, over the "
                    f"{max_seconds}s target")
    return seconds


def make(config: dict, cards, week_label: str, mp3_path: Path,
         script_path: Path, audio: bool = True) -> Path | None:
    """Script (always saved) and, if audio, the MP3. Returns the MP3 path,
    or None when there is nothing to attach. Never raises: the written
    report drafts either way."""
    import rocky
    from anthropic import Anthropic
    try:
        voices, hosts = _settings(config)
        client = Anthropic(api_key=config["anthropic_api_key"])
        model = config.get("kirsten_podcast_model", rocky.CLAUDE_MODEL)
        turns = write_script(client, model, cards, week_label, hosts)
        if not turns:
            log.info("[kirsten podcast] no summarized cards; no podcast")
            return None
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script_text(turns, hosts), encoding="utf-8")
        log.info(f"[kirsten podcast] script: {len(turns)} turns, "
                 f"{word_count(turns)} words -> {script_path}")
        if not audio:
            return None
        synthesize(config["elevenlabs_api_key"], turns, voices, mp3_path)
        return mp3_path
    except Exception as e:
        log.warning(f"[kirsten podcast] FAILED, drafting without audio: {e}")
        return None


# ---------------------------------------------------------------------------
# Attach to the draft
# ---------------------------------------------------------------------------

def attach(token: str, mailbox: str, message_id: str, path: Path) -> bool:
    """Add the MP3 to an existing draft. Under 3 MB in one POST; larger
    through a Graph upload session. Mail.ReadWrite covers both."""
    data = path.read_bytes()
    base = f"{_GRAPH}/users/{mailbox}/messages/{message_id}/attachments"
    auth = {"Authorization": f"Bearer {token}"}
    try:
        if len(data) < _SIMPLE_ATTACH_MAX:
            import base64
            resp = requests.post(base, headers=auth, timeout=120, json={
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": path.name, "contentType": "audio/mpeg",
                "contentBytes": base64.b64encode(data).decode("ascii")})
            ok = resp.status_code == 201
        else:
            resp = requests.post(f"{base}/createUploadSession", headers=auth,
                                 timeout=60, json={"AttachmentItem": {
                                     "attachmentType": "file",
                                     "name": path.name, "size": len(data),
                                     "contentType": "audio/mpeg"}})
            ok = resp.status_code == 201
            if ok:
                url, step = resp.json()["uploadUrl"], 3 * 1024 * 1024
                for start in range(0, len(data), step):
                    part = data[start:start + step]
                    end = start + len(part) - 1
                    resp = requests.put(url, data=part, timeout=120, headers={
                        "Content-Length": str(len(part)),
                        "Content-Range": f"bytes {start}-{end}/{len(data)}"})
                    if resp.status_code not in (200, 201):
                        ok = False
                        break
    except requests.RequestException as e:
        log.warning(f"[kirsten podcast] attach network error: {e}")
        return False
    if not ok:
        log.warning(f"[kirsten podcast] attach failed HTTP "
                    f"{resp.status_code}: {resp.text[:300]}")
    return ok
