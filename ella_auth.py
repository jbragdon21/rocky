"""
One-time authentication for Ella's daily case digest.

Ella double-clicks this .exe on her workstation. It authenticates via
device code flow and saves ella_token_cache.json to her Desktop.
She then sends that file to James.
"""

import sys
import os
from pathlib import Path

import msal

CLIENT_ID = "f012b5f9-051a-46f2-a2c8-15823ed63900"
TENANT_ID = "9301144f-f5f1-48c5-9f23-907000d5f3d2"

OUTPUT_PATH = Path(os.path.expanduser("~")) / "Desktop" / "ella_token_cache.json"


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
        input("\nPress Enter to close...")
        sys.exit(1)

    print(flow["message"])
    print()

    result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        print(f"Authentication failed: {result.get('error_description', result)}")
        input("\nPress Enter to close...")
        sys.exit(1)

    username = result.get("id_token_claims", {}).get("preferred_username", "unknown")

    OUTPUT_PATH.write_text(cache.serialize(), encoding="utf-8")

    print(f"Authentication successful for: {username}")
    print(f"Token saved to: {OUTPUT_PATH}")
    print()
    print("Please send the file 'ella_token_cache.json' from your")
    print("Desktop to James Bragdon.")
    input("\nPress Enter to close...")


if __name__ == "__main__":
    main()
