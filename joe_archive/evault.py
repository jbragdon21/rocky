"""--joe-archive --vault-check: can Rocky reach the full copies behind Joe's
Enterprise Vault shortcuts? Numbers only: no subject, name or line of Joe's
mail is printed, so James can paste the output. Writes vault_check.json and
nothing else.

The 11/5 census found 32,439 of Joe's sends (2019-03-18 to 2023-07-14) held
in his mailbox only as Vault shortcuts. Later on 11/5 IT gave the Rocky app
access to Joe's Vault. Graph reads only the primary mailbox. It can't read an
In-Place Archive (the mailbox export API's GA in May 2026 left archives out)
or an Enterprise Vault server, so this check tests every route Rocky has:

  1. The Graph permissions in Rocky's token (its roles claim): did IT add one?
  2. Shortcuts and full sends sampled across the years: how much text a
     shortcut keeps, next to full sends, and whether it lists attachments.
  3. Each sampled shortcut looked up across the whole mailbox by
     internetMessageId: has a full copy turned up anywhere Graph reads?
  4. Graph's archive-mailbox routes (/users/{id}/settings/exchange and the
     mailbox export API's folder listing): an archive mailbox, and whether
     Rocky may read it.
  5. Vault links in the shortcuts: the Vault server's host, whether it
     resolves from this laptop, and how it answers an anonymous request.
"""

import base64
import json
import re
import socket
import statistics
import warnings
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import requests

from . import census, graph, ingest, store
from .graph import GRAPH, GraphError

GRAPH_BETA = "https://graph.microsoft.com/beta"
CHECK_NAME = "vault_check.json"
WORKERS = 4                     # Exchange allows 4 concurrent requests per mailbox
# One page from each: the oldest sends, each Vault year, and full sends after.
SAMPLE_STARTS = (None, "2020-06-01", "2021-06-01", "2022-06-01", "2023-03-01",
                 "2023-09-01", "2024-06-01")
SAMPLE_PAGE = 50
BUCKETS = ((0, 0), (1, 250), (251, 500), (501, 1000), (1001, 2000), (2001, 5000),
           (5001, None))
# Permissions that would reach past the primary mailbox's normal mail routes
# (the mailbox export API). Reading exchangeSettings needs User.Read.All.
ARCHIVE_ROLES = ("MailboxItem.", "MailboxFolder.")
# What a Vault link looks like: on-premises /EnterpriseVault/ pages, or
# Veritas's hosted archive.
VAULT_URL_MARKS = ("enterprisevault", "veritas", "archivecloud")
_URL = re.compile(r"https?://[^\s\"'<>()\[\]]+", re.I)
_ATTACH_LIST = re.compile(r"<<[^<>\n]{1,200}>>")   # "<<Lease.pdf>>" in a shortcut's text
HOSTS_PROBED = 3
# The census (11/5, before IT's change) found a full copy for 100 of 32,539
# shortcut sends. A sample far above that rate means the Vault mail came back.
BACK_IN_MAILBOX = 0.10


# =============================================================================
# 1. Rocky's permissions
# =============================================================================

def token_claims(token: str) -> dict:
    """The JWT payload, unverified (it only reports what the token says)."""
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}


def permissions(keeper) -> list[str]:
    claims = token_claims(keeper.get())
    if claims.get("roles"):
        return sorted(claims["roles"])
    return sorted((claims.get("scp") or "").split())


# =============================================================================
# 2. What a shortcut keeps
# =============================================================================

def _bucket(n: int) -> str:
    for lo, hi in BUCKETS:
        if n >= lo and (hi is None or n <= hi):
            return f"{lo:,}+" if hi is None else (f"{lo:,}" if lo == hi else f"{lo:,}-{hi:,}")
    return "?"


def _text(msg: dict) -> str:
    return ((msg.get("body") or {}).get("content") or "").replace("\r\n", "\n").strip()


def _group_stats(msgs: list[dict]) -> dict:
    lens = [len(_text(m)) for m in msgs]
    owns = [len(store.split_body(_text(m))[0]) for m in msgs]
    if not msgs:
        return {"n": 0}
    return {
        "n": len(msgs),
        "chars_median": int(statistics.median(lens)),
        "chars_p90": sorted(lens)[round(0.9 * (len(lens) - 1))],
        "chars_max": max(lens),
        "own_median": int(statistics.median(owns)),
        "buckets": {_bucket(lo): sum(1 for n in lens if _bucket(n) == _bucket(lo))
                    for lo, _hi in BUCKETS},
        "has_attachments": sum(1 for m in msgs if m.get("hasAttachments")),
        "lists_attachments": sum(1 for m in msgs if _ATTACH_LIST.search(_text(m))),
        "mentions_archive": sum(1 for m in msgs
                                if re.search(r"vault|archived", _text(m), re.I)),
    }


def sample(keeper, mb, starts=SAMPLE_STARTS, page=SAMPLE_PAGE) -> dict:
    """One listing page (text bodies, as the backfill reads them) from each
    start date, split into shortcuts and full sends."""
    shortcuts, full, seen, skipped = [], [], set(), 0
    for start in starts:
        since = f"{start}T00:00:00Z" if start else None
        for batch in graph.iter_pages(keeper, mb, "sentitems", since, top=page, max_pages=1):
            for msg in batch:
                key = store.msg_key(msg)
                if key in seen:
                    continue
                seen.add(key)
                mclass = graph.message_class(msg)
                if ingest.skip_reason(msg, mclass):
                    skipped += 1
                elif census.is_shortcut(mclass):
                    shortcuts.append(msg)
                else:
                    full.append(msg)
    return {"shortcuts": shortcuts, "full": full, "skipped": skipped}


# =============================================================================
# 3. A full copy anywhere Graph reads
# =============================================================================

def _odata_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def copies(keeper, mb, imid: str) -> list[dict]:
    """Every copy of one send in the primary mailbox, any folder."""
    data = graph.get_json(keeper, f"{GRAPH}/users/{mb}/messages",
                          {"$filter": f"internetMessageId eq {_odata_str(imid)}",
                           "$select": "id,parentFolderId,body", "$top": "10",
                           "$expand": graph.LIST_EXPAND}, graph.TEXT_BODY)
    return data.get("value") or []


def lookup(keeper, mb, shortcuts: list[dict]) -> dict:
    """For each shortcut: does the mailbox hold a full copy? For pairs found,
    how much of the full text the shortcut kept."""
    def one(msg):
        imid = (msg.get("internetMessageId") or "").strip()
        if not imid:
            return "no_message_id", None
        try:
            found = copies(keeper, mb, imid)
        except GraphError as e:
            return f"failed: {e.status or 'no response'}", None
        fulls = [c for c in found if not census.is_shortcut(graph.message_class(c))]
        if not fulls:
            return "only_shortcut", None
        full_len = max(len(_text(c)) for c in fulls)
        return "full_copy", (len(_text(msg)), full_len)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(one, shortcuts))
    counts = Counter(r for r, _ in results)
    pairs = [p for _, p in results if p]
    kept = [round(s / f, 2) for s, f in pairs if f]
    return {"looked_up": len(shortcuts), **dict(counts),
            "pairs_kept_ratio_median": statistics.median(kept) if kept else None,
            "pairs_whole_text": sum(1 for s, f in pairs if f and s >= 0.98 * f)}


# =============================================================================
# 4. Graph's archive-mailbox routes
# =============================================================================

def _mailbox_ids(data: dict) -> list[tuple[str, str]]:
    """(label, id) for each mailbox an exchangeSettings answer names."""
    items = data["value"] if isinstance(data.get("value"), list) else [data]
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        for k, v in it.items():
            if isinstance(v, str) and v and k.lower().endswith("mailboxid"):
                out.append((k, v))
        kind = it.get("mailboxType") or it.get("type")
        if kind and isinstance(it.get("id"), str):
            out.append((f"{kind} mailbox", it["id"]))
    return out


def _status(resp) -> str:
    if resp.status_code == 200:
        return "200 OK"
    return f"{resp.status_code} {graph.error_detail(resp)[:160]}"


def archive_routes(keeper, mb) -> list[dict]:
    out = []
    for ver, base in (("v1.0", GRAPH), ("beta", GRAPH_BETA)):
        resp = graph.get(keeper, f"{base}/users/{mb}/settings/exchange")
        row = {"version": ver, "settings": _status(resp), "mailboxes": []}
        if resp.status_code == 200:
            try:
                ids = _mailbox_ids(resp.json())
            except ValueError:
                ids = []
            for label, mid in ids[:4]:
                r = graph.get(keeper, f"{base}/admin/exchange/mailboxes/{mid}/folders",
                              params={"$top": "1"})
                row["mailboxes"].append({"label": label,
                                         "archive": "archive" in label.lower(),
                                         "folders": _status(r)})
        out.append(row)
    return out


# =============================================================================
# 5. The Vault server named in the shortcuts
# =============================================================================

def vault_links(texts: list[str]) -> Counter:
    """scheme://host/first-segment for each Vault link (the query strings,
    which carry archive and saveset ids, are dropped)."""
    stems = Counter()
    for t in texts:
        for url in set(_URL.findall(t or "")):
            if not any(mark in url.lower() for mark in VAULT_URL_MARKS):
                continue
            parts = urlsplit(url.rstrip(".,;"))
            if not parts.hostname:
                continue
            seg = parts.path.strip("/").split("/")[0]
            stems[f"{parts.scheme.lower()}://{parts.hostname.lower()}/{seg}"] += 1
    return stems


def html_bodies(keeper, mb, top: int = 25) -> list[str]:
    """The oldest sends' HTML bodies: the text form can drop link targets."""
    data = graph.get_json(keeper, f"{GRAPH}/users/{mb}/mailFolders/sentitems/messages",
                          {"$top": str(top), "$select": "id,body",
                           "$orderby": "sentDateTime asc"})
    return [(m.get("body") or {}).get("content") or "" for m in data.get("value") or []]


def probe_host(stem: str) -> dict:
    parts = urlsplit(stem)
    host = parts.hostname or ""
    row = {"stem": stem}
    try:
        socket.getaddrinfo(host, None)
        row["resolves"] = True
    except OSError as e:
        row.update(resolves=False, error=f"{type(e).__name__}")
        return row
    # Anonymous: no credential is sent. An internal certificate Python doesn't
    # trust is noted, then the status is read anyway.
    for verify in (True, False):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                r = requests.get(stem + "/", timeout=10, allow_redirects=False,
                                 verify=verify)
        except requests.exceptions.SSLError:
            row["certificate"] = "not trusted by Python"
            continue
        except requests.RequestException as e:
            row["error"] = f"{type(e).__name__}: {str(e)[:120]}"
            return row
        row["status"] = r.status_code
        auth = r.headers.get("WWW-Authenticate") or ""
        row["auth"] = sorted({a.strip().split(" ")[0] for a in auth.split(",") if a.strip()})
        r.close()
        return row
    return row


# =============================================================================
# The check
# =============================================================================

def run(config: dict, root: Path, out=print, probe_hosts: bool = True) -> dict:
    state = ingest.load_state(root)
    keeper, mb = ingest.resolve_access(config, root, state)
    out(f"Joe's Email Archive: Vault check of {mb} ({keeper.path} path). "
        f"Numbers only; nothing is downloaded.")
    report = {"checked_utc": store.now_utc(), "mailbox": mb, "auth": keeper.path}

    perms = permissions(keeper)
    report["permissions"] = perms
    extra = [p for p in perms if p.startswith(ARCHIVE_ROLES)]
    out("")
    out(f"1. Rocky's Graph permissions ({keeper.path} token): " + (", ".join(perms) or "none"))
    out("   Archive-related: " + (", ".join(extra) if extra else
                                  "none (no MailboxItem.* or MailboxFolder.* permission)"))
    out("   A grant on the Vault server itself doesn't show here: Graph tokens carry "
        "Graph permissions only.")

    s = sample(keeper, mb)
    sc, full = _group_stats(s["shortcuts"]), _group_stats(s["full"])
    report["sample"] = {"shortcuts": sc, "full": full, "skipped": s["skipped"],
                        "starts": [x or "oldest" for x in SAMPLE_STARTS]}
    out("")
    out(f"2. What a shortcut keeps: {sc['n']} shortcuts and {full['n']} full sends sampled "
        f"({len(SAMPLE_STARTS)} pages from 2019 to 2024).")
    out("                        shortcuts   full sends")
    for label, key in (("text, median chars", "chars_median"), ("text, 90th pct", "chars_p90"),
                       ("text, longest", "chars_max"), ("Joe's own text, median", "own_median"),
                       ("has attachments", "has_attachments"),
                       ("lists <<attachments>>", "lists_attachments"),
                       ("says vault/archived", "mentions_archive")):
        out(f"   {label:<22} {sc.get(key, '-'):>9}   {full.get(key, '-'):>10}")
    out("   text length       shortcuts   full sends")
    for lo, _hi in BUCKETS:
        b = _bucket(lo)
        out(f"   {b:<17} {sc.get('buckets', {}).get(b, 0):>9}   "
            f"{full.get('buckets', {}).get(b, 0):>10}")

    found = lookup(keeper, mb, s["shortcuts"])
    report["lookup"] = found
    out("")
    out(f"3. Full copies anywhere Graph reads, for the {found['looked_up']} sampled shortcuts:")
    out(f"   {found.get('full_copy', 0)} with a full copy in the mailbox, "
        f"{found.get('only_shortcut', 0)} only as a shortcut"
        + (f"; {found['no_message_id']} have no message id" if found.get("no_message_id")
           else "")
        + "".join(f"; {v} lookups {k}" for k, v in found.items() if k.startswith("failed")))
    if found.get("pairs_kept_ratio_median") is not None:
        out(f"   In those pairs the shortcut kept a median {found['pairs_kept_ratio_median']:.0%} "
            f"of the full text ({found['pairs_whole_text']} kept all of it).")

    routes = archive_routes(keeper, mb)
    report["archive_routes"] = routes
    out("")
    out("4. Graph's archive-mailbox routes:")
    for r in routes:
        out(f"   {r['version']:<5} /users/{{id}}/settings/exchange: {r['settings']}")
        if r["settings"].startswith("200") and not r["mailboxes"]:
            out("         names no mailbox id")
        for m in r["mailboxes"]:
            out(f"         {m['label']}{' (ARCHIVE)' if m['archive'] else ''}: "
                f"folder listing {m['folders']}")

    texts = [_text(m) for m in s["shortcuts"]]
    try:
        texts += html_bodies(keeper, mb)
    except GraphError as e:
        out(f"   (couldn't read HTML bodies for Vault links: {e})")
    stems = vault_links(texts)
    hosts = [probe_host(st) for st, _n in stems.most_common(HOSTS_PROBED)] if probe_hosts else []
    report["vault_links"] = {"stems": dict(stems), "probed": hosts}
    out("")
    out("5. Vault links in the shortcuts: " + (", ".join(
        f"{st} x{n}" for st, n in stems.most_common()) or "none found"))
    for h in hosts:
        if not h.get("resolves"):
            out(f"   {h['stem']}: the host doesn't resolve from this laptop ({h.get('error')})")
        elif "status" in h:
            out(f"   {h['stem']}: resolves; anonymous request answered {h['status']}"
                + (f", sign-in {'/'.join(h['auth'])}" if h.get("auth") else ""))
        else:
            out(f"   {h['stem']}: resolves; no answer ({h.get('error')})")

    report["verdict"] = verdict(report)
    out("")
    out("Verdict:")
    for line in report["verdict"]:
        out(f"  - {line}")
    root.mkdir(parents=True, exist_ok=True)
    (root / CHECK_NAME).write_text(json.dumps(report, indent=2), encoding="utf-8")
    out(f"Saved {root / CHECK_NAME}")
    return report


def verdict(report: dict) -> list[str]:
    lines = []
    found = report["lookup"]
    n_full, n = found.get("full_copy", 0), found["looked_up"]
    if n and n_full / n >= BACK_IN_MAILBOX:
        lines.append(f"{n_full} of {n} sampled shortcuts have a full copy Graph can read. "
                     f"Before IT's change only 100 of 32,539 did, so the Vault mail looks to "
                     f"be back in the mailbox and will pull. Rerun --census for the whole "
                     f"count.")
    elif n_full:
        lines.append(f"Only {n_full} of {n} sampled shortcuts have a full copy Graph can read, "
                     f"about the rate before IT's change (100 of 32,539, filed copies). As "
                     f"things stand the Vault mail won't pull.")
    elif n:
        lines.append(f"None of the {found['looked_up']} sampled shortcuts has a full copy "
                     f"anywhere Graph reads. As things stand the Vault mail won't pull.")
    else:
        lines.append("No shortcuts were sampled, so the lookup proves nothing.")
    sc, full = report["sample"]["shortcuts"], report["sample"]["full"]
    if sc.get("n"):
        lines.append(f"A shortcut keeps a median {sc['chars_median']:,} characters of text "
                     f"(longest {sc['chars_max']:,}); full sends run a median "
                     f"{full.get('chars_median', 0):,}. {sc['has_attachments']} of {sc['n']} "
                     f"shortcuts carry attachments.")
    routes = report["archive_routes"]
    readable = [m for r in routes for m in r["mailboxes"]
                if m["archive"] and m["folders"].startswith("200")]
    listed = [m for r in routes for m in r["mailboxes"] if m["archive"]]
    if readable:
        lines.append("Graph lists an archive mailbox and Rocky can read its folders: "
                     "that route is worth building on.")
    elif listed:
        lines.append("Graph lists an archive mailbox, but Rocky can't read it "
                     "(the status above says why).")
    else:
        lines.append("Graph names no archive mailbox Rocky can open.")
    if not [p for p in report["permissions"] if p.startswith(ARCHIVE_ROLES)]:
        lines.append("Rocky's token has no archive-related Graph permission, so whatever IT "
                     "changed was outside Graph (on the Vault server or in Exchange).")
    return lines
