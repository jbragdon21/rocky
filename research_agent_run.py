"""RUNTIME for the Rocky Legal Research Agent — runs on every scheduled invocation.

Reads pending tasks from research_tasks.json, runs one Managed Agents session per
task, and saves the resulting memorandum + downloaded cases into the task's case
folder under "Legal Research". Completed tasks are marked done in the tasks file.

Per task, the flow is:
  1. Upload the task's input files as session resources
  2. Create the session (blocks until resources are mounted)
  3. Smoke-test CoCounsel connectivity with one cheap probe turn
  4. Send the research question as a graded Outcome (rubric below) — the harness
     iterates the agent until the memo satisfies the rubric or max iterations
  5. Download everything the agent wrote to /mnt/session/outputs/ into the case folder
  6. Archive the session

Schedule with Windows Task Scheduler, e.g. daily at 6am:
  schtasks /create /tn "Rocky Legal Research Agent" ^
    /tr "py C:\\Users\\jbragdon\\Desktop\\Rocky\\research_agent_run.py" /sc daily /st 06:00

Requires: pip install anthropic ; ANTHROPIC_API_KEY in the environment;
research_agent_config.json created by research_agent_setup.py.
"""

import json
import sys
import time
from datetime import date
from pathlib import Path, PurePosixPath

import anthropic

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "research_agent_config.json"
TASKS_PATH = BASE_DIR / "research_tasks.json"

client = anthropic.Anthropic()

RUBRIC_TEMPLATE = """\
# Memorandum acceptance criteria

1. A Word document (.docx) memorandum exists in /mnt/session/outputs/.
2. The memorandum contains, in order: a To/From/Date/Re header block; a
   Question(s) Presented section; a Brief Answer section; a Discussion section;
   and a Conclusion section.
3. Every legal proposition in the Discussion is supported by a citation to
   specific authority, with pinpoint cites, in Bluebook format.
4. The analysis is confined to {jurisdiction} law (plus controlling federal
   authority where applicable) and does not rely on authority from other
   jurisdictions except as expressly persuasive and labeled as such.
5. The full text of every case relied upon is saved under
   /mnt/session/outputs/cases/, named by citation.
6. Contrary or unfavorable authority found during research is addressed in the
   Discussion, not omitted.
7. The Brief Answer directly answers the Question Presented and is consistent
   with the Discussion.
"""

SMOKE_TEST_PROMPT = (
    "Before starting any work: confirm you can reach the CoCounsel MCP server by "
    "listing one or two of its available tools or capabilities. Begin your reply "
    "with exactly 'COCOUNSEL OK' if it is reachable, or 'COCOUNSEL FAILED' plus "
    "the error if not. Do not begin any research yet."
)


def send_and_drain(session_id: str, events: list) -> tuple[str, str]:
    """Open the stream first, send events, then drain until the session is done.

    Returns (stop_reason_type, concatenated agent text). Does not break on a bare
    idle — 'requires_action' idles are transient waits, not completion.
    """
    texts: list[str] = []
    with client.beta.sessions.events.stream(session_id=session_id) as stream:
        client.beta.sessions.events.send(session_id=session_id, events=events)
        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if block.type == "text":
                        texts.append(block.text)
                        print(block.text, end="", flush=True)
            elif event.type == "session.error":
                print(f"\n[session error] {getattr(event, 'error', event)}")
            elif event.type == "session.status_terminated":
                return "terminated", "".join(texts)
            elif event.type == "session.status_idle":
                if event.stop_reason.type == "requires_action":
                    continue  # waiting on a tool confirmation — not terminal
                return event.stop_reason.type, "".join(texts)
    return "stream_closed", "".join(texts)


def download_outputs(session_id: str, dest_dir: Path) -> int:
    """Download files the agent wrote to /mnt/session/outputs/ into dest_dir."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    time.sleep(3)  # brief indexing lag between idle and files appearing
    count = 0
    for f in client.beta.files.list(
        scope_id=session_id, betas=["managed-agents-2026-04-01"]
    ):
        # Sanitize: keep safe relative structure (e.g. cases/...), drop anything odd
        parts = [p for p in PurePosixPath(f.filename).parts if p not in ("", ".", "..", "/")]
        if not parts:
            continue
        out_path = dest_dir.joinpath(*parts)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        client.beta.files.download(f.id).write_to_file(str(out_path))
        print(f"  saved {out_path}")
        count += 1
    return count


def archive_session(session_id: str) -> None:
    """Archive once the queryable status catches up with the idle event."""
    for _ in range(10):
        if client.beta.sessions.retrieve(session_id=session_id).status != "running":
            client.beta.sessions.archive(session_id=session_id)
            return
        time.sleep(0.5)
    print(f"  session {session_id} still running — left unarchived")


def run_task(config: dict, task: dict) -> bool:
    title = task.get("title", "Legal research task")
    case_folder = Path(task["case_folder"])
    print(f"\n=== {title} ===")

    # 1. Upload input files (OneDrive files must be pinned locally, not cloud-only)
    resources = []
    for fp in task.get("input_files", []):
        p = Path(fp)
        if not p.exists():
            print(f"  MISSING input file, skipping task: {p}")
            return False
        uploaded = client.beta.files.upload(file=p)
        resources.append({
            "type": "file",
            "file_id": uploaded.id,
            "mount_path": f"/workspace/inputs/{p.name}",
        })

    # 2. Create the session — references the pre-created agent by ID
    session = client.beta.sessions.create(
        agent={
            "type": "agent",
            "id": config["agent_id"],
            "version": config["agent_version"],
        },
        environment_id=config["environment_id"],
        vault_ids=[config["vault_id"]],
        resources=resources,
        title=title,
    )
    print(f"  session {session.id}")
    print(f"  watch: https://platform.claude.com/workspaces/default/sessions/{session.id}")

    # 3. Smoke-test CoCounsel before spending real budget
    status, text = send_and_drain(session.id, [{
        "type": "user.message",
        "content": [{"type": "text", "text": SMOKE_TEST_PROMPT}],
    }])
    if status != "end_turn" or "COCOUNSEL OK" not in text:
        print("\n  CoCounsel smoke test FAILED — aborting before the real task.")
        print("  Check the vault credential (token expired?) and re-run.")
        archive_session(session.id)
        return False
    print("\n  CoCounsel reachable — starting research.")

    # 4. Real kickoff: graded outcome (no separate user.message for the task)
    jurisdiction = task.get("jurisdiction", "Virginia, DC, or Maryland as applicable")
    description = (
        f"Research task for the matter '{title}'.\n\n"
        f"Jurisdiction: {jurisdiction}\n\n"
        f"Question: {task['question']}\n\n"
        "Input documents, if any, are mounted under /workspace/inputs/."
    )
    status, _ = send_and_drain(session.id, [{
        "type": "user.define_outcome",
        "description": description,
        "rubric": {"type": "text", "content": RUBRIC_TEMPLATE.format(jurisdiction=jurisdiction)},
        "max_iterations": task.get("max_iterations", 5),
    }])
    print(f"\n  finished with stop reason: {status}")

    # Report the grader's verdict
    final = client.beta.sessions.retrieve(session_id=session.id)
    for ev in getattr(final, "outcome_evaluations", None) or []:
        print(f"  outcome {ev.outcome_id}: {ev.result}")

    # 5. Save deliverables into the case folder
    dest = case_folder / "Legal Research" / f"Research Agent {date.today().isoformat()}"
    n = download_outputs(session.id, dest)
    print(f"  {n} file(s) saved to {dest}")

    # 6. Cleanup
    archive_session(session.id)
    return status != "terminated" and n > 0


def main() -> None:
    if not CONFIG_PATH.exists():
        sys.exit("research_agent_config.json not found — run research_agent_setup.py first.")
    if not TASKS_PATH.exists():
        sys.exit("research_tasks.json not found — nothing to do.")

    config = json.loads(CONFIG_PATH.read_text())
    tasks = json.loads(TASKS_PATH.read_text(encoding="utf-8"))

    pending = [t for t in tasks if t.get("status", "pending") == "pending"]
    if not pending:
        print("No pending research tasks.")
        return

    for task in pending:
        try:
            ok = run_task(config, task)
            task["status"] = "done" if ok else "failed"
        except Exception as exc:
            print(f"  task errored: {exc}")
            task["status"] = "failed"
        TASKS_PATH.write_text(json.dumps(tasks, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
