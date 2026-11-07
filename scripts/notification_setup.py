"""Generate stable VAPID keys privately once; never print private configuration."""

import argparse
import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contact", required=True, help="mailto: owner contact for push service operators")
    args = parser.parse_args()
    if (
        not args.contact.startswith("mailto:")
        or "@" not in args.contact
        or any(c.isspace() for c in args.contact)
    ):
        parser.error("Use a valid mailto: contact")
    path = Path(".env")
    text = path.read_text()
    if any(
        line.startswith("VAPID_PRIVATE_KEY=") and line.partition("=")[2].strip() for line in text.splitlines()
    ):
        print("Existing private notification key preserved. No changes.")
        return
    key = ec.generate_private_key(ec.SECP256R1())

    def encode(value):
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    private = encode(
        key.private_bytes(
            serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    public = encode(
        key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    names = {"VAPID_PRIVATE_KEY", "VAPID_PUBLIC_KEY", "VAPID_SUBJECT"}
    lines = [line for line in text.splitlines() if line.partition("=")[0] not in names]
    path.write_text(
        "\n".join(lines)
        + f"\nVAPID_PRIVATE_KEY={private}\nVAPID_PUBLIC_KEY={public}\nVAPID_SUBJECT={args.contact}\n"
    )
    path.chmod(0o600)
    print("Stable notification keys saved privately. Recreate api and notification-worker to load them.")


if __name__ == "__main__":
    main()
