"""
Build rocky.exe using PyInstaller.

Run from the Rocky source directory:
    python build_exe.py

Output: dist/rocky.exe (single file, all dependencies bundled).
Copy the .exe to OneDrive Program Files after building.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DIST_DIR = ROOT / "dist"
TARGET = Path(
    r"C:\Users\jbragdon\OneDrive\OneDrive - gejlaw.com\Program Files"
)

def main():
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", "rocky",
        "--distpath", str(DIST_DIR),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT),
        # Hidden imports: optional deps that rocky.py imports inside functions.
        "--hidden-import", "openpyxl",
        "--hidden-import", "pypdf",
        "--hidden-import", "docx",
        # Bundle the local modules alongside rocky.py.
        "--add-data", f"{ROOT / 'permissions.py'};.",
        "--add-data", f"{ROOT / 'outbound.py'};.",
        "--add-data", f"{ROOT / 'remy_runner.py'};.",
        # Clean build each time.
        "--clean",
        str(ROOT / "rocky.py"),
    ]

    print(f"Building rocky.exe...")
    print(f"Command: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)

    if result.returncode != 0:
        print(f"\nBuild failed with code {result.returncode}.")
        sys.exit(1)

    exe_path = DIST_DIR / "rocky.exe"
    if not exe_path.exists():
        print(f"\nBuild completed but {exe_path} not found.")
        sys.exit(1)

    size_mb = exe_path.stat().st_size / (1024 * 1024)
    print(f"\nBuild succeeded: {exe_path} ({size_mb:.1f} MB)")

    # Copy to OneDrive.
    TARGET.mkdir(parents=True, exist_ok=True)
    target_path = TARGET / "rocky.exe"
    import shutil
    shutil.copy2(exe_path, target_path)
    print(f"Copied to: {target_path}")


if __name__ == "__main__":
    main()
