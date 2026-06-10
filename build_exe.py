"""
Build rocky.exe and dashboard.exe using PyInstaller.

Run from the Rocky source directory:
    python build_exe.py              Build both rocky.exe and dashboard.exe
    python build_exe.py --rocky      Build only rocky.exe
    python build_exe.py --dashboard  Build only dashboard.exe

Output: dist/rocky.exe, dist/dashboard.exe (single file, all dependencies bundled).
Copies the .exe files to OneDrive Program Files after building.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DIST_DIR = ROOT / "dist"
TARGET = Path(
    r"C:\Users\jbragdon\OneDrive\OneDrive - gejlaw.com\Program Files\Rocky"
)


def build_rocky():
    """Build the main rocky.exe."""
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
        "--add-data", f"{ROOT / 'kill_switch.py'};.",
        "--add-data", f"{ROOT / 'pending_llt.py'};.",
        "--add-data", f"{ROOT / 'pma_tracker.py'};.",
        "--add-data", f"{ROOT / 'Icon'};Icon",
        # Jinja2 is used by pending_llt; ensure PyInstaller bundles it.
        "--hidden-import", "jinja2",
        # Clean build each time.
        "--clean",
        str(ROOT / "rocky.py"),
    ]

    print("Building rocky.exe...")
    print(f"Command: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)

    if result.returncode != 0:
        print(f"\nrocky.exe build failed with code {result.returncode}.")
        return False

    exe_path = DIST_DIR / "rocky.exe"
    if not exe_path.exists():
        print(f"\nBuild completed but {exe_path} not found.")
        return False

    size_mb = exe_path.stat().st_size / (1024 * 1024)
    print(f"\nrocky.exe built: {exe_path} ({size_mb:.1f} MB)")

    # Copy to OneDrive.
    TARGET.mkdir(parents=True, exist_ok=True)
    target_path = TARGET / "rocky.exe"
    shutil.copy2(exe_path, target_path)
    print(f"Copied to: {target_path}")
    return True


def build_dashboard():
    """Build the dashboard.exe web UI."""
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", "dashboard",
        "--distpath", str(DIST_DIR),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT),
        # Flask and its dependencies.
        "--hidden-import", "flask",
        "--hidden-import", "jinja2",
        # Bundle the HTML template.
        "--add-data", f"{ROOT / 'templates'};templates",
        # Clean build each time.
        "--clean",
        str(ROOT / "dashboard.py"),
    ]

    print("Building dashboard.exe...")
    print(f"Command: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)

    if result.returncode != 0:
        print(f"\ndashboard.exe build failed with code {result.returncode}.")
        return False

    exe_path = DIST_DIR / "dashboard.exe"
    if not exe_path.exists():
        print(f"\nBuild completed but {exe_path} not found.")
        return False

    size_mb = exe_path.stat().st_size / (1024 * 1024)
    print(f"\ndashboard.exe built: {exe_path} ({size_mb:.1f} MB)")

    # Copy to OneDrive.
    TARGET.mkdir(parents=True, exist_ok=True)
    target_path = TARGET / "dashboard.exe"
    shutil.copy2(exe_path, target_path)
    print(f"Copied to: {target_path}")
    return True


def main():
    build_r = "--rocky" in sys.argv or len(sys.argv) == 1
    build_d = "--dashboard" in sys.argv or len(sys.argv) == 1

    ok = True
    if build_r:
        ok = build_rocky() and ok
        if build_d:
            print("\n" + "=" * 60 + "\n")
    if build_d:
        ok = build_dashboard() and ok

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
