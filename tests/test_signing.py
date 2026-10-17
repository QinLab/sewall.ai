"""Ed25519 signatures with ssh-keygen; skipped when OpenSSH is not installed."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from sewall.agent import verify_agent_manifest
from sewall.signing import SigningError, sign_file, verify_file
from test_agent import reseal


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen is not installed")
class SigningTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)
        self.key = self.keypair("ed25519")
        self.signers = self.directory / "allowed_signers"
        public = (self.directory / "ed25519.pub").read_text().split()
        self.signers.write_text(f"lab@example.org {public[0]} {public[1]}\n")

    def keypair(self, kind):
        key = self.directory / kind
        subprocess.run(["ssh-keygen", "-q", "-t", kind, "-N", "", "-C", "test", "-f", str(key)], check=True)
        return key

    def write(self, name, value):
        path = self.directory / name
        path.write_text(json.dumps(value, indent=2) + "\n")
        return path

    def test_signed_manifest_verifies_and_any_edit_fails(self):
        manifest = json.loads((ROOT / "gallery" / "runs" / "york-2022.json").read_text())
        path = self.write("manifest.json", manifest)
        signed = sign_file(path, self.key)
        self.assertTrue(signed["public_key"].startswith("ssh-ed25519 "))
        self.assertTrue(verify_file(path, self.signers, "lab@example.org")["valid"])
        self.assertFalse(verify_file(path, self.signers, "other@example.org")["valid"])

        # A consistent edit resealed end to end passes replay; only the signature catches it.
        edited = deepcopy(manifest)
        edited["question"] = edited["inputs"]["question"] = edited["question"] + " (edited)"
        for event in edited["events"]:
            if event["type"] == "plan_created":
                event["details"]["inputs"]["question"] = edited["question"]
            if event["type"] == "node_added" and event["details"]["id"] == "question":
                event["details"]["label"] = edited["question"]
        for node in edited["graph"]["nodes"]:
            if node["id"] == "question":
                node["label"] = edited["question"]
        reseal(edited)
        self.assertTrue(verify_agent_manifest(edited)["valid"])
        path.write_text(json.dumps(edited, indent=2) + "\n")
        result = verify_file(path, self.signers, "lab@example.org")
        self.assertFalse(result["valid"])
        self.assertIn("does not verify", result["reason"])

    def test_only_ed25519_keys_sign_or_verify(self):
        path = self.write("record.json", {"value": 1})
        with self.assertRaisesRegex(SigningError, "Ed25519"):
            sign_file(path, self.keypair("ecdsa"))
        public = (self.directory / "ecdsa.pub").read_text().split()
        signers = self.directory / "ecdsa_signers"
        signers.write_text(f"lab@example.org {public[0]} {public[1]}\n")
        sign_file(path, self.key)
        self.assertIn("Ed25519 entries", verify_file(path, signers, "lab@example.org")["reason"])

    def test_existing_signature_is_not_overwritten(self):
        path = self.write("record.json", {"value": 1})
        sign_file(path, self.key)
        with self.assertRaisesRegex(SigningError, "already exists"):
            sign_file(path, self.key)


if __name__ == "__main__":
    unittest.main()
