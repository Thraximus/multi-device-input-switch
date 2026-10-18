package online.thrax.switcher;

import android.content.Context;
import android.os.Build;

import org.json.JSONObject;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.SecureRandom;

/**
 * What pairing left behind, in the app's private storage (like the Windows agent's state folder):
 * our key and certificate, and the hub's address, pinned certificate and agent id.
 */
public final class HubState {
    final File dir;

    public HubState(Context ctx) {
        dir = ctx.getFilesDir();
    }

    private File keyFile() {
        return new File(dir, "agent.key");
    }

    private File certFile() {
        return new File(dir, "agent.crt");
    }

    private File hubFile() {
        return new File(dir, "hub.json");
    }

    public boolean paired() {
        return hubFile().isFile() && certFile().isFile() && keyFile().isFile();
    }

    /** Our identity, created on first use. Unique subject: the hub finds agent roots by name. */
    public byte[][] identity() throws Exception {
        if (!keyFile().isFile() || !certFile().isFile()) {
            byte[] tag = new byte[4];
            new SecureRandom().nextBytes(tag);
            String cn = "agent-" + Build.MODEL.replaceAll("[^A-Za-z0-9-]", "") + "-" + String.format("%02x%02x%02x%02x",
                    tag[0], tag[1], tag[2], tag[3]);
            byte[][] made = Certs.makeSelfSigned(cn);
            Files.write(keyFile().toPath(), made[0]);
            Files.write(certFile().toPath(), made[1]);
        }
        return new byte[][]{Files.readAllBytes(keyFile().toPath()), Files.readAllBytes(certFile().toPath())};
    }

    public JSONObject hub() throws Exception {
        return new JSONObject(new String(Files.readAllBytes(hubFile().toPath()), StandardCharsets.UTF_8));
    }

    void saveHub(JSONObject hub) throws IOException {
        File tmp = new File(dir, "hub.json.tmp");
        Files.write(tmp.toPath(), hub.toString().getBytes(StandardCharsets.UTF_8));
        if (!tmp.renameTo(hubFile())) throw new IOException("could not save hub.json");
    }

    public void forget() {
        hubFile().delete();
        keyFile().delete();
        certFile().delete();
    }
}
