"""
One-time authentication for Ella's daily case digest.

Ella runs this on her own workstation:
    python ella_auth.py

It authenticates via device code flow and saves the token cache to
OneDrive so the Rocky laptop can pick it up automatically.

Requirements:
    pip install msal
"""

import json
import sys
from pathlib import Path

try:
    import msal
except ImportError:
    print("Missing dependency. Run: pip install msal")
    sys.exit(1)

# Rocky's Azure AD app registration.
CLIENT_ID = "f012b5f9-051a-46f2-a2c8-15823ed63900"
TENANT_ID = "9301144f-f5f1-48c5-9f23-907000d5f3d2"

# Where to save the token cache — OneDrive syncs this to the Rocky laptop.
OUTPUT_PATH = Path(
    r"C:\Users\eaiken\OneDrive - gejlaw.com"
    r"\James D. Bragdon's files - Program Files"
    r"\Rocky\Ella Daily Case Digest\ella_token_cache.json"
)


def main():
    print()
    print("=" * 60)
    print("Rocky — Ella Authentication Setup")
    print("=" * 60)
    print()
    print("This will authenticate your account so Rocky can read")
    print("your mailbox for the daily case digest.")
    print()

    cache = msal.SerializableTokenCache()

    app = msal.PublicClientApplication(
        client_id=CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        token_cache=cache,
    )

    flow = app.initiate_device_flow(scopes=["Mail.Read"])
    if "user_code" not in flow:
        print(f"Failed to start authentication: {flow}")
        sys.exit(1)

    print(flow["message"])
    print()

    result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        print(f"Authentication failed: {result.get('error_description', result)}")
        sys.exit(1)

    username = result.get("id_token_claims", {}).get("preferred_username", "unknown")

    # Save token cache.
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(cache.serialize(), encoding="utf-8")

    print(f"Authentication successful for: {username}")
    print(f"Token saved to: {OUTPUT_PATH}")
    print()
    print("OneDrive will sync this to the Rocky laptop automatically.")
    print("You can close this window.")


if __name__ == "__main__":
    main()
