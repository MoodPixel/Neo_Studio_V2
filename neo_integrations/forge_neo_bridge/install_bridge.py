from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the optional Neo Studio Forge Bridge extension.")
    parser.add_argument("--forge-root", required=True, help="Path to the Forge Neo installation root.")
    parser.add_argument("--name", default="neo-studio-forge-bridge", help="Destination extension folder name.")
    parser.add_argument("--replace", action="store_true", help="Replace an existing Bridge extension folder.")
    args = parser.parse_args()

    forge_root = Path(args.forge_root).expanduser().resolve()
    if not forge_root.is_dir():
        raise SystemExit("Forge root does not exist or is not a directory.")
    if not any((forge_root / marker).exists() for marker in ("launch.py", "webui.py", "webui-user.bat", "webui.sh")):
        raise SystemExit("The selected folder does not look like a Forge WebUI root.")

    source = Path(__file__).resolve().parent / "forge_extension"
    destination = forge_root / "extensions" / args.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not args.replace:
            raise SystemExit(f"Bridge destination already exists: {destination}")
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    print(f"Installed Neo Studio Forge Bridge to: {destination}")
    print("Restart Forge Neo, keep --api enabled, then refresh the Forge profile in Neo Admin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
