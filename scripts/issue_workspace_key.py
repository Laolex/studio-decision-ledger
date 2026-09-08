"""Issue or rotate one administrator-managed studio access key.

Writes the raw key once to an exclusive 0600 secret file, and only its SHA-256
digest to the configured registry. Never prints the key. Restart all API workers
after modifying the registry; removed digests revoke their browser sessions too.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--role", choices=["reader", "operator"], required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,100}", args.subject):
        parser.error("Invalid subject")
    if not args.registry.is_absolute() or not args.secret_file.is_absolute():
        parser.error("Use absolute paths in protected directories")
    args.registry.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.secret_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with args.registry.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entries = json.loads(args.registry.read_text()) if args.registry.exists() else []
        if not isinstance(entries, list):
            parser.error("Registry must be a JSON array")
        if any(not isinstance(entry, dict) or set(entry) != {"sha256", "subject", "role"}
               or not isinstance(entry["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
               or not isinstance(entry["subject"], str) or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,100}", entry["subject"])
               or entry["role"] not in ("reader", "operator") for entry in entries):
            parser.error("Existing registry is invalid; refusing to modify it")
        if any(entry["subject"] == args.subject for entry in entries) and not args.replace:
            parser.error("Subject exists; use --replace to rotate and revoke its existing keys")
        entries = [entry for entry in entries if entry["subject"] != args.subject]
        if len(entries) >= 100:
            parser.error("Registry limit is 100 keys")
        token = secrets.token_urlsafe(32)
        entries.append({"sha256": hashlib.sha256(token.encode()).hexdigest(), "subject": args.subject, "role": args.role})
        descriptor = os.open(args.secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as secret:
            secret.write(token + "\n")
            secret.flush()
            os.fsync(secret.fileno())
        with tempfile.NamedTemporaryFile(mode="w", dir=args.registry.parent, delete=False) as registry:
            registry.write(json.dumps(entries, indent=2) + "\n")
            registry.flush()
            os.fsync(registry.fileno())
        os.replace(registry.name, args.registry)
        args.registry.chmod(0o600)
    print(f"Issued {args.role} key for {args.subject}. Deliver {args.secret_file} securely, then remove that delivery copy. Restart all API workers to load {args.registry}.")


if __name__ == "__main__":
    main()
