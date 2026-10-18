"""One-time pairing with the hub.

The bootstrap command hands us the hub's address, a one-time token and the hub certificate's SHA-256
fingerprint. We connect to the pairing port, check the certificate against that fingerprint, and send
our own certificate; the hub answers with what we need for every later connection.
"""
import asyncio
import hashlib
import json
import platform
import secrets
import socket
import ssl

from websockets.asyncio.client import connect

from .tls import make_self_signed


async def pair(state, hub, port, token, fingerprint):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # verified below by fingerprint, before anything is sent
    if not state.cert_path.exists():
        # Unique subject: the hub trusts agents' self-signed certs as roots, and OpenSSL finds those by
        # name, so two agents named alike (e.g. a reinstall on the same machine) would clash.
        key_pem, cert_pem = make_self_signed(f"agent-{socket.gethostname()}-{secrets.token_hex(4)}")
        state.key_path.write_bytes(key_pem)
        state.cert_path.write_bytes(cert_pem)

    async with connect(f"wss://{hub}:{port}", ssl=ctx) as ws:
        der = ws.transport.get_extra_info("ssl_object").getpeercert(binary_form=True)
        if hashlib.sha256(der).hexdigest() != fingerprint.lower():
            raise SystemExit("hub certificate does not match the expected fingerprint; not pairing")
        hub_cert = ssl.DER_cert_to_PEM_cert(der)
        await ws.send(json.dumps({
            "type": "pair", "token": token, "name": socket.gethostname(),
            "platform": "windows" if platform.system() == "Windows" else platform.system().lower(),
            "cert_pem": state.cert_path.read_text(),
        }))
        reply = json.loads(await asyncio.wait_for(ws.recv(), 10))
    if reply.get("type") != "paired":
        raise SystemExit(f"pairing refused: {reply.get('error', reply)}")
    state.save_hub({"host": hub, "api_port": reply["api_port"], "hub_cert": hub_cert,
                    "sign_public_key": reply["sign_public_key"], "agent_id": reply["agent_id"]})
    return reply["agent_id"]
