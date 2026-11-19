"""Commandes d'administration.

    python -m trdr.cli init-auth   # génère les variables d'authentification du .env
"""

import argparse
import getpass
import secrets
import sys

import pyotp

from .api.auth import hash_password

MIN_PASSWORD_LENGTH = 12


def init_auth() -> None:
    password = getpass.getpass("Mot de passe de l'interface : ")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(f"Mot de passe trop court ({MIN_PASSWORD_LENGTH} caractères minimum).")
    if getpass.getpass("Confirmation : ") != password:
        sys.exit("Les deux saisies ne correspondent pas.")

    totp_secret = pyotp.random_base32()
    print("\n# À coller dans le fichier .env")
    print(f"AUTH_PASSWORD_HASH={hash_password(password)}")
    print(f"AUTH_TOTP_SECRET={totp_secret}")
    print(f"JWT_SECRET={secrets.token_urlsafe(48)}")
    print("\n# Google Authenticator : « Saisir une clé de configuration », clé basée sur l'heure")
    print(f"#   Clé : {totp_secret}")
    print(f"#   ou lien : {pyotp.TOTP(totp_secret).provisioning_uri(name='trdr', issuer_name='trdr')}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="trdr")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-auth", help="génère mot de passe haché, secret TOTP et secret JWT")
    parser.parse_args()
    init_auth()


if __name__ == "__main__":
    main()
