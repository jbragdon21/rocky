# Rocky — Iteration 1

A read-only program that watches James's Outlook inbox, classifies each new
email as either a "Remy request" or "not a Remy request," and logs every
classification to a JSONL file for review.

## What this version does

- Polls your Outlook inbox every 5 minutes via Microsoft Graph
- For each new email, sends it to Claude with a classifier prompt
- Logs the classification (yes/no, confidence, reasoning) to `classifications.jsonl`
- Logs a one-line summary to the console

## What this version does NOT do

- Does NOT read attachment contents (notes filenames only)
- Does NOT draft any replies
- Does NOT send any mail
- Does NOT modify your inbox in any way
- Does NOT run Remy

It only requests the `Mail.Read` permission. It cannot create drafts, cannot
send mail, cannot modify your mailbox. The capability is absent at the
credential level.

## Setup (one-time)

### 1. Install Python 3.11 or 3.12

If you don't have it: https://www.python.org/downloads/

### 2. Install dependencies

From this folder:

```
pip install -r requirements.txt
```

### 3. Register an Azure AD app

This is a one-time setup. Either you do this yourself (if you have permission
in your tenant) or your IT admin does it. The app registration is what gives
Rocky permission to read your mail.

In the Azure portal (or Entra portal):

1. Go to "App registrations" → "New registration"
2. Name: "Rocky" (or whatever you prefer)
3. Supported account types: "Accounts in this organizational directory only"
4. Redirect URI: leave blank (we use device code flow)
5. After creation, on the app's "Authentication" page:
   - Set "Allow public client flows" to YES
6. On the app's "API permissions" page, add:
   - Microsoft Graph → Delegated → `Mail.Read`
   - Click "Grant admin consent" (or have your admin do it)
7. Copy the **Application (client) ID** and the **Directory (tenant) ID**
   from the app's overview page — you'll need them for config.json.

### 4. Get an Anthropic API key

If you don't have one: https://console.anthropic.com/

Create a key, copy it.

### 5. Create config.json

Copy `config.example.json` to `config.json` and fill in your values:

```json
{
  "client_id": "the GUID from Azure",
  "tenant_id": "the GUID from Azure",
  "user_email": "jbragdon@gallagherllp.com",
  "anthropic_api_key": "sk-ant-..."
}
```

`config.json` should NOT be checked into version control. The included
`config.example.json` is a template.

## Running

```
python rocky.py
```

On first run:
- Rocky will print a code and a URL
- Open the URL in your browser, paste the code, sign in with your work account
- Authentication is cached locally; subsequent runs are silent

While running:
- Console shows a one-line summary for each email classified
- `classifications.jsonl` accumulates the full structured records
- `rocky.log` accumulates the operational log
- `state/last_check.json` tracks where he left off

To stop: Ctrl+C. To restart: `python rocky.py` again. He picks up where
he left off.

## Reviewing classifications

The fastest way is the included **review tool**, which walks you through
classifications interactively and tracks accuracy over time:

```
python review.py
```

Each classification appears in your terminal with the email metadata, what
Rocky decided, his confidence, and his reasoning. Press `y` if he got
it right, `n` if wrong (with an optional note explaining why), `s` to skip,
or `q` to save progress and quit. Verdicts are saved to `review.jsonl` so
your grading persists across sessions.

Useful flags:

```
python review.py --stats        # Just show accuracy stats, no review
python review.py --recent 20    # Review only the 20 most recent
python review.py --remy-only    # Skip the ones he said weren't Remy
python review.py --export       # Write a markdown report of misclassifications
python review.py --redo         # Re-review entries you've already graded
```

After 20-30 reviews you'll have a clear picture of where the classifier
is reliable and where it isn't. The `--export` flag is especially useful —
it generates a markdown file grouping misclassifications by type (false
positives vs. false negatives) so you can spot patterns to refine.

For raw access, `classifications.jsonl` is plain JSONL and opens in any
text editor.

## Iterating on the classifier

Two ways to refine his judgment:

1. **Edit `instructions.md`** — add plain-English rules. He picks them up
   on the next email, no restart needed.

2. **Edit the classifier prompt in `rocky.py`** — for deeper changes
   to his reasoning. Look for `CLASSIFIER_SYSTEM_PROMPT`. Restart after
   changes.

Start with `instructions.md` — easier and reversible.

## Troubleshooting

**"Token cache deserialize" error on first run:** Normal — there's no
cache yet. The device code flow will start.

**"Failed to start device flow":** Usually means the Azure app registration
doesn't have "Allow public client flows" enabled. See setup step 3.5.

**"Could not parse classifier response as JSON":** Claude occasionally
wraps its response in markdown or adds prose. The code attempts to handle
this, but if it fails consistently, the prompt may need tightening. Check
the `_raw_response` field in the bad classification record.

**Rocky keeps re-processing the same emails:** Check `state/last_check.json`.
If it's not being updated, something is wrong with the timestamp parsing.
Delete the file to start fresh from N hours ago.

**Polling takes too long / API rate limits:** Adjust `POLL_INTERVAL_SECONDS`
in `rocky.py`. Default is 300 (5 minutes); shorter values make Rocky more
responsive at the cost of more Graph API calls.

## Stopping safely

Ctrl+C is safe. State is written after each email is processed, so you
won't lose progress. The token cache is also saved.

## What's next

Once the classifier is reliably right on the patterns that come up in your
practice (a week or two of observation), iteration 2 adds attachment
reading — actually pulling lease/ledger contents into Claude's view.
Iteration 3 adds Remy invocation. Each iteration is added incrementally
without breaking the previous one.
