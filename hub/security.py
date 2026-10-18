"""Hub identity, agent pairing and update signing.

- TLS: the hub and each agent have a self-signed EC certificate. Agents pin the hub certificate;
  the hub only accepts agent certificates it stored during pairing (mutual TLS).
- Pairing: a one-time token, valid for a few minutes, proves the agent was invited by the user.
- Updates: manifests are signed with the hub's Ed25519 key, which agents receive during pairing.
"""
import base64
import datetime
import hashlib
import hmac
import json
import secrets
import ssl
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import NameOID

from .config import CONFIG_DIR

PAIRING_TTL = 600


def make_self_signed(common_name):
    """Return (key_pem, cert_pem) for a new self-signed EC P-256 certificate valid for 20 years."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365 * 20))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return key_pem, cert.public_bytes(serialization.Encoding.PEM)


def fingerprint(cert):
    """SHA-256 fingerprint (hex) of a certificate given as PEM bytes/str or DER bytes."""
    if isinstance(cert, str):
        cert = cert.encode()
    if cert.startswith(b"-----BEGIN"):
        cert = x509.load_pem_x509_certificate(cert).public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(cert).hexdigest()


class Identity:
    def __init__(self, directory=CONFIG_DIR):
        self.dir = directory / "identity"
        self.agents_dir = directory / "agents"
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.agents_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.key_path = self.dir / "hub.key"
        self.cert_path = self.dir / "hub.crt"
        self.sign_path = self.dir / "sign.key"
        self._ensure()
        self.cert_pem = self.cert_path.read_text()
        self.fingerprint = fingerprint(self.cert_pem)
        self._sign_key = serialization.load_pem_private_key(self.sign_path.read_bytes(), None)
        self.sign_public_b64 = base64.b64encode(self._sign_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
        self._tokens = {}  # token -> (expiry, target id)

    def _ensure(self):
        if not self.key_path.exists():
            key_pem, cert_pem = make_self_signed("pi-switcher-hub")
            self._write_secret(self.key_path, key_pem)
            self.cert_path.write_bytes(cert_pem)
        if not self.sign_path.exists():
            key = ed25519.Ed25519PrivateKey.generate()
            self._write_secret(self.sign_path, key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))

    @staticmethod
    def _write_secret(path, data):
        path.touch(mode=0o600)
        path.write_bytes(data)

    # --- agents --------------------------------------------------------------

    def agents(self):
        return {p.stem: json.loads(p.read_text()) for p in sorted(self.agents_dir.glob("*.json"))}

    def agent_by_fingerprint(self, fp):
        agent = self.agents().get(fp[:16])
        return agent if agent and agent["fingerprint"] == fp else None

    def add_agent(self, name, platform, cert_pem, target):
        fp = fingerprint(cert_pem)
        agent = {"id": fp[:16], "name": name, "platform": platform, "target": target,
                 "fingerprint": fp, "cert_pem": cert_pem, "paired_at": int(time.time())}
        (self.agents_dir / f"{agent['id']}.json").write_text(json.dumps(agent, indent=2))
        return agent

    def remove_agent(self, agent_id):
        path = self.agents_dir / f"{agent_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    # --- pairing -------------------------------------------------------------

    def new_pairing_token(self, target):
        self._expire_tokens()
        token = secrets.token_urlsafe(16)
        self._tokens[token] = (time.monotonic() + PAIRING_TTL, target)
        return token

    def consume_pairing_token(self, token):
        """Return the target id the token was issued for, or None. Tokens are single-use."""
        self._expire_tokens()
        for known in list(self._tokens):
            if hmac.compare_digest(known, token):
                return self._tokens.pop(known)[1]
        return None

    def pairing_token_valid(self, token):
        """True if token is live (without consuming it), e.g. for the bootstrap download."""
        self._expire_tokens()
        return any(hmac.compare_digest(known, token) for known in self._tokens)

    def pairing_active(self):
        self._expire_tokens()
        return bool(self._tokens)

    def _expire_tokens(self):
        now = time.monotonic()
        self._tokens = {t: v for t, v in self._tokens.items() if v[0] > now}

    # --- TLS -----------------------------------------------------------------

    def server_context(self, require_client_cert):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # Agents bundle OpenSSL and use TLS 1.3. The pairing port also serves the bootstrap download to
        # Windows PowerShell 5.1, whose Schannel on Windows 10 has no TLS 1.3.
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3 if require_client_cert else ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(self.cert_path, self.key_path)
        if require_client_cert:
            ctx.verify_mode = ssl.CERT_REQUIRED
            # The hub's own cert keeps the trust store non-empty before any agent is paired.
            ctx.load_verify_locations(cadata=self.cert_pem)
            for agent in self.agents().values():
                ctx.load_verify_locations(cadata=agent["cert_pem"])
        return ctx

    # --- signing -------------------------------------------------------------

    def sign(self, data):
        return base64.b64encode(self._sign_key.sign(data)).decode()


def verify_signature(public_b64, data, signature_b64):
    """Agent side: check a manifest signature against the hub key received during pairing."""
    key = ed25519.Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64))
    try:
        key.verify(base64.b64decode(signature_b64), data)
        return True
    except Exception:
        return False
