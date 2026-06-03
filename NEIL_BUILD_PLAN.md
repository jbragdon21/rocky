# NEIL — Build Plan

**Networked Efficient Intelligent Litigation**

A Python program that takes a complaint, analyzes it through a multi-stage Claude API pipeline with CoCounsel legal research, and produces a motion to dismiss and initial discovery requests. The lawyer stays in the loop via Teams chat at each stage.

---

## What NEIL does

NEIL automates the opening defensive workflow in federal civil litigation:

1. **Reads a complaint** and extracts facts, claims, parties, jurisdiction, and legal theories
2. **Asks the lawyer questions** via Teams chat to clarify facts and strategic intent
3. **Runs legal research** via Westlaw CoCounsel to identify dismissal grounds, supporting authority, and procedural requirements
4. **Asks the lawyer again** to confirm defense strategy based on what research found
5. **Drafts a motion to dismiss** using Jinja-formatted templates with court-specific formatting
6. **Generates initial discovery requests** (interrogatories, RFPs, RFAs) from the accumulated analysis

Rocky triggers NEIL by detecting a NEIL request in its inbox and launching the process.

---

## Pipeline overview

```
                         ┌─────────────────────┐
                         │  Complaint PDF       │
                         │  (forwarded to Rocky)│
                         └──────────┬───────────┘
                                    │
                         ┌──────────▼───────────┐
                         │  STAGE 1: EXTRACTION  │
                         │  Claude API call #1   │
                         │  + instructions file  │
                         └──────────┬───────────┘
                                    │
                         ┌──────────▼───────────┐
                         │  TEAMS Q&A #1         │
                         │  Clarify facts,       │
                         │  strategic direction   │
                         │  (pause & poll)        │
                         └──────────┬───────────┘
                                    │
                         ┌──────────▼───────────┐
                         │  STAGE 2: RESEARCH    │
                         │  Claude API call #2   │
                         │  + CoCounsel MCP      │
                         └──────────┬───────────┘
                                    │
                         ┌──────────▼───────────┐
                         │  TEAMS Q&A #2         │
                         │  Confirm strategy,    │
                         │  select defenses      │
                         │  (pause & poll)        │
                         └──────────┬───────────┘
                                    │
                ┌───────────────────┴───────────────────┐
                │                                       │
     ┌──────────▼───────────┐              ┌───────────▼──────────┐
     │  STAGE 3: MOTION     │              │  STAGE 4: DISCOVERY  │
     │  Claude API call #3  │              │  Claude API call #4  │
     │  + Jinja template    │              │  + Jinja template    │
     └──────────┬───────────┘              └───────────┬──────────┘
                │                                       │
                └───────────────────┬───────────────────┘
                                    │
                         ┌──────────▼───────────┐
                         │  OUTPUT               │
                         │  → Case folder        │
                         │  → Teams notification  │
                         └───────────────────────┘
```

---

## Deployment model

**NEIL runs on the Rocky laptop.** Same machine, same infrastructure. NEIL is a separate executable invoked by Rocky when a NEIL request arrives.

**NEIL's project folder lives on shared OneDrive.** Instructions, Jinja templates, and output all live in a shared OneDrive folder so other attorneys can access templates, review output, and (eventually) trigger their own NEIL runs.

```
OneDrive - gejlaw.com\NEIL\
├── config.json                  # CoCounsel API key, Teams chat ID, model settings
├── instructions\                # Prompt instructions for each Claude API call
│   ├── stage1_extraction.md     # How to analyze a complaint
│   ├── stage2_research.md       # How to frame research queries for CoCounsel
│   ├── stage3_motion.md         # How to draft a motion to dismiss
│   └── stage4_discovery.md      # How to generate discovery requests
├── templates\                   # Jinja templates for document formatting
│   ├── motion_to_dismiss.j2    # Court-formatted MTD template
│   ├── interrogatories.j2      # Interrogatory set template
│   ├── rfp.j2                  # Request for Production template
│   └── rfa.j2                  # Request for Admission template
├── runs\                        # Per-run working directories
│   └── {RRID}-{timestamp}\
│       ├── run_state.json       # Pipeline state (current stage, accumulated context)
│       ├── complaint.pdf        # Input complaint
│       ├── extraction.json      # Stage 1 output
│       ├── research.json        # Stage 2 output (CoCounsel results)
│       ├── teams_log.jsonl      # All Teams Q&A messages
│       ├── motion_draft.docx    # Stage 3 output
│       ├── interrogatories.docx # Stage 4 output
│       ├── rfp.docx             # Stage 4 output
│       └── rfa.docx             # Stage 4 output
└── neil.log                     # Operational log
```

**Rocky laptop runtime files (local, `C:\Rocky\`):**

NEIL's executable lives alongside Rocky's. No separate install — NEIL is another command Rocky can invoke:

```
C:\Rocky\
├── rocky.exe                    # Existing
├── neil.exe                     # NEIL executable (built same way as rocky.exe)
└── state\
    └── neil_active_runs.json    # Tracks which NEIL runs are in progress
```

**Build and deploy:** Same as Rocky. `python build_neil.py` → `dist/neil.exe` → copied to `OneDrive - gejlaw.com\Program Files\neil.exe` → syncs to Rocky laptop.

---

## Stage 1: Complaint extraction

**Input:** Complaint PDF (extracted text via `pdfplumber` or `PyMuPDF`)

**Claude API call:** Single call with the complaint text + `instructions/stage1_extraction.md`

**Output schema (`extraction.json`):**

```json
{
  "case_caption": "Smith v. Jones Property Management LLC",
  "court": "U.S. District Court for the Eastern District of Virginia",
  "case_number": "1:26-cv-00123",
  "jurisdiction_basis": "federal_question",
  "parties": {
    "plaintiffs": [
      {"name": "John Smith", "role": "tenant", "represented_by": "Jane Doe, Esq."}
    ],
    "defendants": [
      {"name": "Jones Property Management LLC", "role": "property_manager", "our_client": true}
    ]
  },
  "claims": [
    {
      "count": 1,
      "theory": "Fair Housing Act - Discriminatory Refusal to Rent",
      "statute": "42 U.S.C. § 3604(a)",
      "factual_basis": "Plaintiff alleges defendant refused to rent based on familial status",
      "elements": ["protected class membership", "applied and qualified", "rejected", "unit remained available"],
      "potential_defenses": ["legitimate nondiscriminatory reason", "failure to state a claim", "statute of limitations"]
    }
  ],
  "key_facts": [
    {"fact": "Application submitted March 15, 2026", "paragraph": 12, "disputed": false},
    {"fact": "Denial letter sent March 22, 2026", "paragraph": 15, "disputed": true}
  ],
  "deadlines": {
    "answer_due": null,
    "filing_date": "2026-05-01"
  },
  "questions_for_lawyer": [
    "Was there a legitimate nondiscriminatory reason for the denial?",
    "Do we have the denial letter and application file?",
    "Has the client been served? When does the answer deadline run?"
  ]
}
```

The `questions_for_lawyer` array feeds directly into Teams Q&A #1.

---

## Stage 2: CoCounsel legal research

**Input:** Extracted facts + claims + lawyer's Q&A answers

**Integration model:** Claude API call with CoCounsel tools defined via `tools` parameter. When Claude invokes a CoCounsel tool, NEIL's Python code calls the Westlaw CoCounsel API and returns the results.

**CoCounsel tool definitions:**

```python
cocounsel_tools = [
    {
        "name": "search_case_law",
        "description": "Search Westlaw for case law on a legal issue",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language legal research query"},
                "jurisdiction": {"type": "string"},
                "date_range": {"type": "string", "description": "e.g. 'last 10 years'"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "search_statutes",
        "description": "Search for relevant statutes and regulations",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "jurisdiction": {"type": "string"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "analyze_motion_standards",
        "description": "Research the standard for a specific motion type in a jurisdiction",
        "input_schema": {
            "type": "object",
            "properties": {
                "motion_type": {"type": "string", "description": "e.g. '12(b)(6) motion to dismiss'"},
                "jurisdiction": {"type": "string"}
            },
            "required": ["motion_type"]
        }
    }
]
```

**How the tool loop works:**

```python
# Simplified — actual implementation handles retries, token limits, etc.
messages = [{"role": "user", "content": research_prompt}]

while True:
    response = client.messages.create(
        model="claude-sonnet-4-5-20250514",
        tools=cocounsel_tools,
        messages=messages,
    )

    if response.stop_reason == "end_turn":
        break  # Claude is done researching

    # Handle tool calls
    for block in response.content:
        if block.type == "tool_use":
            result = call_cocounsel_api(block.name, block.input)
            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": block.id, "content": result}]
            })
```

**Output (`research.json`):** Claude synthesizes all CoCounsel results into a structured research memo — key cases, statutes, standards of review, recommended dismissal arguments ranked by strength, and questions for the lawyer about strategy.

---

## Stage 3: Motion to dismiss

**Input:** Extraction + research + all lawyer Q&A answers + `instructions/stage3_motion.md`

**Claude API call:** Single call. The instructions file contains detailed guidance on:
- Motion structure (caption, introduction, statement of facts, legal standard, argument, conclusion)
- Tone and style (firm but not overwrought, cite-heavy, EDVA/DC/MD conventions)
- Citation format (Bluebook)
- What NOT to do (don't over-argue facts at the MTD stage, don't preview discovery disputes)

**Output:** Structured JSON with motion sections that Jinja assembles into a `.docx`:

```json
{
  "caption": { "court": "...", "parties": "...", "case_number": "..." },
  "title": "DEFENDANT'S MOTION TO DISMISS PURSUANT TO FED. R. CIV. P. 12(b)(6)",
  "introduction": "...",
  "statement_of_facts": "...",
  "legal_standard": "...",
  "arguments": [
    {
      "heading": "I. COUNT ONE FAILS TO STATE A CLAIM UNDER THE FAIR HOUSING ACT",
      "subheadings": [
        {"heading": "A. Plaintiff Has Not Alleged...", "body": "..."}
      ]
    }
  ],
  "conclusion": "...",
  "signature_block": { "attorney": "...", "firm": "Gallagher LLP", "bar_number": "..." },
  "proposed_order": "..."
}
```

**Jinja rendering:** `templates/motion_to_dismiss.j2` produces a `.docx` via `python-docx` with Jinja2 templating (using `docxtpl`, which wraps both libraries). Court-specific formatting: 1-inch margins, Times New Roman 12pt, double-spaced body, single-spaced block quotes, page numbers.

---

## Stage 4: Discovery requests

**Input:** Same accumulated context as Stage 3

**Claude API call:** Single call with `instructions/stage4_discovery.md`. Instructions specify:
- Number and scope of interrogatories (stay under the default limit, 25 for federal)
- RFP categories aligned with the claims and defenses
- RFA targets (undisputed facts to narrow issues, authenticity of key documents)
- Proportionality considerations

**Output:** Three `.docx` files via Jinja:
- Interrogatories (`interrogatories.j2`)
- Requests for Production (`rfp.j2`)
- Requests for Admission (`rfa.j2`)

Stages 3 and 4 can run in parallel since they draw from the same accumulated context.

---

## Teams chat integration

**Microsoft Graph API endpoints:**

| Operation | Endpoint | Permission |
|---|---|---|
| Create 1:1 chat | `POST /chats` | `Chat.ReadWrite` |
| Send message | `POST /chats/{chatId}/messages` | `ChatMessage.Send` |
| Poll for replies | `GET /chats/{chatId}/messages` | `Chat.Read` |

**How it works:**

1. On first NEIL run, create a 1:1 chat between `rocky@gallagherllp.com` and `jbragdon@gallagherllp.com` (or reuse existing). Store `chatId` in config.
2. When NEIL needs to ask questions, it sends a formatted Teams message with the questions numbered.
3. NEIL polls `GET /chats/{chatId}/messages` every 30 seconds, watching for a reply from James after the question message timestamp.
4. On reply, NEIL parses the response, logs it to `teams_log.jsonl`, and resumes the pipeline.
5. Timeout after 24 hours — NEIL sends a reminder at 4 hours, abandons at 24 with a "timed out, re-trigger when ready" message.

**Message format (outbound):**

```
🔬 NEIL — Smith v. Jones (RRID-0042)
Stage 1 complete. I need your input before running legal research.

1. Was there a legitimate nondiscriminatory reason for the denial?
2. Do we have the denial letter and application file?
3. Has the client been served? When does the answer deadline run?

Reply here with your answers (number them to match).
```

**Permissions required (new for Rocky's app registration):**

- `Chat.ReadWrite` — create and read chats
- `ChatMessage.Send` — send messages in chats

These are delegated permissions on Rocky's account. IT will need to add them to the existing Azure AD app registration.

---

## Rocky integration

**Trigger detection:** Rocky's email classifier gets a new `project_category` value: `neil_request`. The classifier identifies NEIL requests by:
- A complaint PDF is attached or referenced
- The forwarding message includes "NEIL", "motion to dismiss", "MTD", or similar trigger language
- OR Rocky could expose a dedicated email trigger: forward to `rocky@gallagherllp.com` with subject containing `[NEIL]`

**Launch sequence:**

```python
# In rocky.py, after classifier identifies a NEIL request:
if classification["project_category"] == "neil_request":
    complaint_path = save_attachments_to_temp(email)
    rrid = lookup_or_create_case(classification)

    subprocess.Popen([
        NEIL_EXE_PATH,
        "--complaint", complaint_path,
        "--rrid", rrid,
        "--config", NEIL_CONFIG_PATH,
    ])
    log_event("neil_launched", {"rrid": rrid, "email_id": email["id"]})
```

Rocky fires and forgets — NEIL runs as an independent process. Rocky logs the launch and moves on. NEIL manages its own lifecycle, state, and Teams communication.

---

## State management

NEIL is a long-running process (hours to days, depending on lawyer response time). State is persisted to disk at every stage transition so the process can survive crashes.

**`run_state.json`:**

```json
{
  "rrid": "RRID-0042",
  "started": "2026-05-24T14:30:00Z",
  "current_stage": "teams_qa_1",
  "complaint_path": "complaint.pdf",
  "extraction": { "completed": true, "output_file": "extraction.json" },
  "teams_qa_1": {
    "message_sent": "2026-05-24T14:32:00Z",
    "message_id": "abc123",
    "response_received": null
  },
  "research": { "completed": false },
  "teams_qa_2": { "completed": false },
  "motion": { "completed": false },
  "discovery": { "completed": false }
}
```

**Crash recovery:** On launch, NEIL checks for an existing `run_state.json` in the run directory. If found, it resumes from the last incomplete stage instead of starting over. Completed stages are never re-run.

**Concurrent runs:** `neil_active_runs.json` on the Rocky laptop tracks all in-progress NEIL runs. Rocky checks this before launching a new one to prevent duplicates for the same RRID.

---

## Permission requirements

| Component | Permission | Scope | Status |
|---|---|---|---|
| Complaint email reading | `Mail.Read` | Rocky's existing delegation on James's mailbox | Already granted |
| Teams chat Q&A | `Chat.ReadWrite` | Rocky's app registration (delegated) | **New — needs IT** |
| Teams message send | `ChatMessage.Send` | Rocky's app registration (delegated) | **New — needs IT** |
| CoCounsel API | API key | Westlaw CoCounsel subscription | **New — needs Westlaw account setup** |
| OneDrive file write | Filesystem (local sync) | OneDrive - gejlaw.com\NEIL\ folder | Existing (pin "always keep on this device") |

---

## Technology stack

| Component | Library/Service |
|---|---|
| Claude API | `anthropic` SDK (Python) |
| CoCounsel | Westlaw CoCounsel API (REST) |
| PDF extraction | `pdfplumber` (text + tables) or `PyMuPDF` (faster, layout-aware) |
| Teams integration | Microsoft Graph API via `requests` (same auth pattern as Rocky) |
| Document generation | `docxtpl` (Jinja2 + python-docx) |
| Auth | MSAL (shared token cache with Rocky) |
| Executable packaging | PyInstaller (single-file .exe, same as Rocky) |

---

## Build phases

| Phase | What it delivers | Depends on |
|---|---|---|
| **Phase 1: Extraction engine** | Stage 1 works end-to-end: PDF in → structured extraction out. CLI-only, no Teams, no CoCounsel. Run manually with `neil.exe --complaint path.pdf` | Instructions file authored |
| **Phase 2: Teams chat loop** | Pause-and-poll Teams Q&A works. Can send questions, receive answers, resume. Tested standalone before wiring into pipeline. | IT grants Chat.ReadWrite + ChatMessage.Send |
| **Phase 3: CoCounsel integration** | Stage 2 works: Claude calls CoCounsel tools, synthesizes research. Tested with real complaints. | Westlaw CoCounsel API access + key |
| **Phase 4: Motion drafting** | Stage 3 works: research + facts → motion to dismiss via Jinja template. Template authored and tested with sample motions. | Jinja template authored |
| **Phase 5: Discovery generation** | Stage 4 works: discovery requests via Jinja templates. | Templates authored |
| **Phase 6: Full pipeline** | All stages wired together with state management, crash recovery, concurrent-run tracking. | Phases 1–5 |
| **Phase 7: Rocky integration** | Rocky classifier detects NEIL requests and launches the process. | Phase 6 + Rocky classifier update |

**Phases 1, 2, and 3 can be built in parallel** — they're independent components. Phase 4 and 5 are also independent of each other. Phase 6 is integration. Phase 7 is the last mile.

**Estimated build time:** 4–6 weekends, assuming CoCounsel API access is resolved early. The Jinja templates and instruction files are the long-tail work — they need iteration against real complaints to get the output quality right.

---

## Instructions files — what goes in them

These are the most important files in the project. They're the equivalent of Rocky's `instructions.md` — plain-English guidance that shapes Claude's output quality. They need to be written by James (or iterated with James) because they encode legal judgment.

**`stage1_extraction.md`** should cover:
- How to identify claims vs. factual allegations vs. legal conclusions
- How to map allegations to specific elements of each cause of action
- When to flag a factual allegation as "disputed" vs. "undisputed"
- What questions to generate for the lawyer (focus on facts only the client would know, strategic choices, and deadline-critical items)
- How to handle multi-defendant complaints where our client is one of several

**`stage2_research.md`** should cover:
- How to frame CoCounsel research queries (specific enough to get useful results, not so narrow that relevant authority is missed)
- Priority order: 12(b)(6) standards in the specific circuit, then element-by-element analysis, then affirmative defenses raisable on a MTD
- How to evaluate case strength (binding vs. persuasive, how recent, how factually similar)
- What to look for in plaintiff's likely responses to each argument

**`stage3_motion.md`** should cover:
- Court-specific formatting conventions (EDVA, D.Md., D.D.C.)
- Motion structure and tone
- Citation format and density expectations
- How to handle factual disputes at the MTD stage (accept well-pleaded facts, challenge legal conclusions)
- Standard introductory and concluding language

**`stage4_discovery.md`** should cover:
- Interrogatory strategy (what to ask, what to save for depositions)
- RFP scope (proportionality, relevance framing)
- RFA targets (undisputed facts, document authenticity, legal conclusions that narrow issues)
- Jurisdiction-specific limits and conventions

---

## CoCounsel API — what needs to be validated

Before Phase 3 can start, these questions need answers:

1. **API access model.** Does Gallagher's Westlaw subscription include CoCounsel API access, or is it a separate add-on? Some Westlaw tiers bundle CoCounsel; others require a separate license.
2. **API endpoints.** CoCounsel's API surface — what operations are available? At minimum, NEIL needs a case law search and a statute search. If CoCounsel exposes a "draft argument" or "analyze issue" endpoint, that could replace or augment Claude's synthesis step.
3. **Rate limits and latency.** Legal research queries can be slow. NEIL needs to handle timeouts and retries gracefully.
4. **Authentication.** OAuth2, API key, or client-certificate? This determines how NEIL stores and refreshes credentials.
5. **Output format.** What does CoCounsel return — full case text, headnotes, key passages, citations only? This shapes how Claude processes the results.

If CoCounsel API access is blocked or delayed, a **fallback option** is to use Claude with web search MCP tools to find publicly available case law (Google Scholar, court websites). Lower quality than Westlaw but unblocks development.

---

## Open decisions

- **Which courts/jurisdictions first?** EDVA is the likely starting point given James's practice, but the templates and instructions need to be jurisdiction-aware from the start.
- **Multi-attorney support.** If NEIL lives on shared OneDrive, can other attorneys trigger it? If so, Teams Q&A needs to route to the right attorney, not just James.
- **Complaint format variants.** State court complaints differ from federal. NEIL's extraction stage needs to handle both, or should we scope to federal only for v1?
- **Review workflow.** Does James want NEIL to save the motion as a draft for review, or should there be a Teams-based approval step before finalizing?
- **Case folder integration.** Should NEIL output go into the Rocky Cases folder structure (e.g., `RRID-0042/Drafts/`), or does NEIL maintain its own `runs/` directory? Rocky Cases integration is cleaner but couples the two systems.
- **Cost tracking.** Each NEIL run involves multiple Claude API calls + CoCounsel API calls. Should NEIL log token usage and estimated cost per run?
- **RRID assignment.** If Rocky doesn't already have a case for this complaint, does NEIL create one? Or does it require an existing RRID?

---

## Security considerations

- **CoCounsel API key** stored in `config.json` on OneDrive — needs the same protection as Rocky's `config.json` (which lives locally on the Rocky laptop, NOT on OneDrive). Consider: NEIL's `config.json` with API keys should live locally at `C:\Rocky\neil_config.json`, NOT in the shared OneDrive folder. The OneDrive folder should only contain instructions, templates, and run output.
- **Complaint PDFs contain privileged information.** NEIL run directories on OneDrive contain client data. OneDrive sharing permissions should be restricted to attorneys who need access.
- **Teams messages contain case strategy.** The Q&A exchanges include privileged attorney work product. The 1:1 chat is inherently private, but logs stored in `runs/` are on shared OneDrive.
- **Claude API calls transmit complaint text.** Same consideration as Rocky's existing email processing — covered by Anthropic's data use policy (API inputs not used for training).
