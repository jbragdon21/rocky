"""Day sheets: what the drafting and verification calls read.

A port of the folder's daydump.py, grown into the packet the design
describes (TIME_ENTRIES_DESIGN.md, "4. Draft, one call per day"). One sheet
covers one business day, and a Saturday or Sunday rides with the Monday
after it, because weekend work leaves the outbox in Monday's 07:xx batch.

Each sheet lists:
  * every timecard entry on its dates, filled and blank; the ones to draft
    get a ref (E1, E2, ...) and the draft must answer with that ref
  * every send on those dates, plus the next morning before the batch hour,
    with James's own text and the Eastern send time; each gets a ref (M1...)
  * the correlator's ranked candidates for each entry to draft
  * the reply chains of the top candidates (Rule 0: read the chain, not
    just James's line)
  * the reference rows for the matters in play
  * James's own narratives on those matters within two weeks
  * the colleague list (@gallagherllp.com is never the advisee)

The sheet also answers the two tools the calls get: read_email (one email's
full body and reply chain) and search_mail (James's sends near a date).
Mail found through either tool gets a ref too, so drafts can cite it.
"""

import re
from datetime import date, timedelta

from .mail import clean_text, own_text

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# From the hand process's drafting_agent_spec.md (Rule 0), plus S. Kannan,
# whom the October Part 2 run found writing on the Marina Bay thread.
COLLEAGUES = ("G. O'Mara, C. Araviakis, H. Mondragon, S. Ronan, T. Fultz, "
              "S. Stephey, P. Goranin, T. Jerome, A. Smolkin, S. Cowin, "
              "J. Dugan, M. Brown, C. Esmeir, J. Dvornicky, S. Kannan")

OWN_CHARS = 900            # James's own text per email in the mail list
CHAIN_MSG_CHARS = 1200     # each message in a rendered reply chain
CHAIN_CHARS = 6000         # one rendered chain
CHAINS_TOTAL = 60000       # every chain in the sheet
CHAINS_PER_ENTRY = 3       # top candidates whose chains go in the sheet
READ_BODY_CHARS = 8000     # read_email: the email's own full body
READ_CHAIN_CHARS = 12000   # read_email: its reply chain
SEARCH_MAX_DAYS = 31
SEARCH_MAX_HITS = 15
PRIOR_DAYS = 14


def _d(s: str) -> date:
    return date.fromisoformat(s)


def day_label(d: str) -> str:
    x = _d(d)
    return f"{WEEKDAYS[x.weekday()]} {x.month}/{x.day}"


def group_days(dates) -> list[list[str]]:
    """Work dates -> one list per drafting call. A Saturday or Sunday joins
    the Monday after it, and the rest of that weekend and the Monday are in
    the group even with nothing to draft: Saturday work can leave the outbox
    on Sunday, and Monday's batch is the weekend's evidence."""
    groups: dict[str, set] = {}
    for d in sorted(set(dates)):
        x = _d(d)
        anchor = x if x.weekday() < 5 else x + timedelta(7 - x.weekday())
        span = {(x + timedelta(k)).isoformat() for k in range((anchor - x).days + 1)}
        groups.setdefault(anchor.isoformat(), set()).update(span)
    return [sorted(v) for _k, v in sorted(groups.items())]


def _names(s: str | None, n: int = 6) -> str:
    parts = [p.strip(" ';\"") for p in re.split(r"[;\x00]", s or "") if p.strip(" ';\"")]
    return "; ".join(parts[:n]) + (" …" if len(parts) > n else "")


def _hhmm(e: dict) -> str:
    return (e.get("sent_at") or "")[11:16]


def _sent_hour(e: dict) -> int:
    try:
        return int(_hhmm(e)[:2])
    except ValueError:
        return 0


class DaySheet:
    """One drafting call's packet, plus the ref registry its tools share."""

    def __init__(self, dates: list[str], entries: list[dict], work_rows: set,
                 mail_by_date: dict[str, list[dict]], results: dict,
                 props: dict, notes: dict, prior: dict, settings: dict,
                 thread_fn=None, all_mail: list[dict] | None = None):
        self.dates = list(dates)
        self.settings = settings
        self.thread_fn = thread_fn
        self.all_mail = all_mail or [e for v in mail_by_date.values() for e in v]
        self._mail_by_date = mail_by_date
        self.mail_refs: dict[str, dict] = {}
        self._ref_of: dict[str, str] = {}

        # Entries on the sheet's dates; the ones to draft get refs.
        self.entries = sorted((e for e in entries if e["work_date"] in set(dates)),
                              key=lambda e: (e["work_date"], e.get("row") or 0))
        self.entry_refs: dict[str, dict] = {}
        n = 0
        for e in self.entries:
            if e.get("row") in work_rows:
                n += 1
                self.entry_refs[f"E{n}"] = e
        self.results = {ref: results.get(e.get("row")) for ref, e in self.entry_refs.items()}

        # Mail on the sheet: its dates, and the next morning's batch.
        batch = settings.get("window_batch_hour") or 0
        shown = []
        for d in self.dates:
            shown += mail_by_date.get(d, [])
        nxt = (_d(self.dates[-1]) + timedelta(1)).isoformat()
        self.next_morning = []
        if batch:
            self.next_morning = [e for e in mail_by_date.get(nxt, [])
                                 if _sent_hour(e) < batch]
        self.shown = sorted(shown, key=lambda e: e.get("sent_at") or "")
        for e in self.shown + self.next_morning:
            self.ref(e)
        # Candidates from other days (±3, Sunday forward) get refs after.
        self.other_day = []
        by_id = {e["id"]: e for e in self.all_mail}
        for ref, res in self.results.items():
            for c in (res or {}).get("cands", []):
                e = by_id.get(c["id"])
                if e is not None and c["id"] not in self._ref_of:
                    self.ref(e)
                    self.other_day.append(e)
        self.other_day.sort(key=lambda e: e.get("sent_at") or "")

        self.matters = sorted({e["matter"] for e in self.entry_refs.values() if e["matter"]})
        self.props, self.notes, self.prior = props, notes, prior
        self.text = self._render()

    # ------------------------------------------------------------------ refs
    def ref(self, email: dict) -> str:
        r = self._ref_of.get(email["id"])
        if r is None:
            r = f"M{len(self.mail_refs) + 1}"
            self._ref_of[email["id"]] = r
            self.mail_refs[r] = email
        return r

    def email(self, ref: str) -> dict | None:
        return self.mail_refs.get((ref or "").strip().upper())

    # ------------------------------------------------------------- rendering
    def _mail_line(self, e: dict, chars: int = OWN_CHARS) -> list[str]:
        tag = " [INVITE]" if e.get("inv") else ""
        out = [f"[{self.ref(e)}] {day_label(e['date'])} {_hhmm(e)}{tag}  {e.get('subject') or ''}"]
        to = f"     to: {_names(e.get('to'))}"
        if (e.get("cc") or "").strip():
            to += f"   cc: {_names(e.get('cc'))}"
        out.append(to)
        txt = own_text(e.get("body"), chars)
        for line in txt.split("\n") if txt else []:
            out.append("     | " + line)
        return out

    def chain_text(self, e: dict, total: int = CHAIN_CHARS,
                   per_msg: int = CHAIN_MSG_CHARS) -> str:
        """The reply chain around one of James's sends. brain.db gives the
        whole conversation; corpus.db only has the quoted text in his body."""
        msgs = self.thread_fn(e.get("conversation_id")) if self.thread_fn else []
        if not msgs:
            body = clean_text(e.get("body"))
            return (body[:total] + " …") if len(body) > total else body
        lines = []
        for m in msgs:
            who = "JAMES" if m.get("direction") == "sent" else (m.get("from") or "?")
            stamp = m.get("sent_at") or ""
            when = f"{day_label(stamp[:10])} {stamp[11:16]}" if len(stamp) >= 16 else stamp
            mark = "  <== this email" if m.get("id") == e["id"] else ""
            lines.append(f"  {when}  {who}{mark}")
            txt = own_text(m.get("body"), per_msg)
            for ln in txt.split("\n") if txt else []:
                lines.append("    | " + ln)
        text = "\n".join(lines)
        if len(text) > total:
            # Keep the end: the latest messages and this email matter most.
            text = "… (earlier messages cut)\n" + text[-total:]
        return text

    def _render(self) -> str:
        S: list[str] = []
        days = ", ".join(day_label(d) for d in self.dates)
        S.append(f"TIME ENTRY DAY SHEET: {days}/{_d(self.dates[-1]).year}")
        batch = self.settings.get("window_batch_hour") or 0
        S.append(f"Mail shown: James's sends on these dates"
                 + (f", plus the next morning before {batch}:00" if batch else "")
                 + ". Times are Eastern.")
        S.append("")
        S.append("== TIME ENTRIES ==")
        S.append("Entries with a ref [E#] need a narrative. The rest are written already, "
                 "and the threads they describe are billed.")
        refs = {id(e): r for r, e in self.entry_refs.items()}
        for e in self.entries:
            narr = (e.get("narrative") or "").strip()
            head = (f"{e['work_date']} {WEEKDAYS[_d(e['work_date']).weekday()]} | "
                    f"{e['matter'] or '(no matter)'} | {e.get('hours')}h | "
                    f"{(e.get('matter_name') or '')[:60]}"
                    + (f" ({e['client']})" if e.get("client") else ""))
            flags = []
            if e.get("non_billable"):
                flags.append("non-billable")
            if e.get("no_charge"):
                flags.append("no charge")
            if flags:
                head += " [" + ", ".join(flags) + "]"
            r = refs.get(id(e))
            if r:
                state = (f'PARTIAL: "{narr}" (the draft must begin with this text)'
                         if narr else "BLANK")
                S.append(f"[{r}] {head} | {state}")
            else:
                S.append(f"     {head} | written: \"{narr}\"")
        S.append("")
        S.append("== JAMES'S SENT MAIL ==")
        if not self.shown:
            S.append("(no sends on these dates)")
        for e in self.shown:
            S += self._mail_line(e)
        if self.next_morning:
            S.append("")
            S.append(f"-- next morning, before {batch}:00 --")
            for e in self.next_morning:
                S += self._mail_line(e)
        if self.other_day:
            S.append("")
            S.append("-- other-day mail the correlator ranked (cite for a High only "
                     "under the weekend-batch rule) --")
            for e in self.other_day:
                S += self._mail_line(e, 500)
        S.append("")
        S.append("== CORRELATOR CANDIDATES (ranked by matter tokens; a place to start, "
                 "not a verdict) ==")
        chain_ids: list[str] = []
        for ref, e in self.entry_refs.items():
            res = self.results.get(ref) or {}
            cands = res.get("cands") or []
            label = f"{ref} {e['matter'] or '(no matter)'}"
            if not e["matter"]:
                S.append(f"{label}: no matter number, so nothing to correlate; "
                         f"REVIEW and ask James to assign the matter")
                continue
            if cands:
                parts = []
                for i, c in enumerate(cands):
                    r = self._ref_of.get(c["id"], "?")
                    parts.append(f"{r} ({c['score']}: {', '.join(c['hits'][:4])})")
                    if i < CHAINS_PER_ENTRY and c["id"] not in chain_ids:
                        chain_ids.append(c["id"])
                S.append(f"{label}: " + ", ".join(parts)
                         + (f"   [window: {res.get('stage')}]" if res.get("stage") else ""))
            else:
                why = "no candidates"
                if res.get("zero_hit_token"):
                    why += (f'; the token "{res["zero_hit_token"]}" has zero hits in all '
                            f"loaded mail (probably a missing alias)")
                if res.get("noemail"):
                    why += "; matter is flagged typically-no-email"
                S.append(f"{label}: {why}. Tokens tried: "
                         + ", ".join((res.get("tokens") or [])[:8]))
        S.append("")
        S.append("== REPLY CHAINS FOR THE TOP CANDIDATES ==")
        S.append("Use read_email for any other email's full body and chain.")
        by_id = {e["id"]: e for e in self.all_mail}
        used = 0
        for cid in chain_ids:
            e = by_id.get(cid)
            if e is None:
                continue
            block = (f"[{self.ref(e)}] chain for {day_label(e['date'])} {_hhmm(e)} "
                     f"'{e.get('subject') or ''}'\n" + self.chain_text(e))
            if used + len(block) > CHAINS_TOTAL:
                S.append(f"[{self.ref(e)}] (chain not shown: sheet is full; use read_email)")
                continue
            used += len(block)
            S.append(block)
            S.append("")
        S.append("== MATTERS IN PLAY ==")
        for mn in self.matters:
            p = self.props.get(mn) or {}
            nt = self.notes.get(mn) or {}
            bits = [mn]
            for k, lbl in (("property", "property"), ("matter_name", "matter"),
                           ("client", "client"), ("contacts", "contacts")):
                if p.get(k):
                    bits.append(f"{lbl}: {p[k]}")
            for k, v in nt.items():
                if k != "Matter Number" and v not in (None, ""):
                    bits.append(f"{k}: {v}")
            if len(bits) == 1:
                bits.append("not in the Property Reference Table")
            S.append(" | ".join(str(b) for b in bits))
        S.append("")
        S.append(f"== JAMES'S OWN NARRATIVES ON THESE MATTERS (within {PRIOR_DAYS} days) ==")
        S.append("Resident names and units often appear here first. Keep his naming.")
        for mn in self.matters:
            rows = self.prior.get(mn) or []
            if rows:
                S.append(f"{mn}:")
                for d, narr in rows[:12]:
                    S.append(f"   {d}: {narr}")
        S.append("")
        S.append("== COLLEAGUES ==")
        S.append("Anyone @gallagherllp.com is a Gallagher colleague, never the client or "
                 f"advisee: {COLLEAGUES}.")
        return "\n".join(S)

    # ----------------------------------------------------------------- tools
    def read_email(self, refs: list[str]) -> str:
        out = []
        for r in refs[:6]:
            e = self.email(r)
            if e is None:
                out.append(f"[{r}] no such email ref on this sheet.")
                continue
            body = clean_text(e.get("body"))
            if len(body) > READ_BODY_CHARS:
                body = body[:READ_BODY_CHARS] + " …"
            msgs = self.thread_fn(e.get("conversation_id")) if self.thread_fn else []
            part = [f"[{self.ref(e)}] {e['date']} {_hhmm(e)}  {e.get('subject') or ''}",
                    f"to: {e.get('to') or ''}",
                    f"cc: {e.get('cc') or ''}",
                    "---- full body (James's text, then any quoted history) ----",
                    body]
            if msgs:
                part += ["---- the conversation, oldest first ----",
                         self.chain_text(e, READ_CHAIN_CHARS, 2500)]
            out.append("\n".join(part))
        return "\n\n".join(out)

    def search_mail(self, query: str, date_from: str, date_to: str) -> str:
        q = (query or "").strip().lower()
        if len(q) < 3:
            return "Query too short: give at least three characters."
        try:
            lo, hi = _d(date_from), _d(date_to)
        except ValueError:
            return "Dates must be YYYY-MM-DD."
        if hi < lo:
            lo, hi = hi, lo
        if (hi - lo).days > SEARCH_MAX_DAYS:
            hi = lo + timedelta(SEARCH_MAX_DAYS)
        hits = [e for e in self.all_mail
                if lo.isoformat() <= e["date"] <= hi.isoformat() and q in e["hay"]]
        hits.sort(key=lambda e: e.get("sent_at") or "")
        if not hits:
            return f'No sends between {lo} and {hi} mention "{query}".'
        out = [f'{len(hits)} send(s) between {lo} and {hi} mention "{query}"'
               + (f"; first {SEARCH_MAX_HITS} shown" if len(hits) > SEARCH_MAX_HITS else "")
               + ":"]
        for e in hits[:SEARCH_MAX_HITS]:
            out += self._mail_line(e, 300)
        return "\n".join(out)


def prior_narratives(rows, matters, dates, entries=None, before: str | None = None) -> dict:
    """matter -> [(date, narrative)] within PRIOR_DAYS of the sheet's dates.

    rows: (work_date, matter, narrative) from te.db's narratives. `before`
    drops anything on or after that date, so a backtest can't show the
    drafter the very narratives it is being scored against. The timecard's
    own filled entries (`entries`) are always fair: they existed when the
    export was taken."""
    lo = (_d(min(dates)) - timedelta(PRIOR_DAYS)).isoformat()
    hi = (_d(max(dates)) + timedelta(PRIOR_DAYS)).isoformat()
    want = set(matters)
    pool = [(d, mn, narr) for d, mn, narr in rows
            if not (before and (d or "") >= before)]
    pool += [(e["work_date"], e["matter"], e.get("narrative")) for e in entries or []]
    seen, out = set(), {}
    for d, mn, narr in pool:
        if mn not in want or not narr or len(narr.strip()) < 12:
            continue
        if not lo <= (d or "") <= hi:
            continue
        key = (d, mn, narr.strip())
        if key in seen:
            continue
        seen.add(key)
        out.setdefault(mn, []).append((d, narr.strip()))
    for mn in out:
        out[mn].sort(key=lambda x: x[0], reverse=True)
    return out
