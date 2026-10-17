"""Ed25519 signatures over saved records, using OpenSSH ``ssh-keygen -Y``.

A digest shows that a record matches a retained hash. A signature also shows
who vouched for the exact bytes: anyone holding the signer's public key in an
allowed_signers file can check that the file is unchanged since signing. It
does not show that the signed run was scientifically correct.
"""

from pathlib import Path
import shutil
import subprocess


NAMESPACE = "sewall-manifest"
_TIMEOUT = 60


class SigningError(ValueError):
    pass


def _ssh_keygen():
    program = shutil.which("ssh-keygen")
    if program is None:
        raise SigningError("ssh-keygen (OpenSSH 8.1 or later) is required for signatures")
    return program


def _run(arguments, **options):
    try:
        return subprocess.run([_ssh_keygen(), *arguments], capture_output=True, text=True, timeout=_TIMEOUT, **options)
    except subprocess.TimeoutExpired:
        raise SigningError("ssh-keygen did not finish; passphrase-protected keys must be loaded in ssh-agent") from None


def _public_key(key):
    """Return the public key line for a private key file, requiring Ed25519."""
    result = _run(["-y", "-f", str(key)], stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        raise SigningError("Cannot read the signing key; passphrase-protected keys must be loaded in ssh-agent")
    line = result.stdout.strip()
    if not line.startswith("ssh-ed25519 "):
        raise SigningError("Signing key must be Ed25519")
    return line


def sign_file(path, key) -> dict:
    """Sign a file with an Ed25519 private key; the signature is written next to it as <file>.sig."""
    path, signature = Path(path), Path(str(path) + ".sig")
    if not path.is_file():
        raise SigningError("Nothing to sign: file not found")
    if signature.exists():
        raise SigningError("Signature file already exists; remove it to re-sign")
    public = _public_key(key)
    result = _run(["-Y", "sign", "-q", "-f", str(key), "-n", NAMESPACE, str(path)], stdin=subprocess.DEVNULL)
    if result.returncode != 0 or not signature.is_file():
        raise SigningError("ssh-keygen could not sign the file: " + result.stderr.strip()[:300])
    return {"file": str(path), "signature": str(signature), "namespace": NAMESPACE,
            "public_key": " ".join(public.split()[:2])}


def _signers(allowed_signers, identity):
    keys = []
    for line in Path(allowed_signers).read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) >= 3 and not fields[0].startswith("#") and identity in fields[0].split(","):
            keys.append(next((item for item in fields[1:] if item.startswith(("ssh-", "ecdsa-", "sk-"))), None))
    return keys


def verify_file(path, allowed_signers, identity, signature=None) -> dict:
    """Check a file's signature against an allowed_signers entry; only Ed25519 entries are accepted."""
    path = Path(path)
    signature = Path(signature) if signature else Path(str(path) + ".sig")
    if not path.is_file() or not signature.is_file():
        return {"valid": False, "reason": "File or signature not found"}
    keys = _signers(allowed_signers, identity)
    if not keys or any(key != "ssh-ed25519" for key in keys):
        return {"valid": False, "reason": "The identity needs Ed25519 entries in allowed_signers"}
    with path.open("rb") as handle:
        result = _run(["-Y", "verify", "-f", str(allowed_signers), "-I", identity, "-n", NAMESPACE,
                       "-s", str(signature)], stdin=handle)
    if result.returncode != 0:
        return {"valid": False, "reason": "Signature does not verify for this identity and file"}
    return {"valid": True, "reason": "Ed25519 signature verified", "identity": identity, "namespace": NAMESPACE}
