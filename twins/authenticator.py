"""A software passkey authenticator: the twin of Face ID, Touch ID, Windows Hello or a security key.

It answers WebAuthn requests the way a browser plus platform authenticator would. It takes the
options JSON the server sent and returns ``PublicKeyCredential.toJSON()``: packed "none"
attestation with an ES256 (P-256) key, discoverable credentials holding the user handle, a sign
counter, and user presence and verification flags. The server's verification (py_webauthn)
therefore runs on real signatures.

    authenticator = SoftAuthenticator()
    credential = authenticator.create(options, origin="https://vault.test")
    assertion = authenticator.get(options, origin="https://vault.test")

Knobs: ``user_verified``, ``counter_step``, ``backup_eligible``, and ``tamper`` (flip a
signature bit to test rejection).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
from dataclasses import dataclass, field

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

AAGUID = bytes(16)  # all zeros: "none" attestation


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass
class StoredCredential:
    credential_id: bytes
    rp_id: str
    user_handle: bytes
    user_name: str
    key: ec.EllipticCurvePrivateKey
    sign_count: int = 0


@dataclass
class SoftAuthenticator:
    user_verified: bool = True
    counter_step: int = 1
    backup_eligible: bool = True  # synced passkeys (iCloud Keychain, Google Password Manager) set BE/BS
    tamper: bool = False
    credentials: list[StoredCredential] = field(default_factory=list)

    def _flags(self, attested: bool) -> int:
        flags = 0x01  # UP
        if self.user_verified:
            flags |= 0x04  # UV
        if self.backup_eligible:
            flags |= 0x08 | 0x10  # BE, BS
        if attested:
            flags |= 0x40  # AT
        return flags

    def create(self, options: dict, origin: str) -> dict:
        """navigator.credentials.create({publicKey: options}) -> credential.toJSON()"""
        rp_id = options["rp"]["id"]
        excluded = {c["id"] for c in options.get("excludeCredentials", [])}
        if any(b64u(c.credential_id) in excluded for c in self.credentials if c.rp_id == rp_id):
            raise PermissionError("InvalidStateError: a passkey for this account already exists on this device")
        key = ec.generate_private_key(ec.SECP256R1())
        numbers = key.public_key().public_numbers()
        cose = {1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")}
        credential_id = os.urandom(32)
        stored = StoredCredential(credential_id, rp_id, unb64u(options["user"]["id"]), options["user"]["name"], key)
        self.credentials.append(stored)
        auth_data = (hashlib.sha256(rp_id.encode()).digest() + bytes([self._flags(True)]) + struct.pack(">I", 0)
                     + AAGUID + struct.pack(">H", len(credential_id)) + credential_id + cbor2.dumps(cose))
        client_data = self._client_data("webauthn.create", options["challenge"], origin)
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64u(credential_id), "rawId": b64u(credential_id), "type": "public-key",
            "authenticatorAttachment": "platform", "clientExtensionResults": {},
            "response": {"clientDataJSON": b64u(client_data), "attestationObject": b64u(attestation),
                         "transports": ["internal", "hybrid"]},
        }

    def get(self, options: dict, origin: str, *, credential_id: bytes | None = None) -> dict:
        """navigator.credentials.get({publicKey: options}) -> credential.toJSON(); picks the first
        discoverable credential for the rp (or ``credential_id``)."""
        rp_id = options["rpId"]
        allowed = {c["id"] for c in options.get("allowCredentials", [])}
        candidates = [c for c in self.credentials if c.rp_id == rp_id
                      and (not allowed or b64u(c.credential_id) in allowed)
                      and (credential_id is None or c.credential_id == credential_id)]
        if not candidates:
            raise LookupError("NotAllowedError: no passkey for this site on this device")
        cred = candidates[0]
        cred.sign_count += self.counter_step
        auth_data = hashlib.sha256(rp_id.encode()).digest() + bytes([self._flags(False)]) + struct.pack(">I", cred.sign_count)
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        signature = cred.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        if self.tamper:
            signature = signature[:-1] + bytes([signature[-1] ^ 1])
        return {
            "id": b64u(cred.credential_id), "rawId": b64u(cred.credential_id), "type": "public-key",
            "authenticatorAttachment": "platform", "clientExtensionResults": {},
            "response": {"clientDataJSON": b64u(client_data), "authenticatorData": b64u(auth_data),
                         "signature": b64u(signature), "userHandle": b64u(cred.user_handle)},
        }

    @staticmethod
    def _client_data(kind: str, challenge: str, origin: str) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False},
                          separators=(",", ":")).encode()
