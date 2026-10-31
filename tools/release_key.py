"""
The release key of Verbaliox (see app/release_sign.py and docs/release-signing.md).

  python tools/release_key.py generate [--out FILE]
      A new key. Prints the public key for app/updater.py (RELEASE_PUBLIC_KEY) and writes the
      secret to FILE (verbascribe-release.key by default): keep it offline, and put its text into
      the RELEASE_SIGNING_KEY secret of the repository's Actions. Never commit it.

  python tools/release_key.py sign SHA256SUMS.txt --tag vX.Y.Z
      Signs with the secret from the RELEASE_SIGNING_KEY environment variable (the release workflow)
      and writes SHA256SUMS.txt.minisig.

  python tools/release_key.py verify SHA256SUMS.txt --tag vX.Y.Z
      Checks SHA256SUMS.txt.minisig with the public key of app/updater.py, as the updater does.
"""

import argparse
import base64
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import release_sign  # noqa: E402


def _secret() -> tuple:
    text = os.environ.get("RELEASE_SIGNING_KEY", "").strip()
    # The prefix keeps the program's former name: the secret in GitHub Actions was made with it
    m = re.fullmatch(r"verbascribe-release-key:([A-Za-z0-9+/=]+):([A-Za-z0-9+/=]+)", text)
    if not m:
        sys.exit("RELEASE_SIGNING_KEY is empty or not a key made by tools/release_key.py generate")
    seed, key_id = base64.b64decode(m.group(1)), base64.b64decode(m.group(2))
    if len(seed) != 32 or len(key_id) != 8:
        sys.exit("RELEASE_SIGNING_KEY is damaged")
    return seed, key_id


def _public_key_of_app() -> str:
    m = re.search(r'^RELEASE_PUBLIC_KEY = "([^"]*)"', (ROOT / "app" / "updater.py").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["generate", "sign", "verify"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--tag", default="")
    ap.add_argument("--out", default="verbascribe-release.key")
    a = ap.parse_args()

    if a.action == "generate":
        out = Path(a.out)
        if out.exists():
            sys.exit(f"{out} exists: a key is never overwritten")
        if ROOT in out.resolve().parents:
            print("Warning: the secret is written inside the program folder. Move it out and never commit it.")
        seed, key_id = release_sign.new_key()
        out.parent.mkdir(parents=True, exist_ok=True)
        secret = f"verbascribe-release-key:{base64.b64encode(seed).decode()}:{base64.b64encode(key_id).decode()}"
        out.write_text(secret + "\n", encoding="utf-8")
        print(f"Secret key written to {out.resolve()}")
        print("Put its whole text into the repository secret RELEASE_SIGNING_KEY (Settings → Secrets and variables → Actions).")
        print("Public key, for app/updater.py RELEASE_PUBLIC_KEY (and for minisign -P):")
        print(release_sign.public_key_text(seed, key_id))
        return

    if not a.file or not a.tag:
        sys.exit("a file and --tag are needed")
    data = Path(a.file).read_bytes()
    sig_path = Path(a.file + ".minisig")
    if a.action == "sign":
        seed, key_id = _secret()
        when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sig_path.write_text(release_sign.sign_file(seed, key_id, data, f"tag:{a.tag} file:{Path(a.file).name} time:{when}"),
                            encoding="utf-8")
        print(f"Signed: {sig_path}")
        if _public_key_of_app() and _public_key_of_app() != release_sign.public_key_text(seed, key_id):
            sys.exit("The secret does not belong to the public key in app/updater.py: the updater would refuse this release")
        return

    key = _public_key_of_app()
    if not key:
        sys.exit("app/updater.py has no RELEASE_PUBLIC_KEY")
    comment = release_sign.verify_file(key, data, sig_path.read_text(encoding="utf-8"))
    if f"tag:{a.tag}" not in comment.split():
        sys.exit(f"The signature is for another release: {comment}")
    print(f"Good signature: {comment}")


if __name__ == "__main__":
    main()
