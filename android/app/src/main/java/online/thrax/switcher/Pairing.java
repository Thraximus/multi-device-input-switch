package online.thrax.switcher;

import android.os.Build;
import android.util.Base64;

import org.json.JSONObject;

/**
 * One-time pairing, as in agent/pairing.py: connect to the hub's pairing port, check its certificate
 * against the fingerprint we were given, send our certificate with the one-time token, keep what the
 * hub answers.
 */
public final class Pairing {
    private Pairing() {}

    public static String pair(HubState state, String host, int port, String token, String fingerprint) throws Exception {
        byte[][] id = state.identity();
        try (WebSocket ws = WebSocket.connect(Certs.pairingFactory(), host, port, 15_000)) {
            byte[] hubCert = ws.socket().getSession().getPeerCertificates()[0].getEncoded();
            if (!Certs.sha256Hex(hubCert).equalsIgnoreCase(fingerprint)) {
                throw new SecurityException("hub certificate does not match the expected fingerprint; not pairing");
            }
            ws.send(new JSONObject()
                    .put("type", "pair").put("token", token).put("name", Build.MODEL).put("platform", "android")
                    .put("cert_pem", Certs.pem(id[1])).toString());
            JSONObject reply = new JSONObject(ws.receive());
            if (!"paired".equals(reply.optString("type"))) {
                throw new IllegalStateException("pairing refused: " + reply.optString("error", reply.toString()));
            }
            state.saveHub(new JSONObject()
                    .put("host", host)
                    .put("api_port", reply.getInt("api_port"))
                    .put("hub_cert_der", Base64.encodeToString(hubCert, Base64.NO_WRAP))
                    .put("sign_public_key", reply.optString("sign_public_key"))
                    .put("agent_id", reply.getString("agent_id")));
            return reply.getString("agent_id");
        }
    }
}
