"""ONE-TIME SETUP for the Rocky Legal Research Agent — run once, then never again.

Creates the persistent Managed Agents resources (environment, agent, vault) and
writes their IDs to research_agent_config.json. The runtime script
(research_agent_run.py) loads the IDs from that file on every scheduled run.

Do NOT re-run this on a schedule — agents are persistent, versioned objects.
To change the agent's behavior later, update it in place:
    client.beta.agents.update(agent_id, system=..., ...)

Before running, fill in the four COCOUNSEL_* placeholders below.
See the setup notes (chat summary / SESSIONS.md) for how to obtain them.

Usage (PowerShell):
    $env:ANTHROPIC_API_KEY = "sk-ant-..."
    python research_agent_setup.py
"""

import json
import sys
from pathlib import Path

import anthropic

# ---------------------------------------------------------------------------
# CoCounsel MCP connection — FILL THESE IN before running.
#
# COCOUNSEL_MCP_URL: claude.ai -> Settings -> Connectors -> CoCounsel -> server URL
# Tokens: easiest path is `npx mcp-remote <url>` on this laptop — it runs the
# browser OAuth flow and caches access_token / refresh_token / client_id under
# %USERPROFILE%\.mcp-auth\. The token endpoint is published in the auth server's
# /.well-known/oauth-authorization-server metadata.
# ---------------------------------------------------------------------------
COCOUNSEL_MCP_URL = "REPLACE_WITH_COCOUNSEL_MCP_SERVER_URL"
COCOUNSEL_ACCESS_TOKEN = "REPLACE_WITH_ACCESS_TOKEN"
COCOUNSEL_REFRESH_TOKEN = "REPLACE_WITH_REFRESH_TOKEN"
COCOUNSEL_CLIENT_ID = "REPLACE_WITH_OAUTH_CLIENT_ID"
COCOUNSEL_TOKEN_ENDPOINT = "REPLACE_WITH_TOKEN_ENDPOINT_URL"
COCOUNSEL_TOKEN_EXPIRES_AT = "2026-06-10T23:59:59Z"  # current access token's expiry

CONFIG_PATH = Path(__file__).parent / "research_agent_config.json"

SYSTEM_PROMPT = """\
You are a legal research agent for Gallagher LLP, supporting attorney James Bragdon
(landlord-tenant law, property management, and federal civil litigation in Virginia,
the District of Columbia, and Maryland).

Each task assigns one research question for one case. Workflow for every task:

1. RESEARCH. Use the CoCounsel MCP tools for all substantive legal research. Run
   searches for each distinct legal issue in the question — never answer a legal
   question from memory alone. Where authority could have changed recently, research
   before drafting.

2. COLLECT AUTHORITY. Save the full text of every case you rely on under
   /mnt/session/outputs/cases/, named by citation (PDF preferred, otherwise .txt).
   If CoCounsel does not return the full text, fetch a public copy via web_fetch
   from CourtListener, Justia, Google Scholar, or the relevant court's website.

3. DRAFT THE MEMORANDUM. Produce a summary legal memorandum as a Word document
   (.docx) using the docx skill and save it to /mnt/session/outputs/. Structure:
   To / From / Date / Re header block, Question(s) Presented, Brief Answer,
   Discussion with pinpoint citations, and Conclusion. Use Bluebook citation
   format. Address contrary authority where it exists; do not omit unfavorable
   cases. Confine the analysis to the jurisdiction(s) specified in the task.

All deliverables MUST be written under /mnt/session/outputs/ — files written
anywhere else cannot be retrieved after the session ends.
"""

ALLOWED_HOSTS = [
    "www.courtlistener.com",
    "courtlistener.com",
    "scholar.google.com",
    "law.justia.com",
    "casetext.com",
    "www.vacourts.gov",
    "vacourts.gov",
    "www.dccourts.gov",
    "dccourts.gov",
    "mdcourts.gov",
    "www.mdcourts.gov",
    "casesearch.courts.state.md.us",
]


def main() -> None:
    if "REPLACE_WITH" in COCOUNSEL_MCP_URL:
        sys.exit("Fill in the COCOUNSEL_* placeholders at the top of this file first.")
    if CONFIG_PATH.exists():
        sys.exit(
            f"{CONFIG_PATH.name} already exists — the agent is already set up. "
            "To change the agent, use client.beta.agents.update(), not a re-create."
        )

    client = anthropic.Anthropic()

    environment = client.beta.environments.create(
        name="rocky-legal-research",
        config={
            "type": "cloud",
            "networking": {
                "type": "limited",
                "allow_mcp_servers": True,       # lets the container reach CoCounsel
                "allow_package_managers": True,  # docx/pdf skills may pip install
                "allowed_hosts": ALLOWED_HOSTS,
            },
        },
    )
    print(f"Environment: {environment.id}")

    agent = client.beta.agents.create(
        name="Rocky Legal Research Agent",
        model="claude-opus-4-8",
        system=SYSTEM_PROMPT,
        tools=[
            {"type": "agent_toolset_20260401", "default_config": {"enabled": True}},
            {"type": "mcp_toolset", "mcp_server_name": "cocounsel"},
        ],
        mcp_servers=[
            {"type": "url", "name": "cocounsel", "url": COCOUNSEL_MCP_URL},
        ],
        skills=[
            {"type": "anthropic", "skill_id": "docx"},
            {"type": "anthropic", "skill_id": "pdf"},
            {"type": "anthropic", "skill_id": "xlsx"},
        ],
    )
    print(f"Agent: {agent.id} (version {agent.version})")

    vault = client.beta.vaults.create(name="CoCounsel credentials")
    credential = client.beta.vaults.credentials.create(
        vault.id,
        display_name="CoCounsel (Gallagher LLP)",
        auth={
            "type": "mcp_oauth",
            "mcp_server_url": COCOUNSEL_MCP_URL,
            "access_token": COCOUNSEL_ACCESS_TOKEN,
            "expires_at": COCOUNSEL_TOKEN_EXPIRES_AT,
            "refresh": {
                "refresh_token": COCOUNSEL_REFRESH_TOKEN,
                "client_id": COCOUNSEL_CLIENT_ID,
                "token_endpoint": COCOUNSEL_TOKEN_ENDPOINT,
                "token_endpoint_auth": {"type": "none"},
            },
        },
    )
    print(f"Vault: {vault.id} (credential {credential.id})")

    # Validate the OAuth credential now rather than discovering a bad token mid-run.
    try:
        client.beta.vaults.credentials.mcp_oauth_validate(
            credential.id, vault_id=vault.id
        )
        print("CoCounsel OAuth credential validated.")
    except Exception as exc:  # surface but don't block — runtime smoke test re-checks
        print(f"WARNING: credential validation failed: {exc}")
        print("The runtime smoke test will re-check before any real work is done.")

    CONFIG_PATH.write_text(
        json.dumps(
            {
                "agent_id": agent.id,
                "agent_version": agent.version,
                "environment_id": environment.id,
                "vault_id": vault.id,
            },
            indent=2,
        )
    )
    print(f"\nWrote {CONFIG_PATH.name}. Setup complete — do not run this script again.")


if __name__ == "__main__":
    main()
