package online.thrax.switcher;

import android.util.Base64;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.math.BigInteger;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.MessageDigest;
import java.security.Principal;
import java.security.PrivateKey;
import java.security.SecureRandom;
import java.security.Signature;
import java.security.cert.CertificateException;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.security.spec.ECGenParameterSpec;
import java.security.spec.PKCS8EncodedKeySpec;
import java.text.SimpleDateFormat;
import java.util.Arrays;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;

import javax.net.ssl.KeyManager;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509KeyManager;
import javax.net.ssl.X509TrustManager;

/**
 * The agent's identity and TLS, matching the Windows agent (agent/tls.py): a self-signed EC P-256
 * certificate with CA:TRUE (the hub loads agent certificates as trust roots), and the hub's own
 * certificate pinned byte for byte instead of checked against any CA.
 *
 * Android has no public API that writes certificates, so the few DER structures needed are built here.
 */
public final class Certs {
    private Certs() {}

    /** [PKCS#8 private key DER, certificate DER] for a new identity. */
    static byte[][] makeSelfSigned(String commonName) throws Exception {
        KeyPairGenerator gen = KeyPairGenerator.getInstance("EC");
        gen.initialize(new ECGenParameterSpec("secp256r1"));
        KeyPair kp = gen.generateKeyPair();

        byte[] serial = new byte[16];
        new SecureRandom().nextBytes(serial);
        byte[] ecdsaSha256 = seq(oid("1.2.840.10045.4.3.2"));
        byte[] name = seq(set(seq(oid("2.5.4.3"), tlv(0x0C, commonName.getBytes(StandardCharsets.UTF_8)))));
        long now = System.currentTimeMillis();
        byte[] validity = seq(utcTime(now - 86_400_000L), utcTime(now + 20L * 365 * 86_400_000L));
        byte[] basicConstraints = seq(oid("2.5.29.19"), tlv(0x01, new byte[]{(byte) 0xFF}),
                tlv(0x04, seq(tlv(0x01, new byte[]{(byte) 0xFF}))));
        byte[] tbs = seq(
                tlv(0xA0, tlv(0x02, new byte[]{2})),            // v3
                tlv(0x02, new BigInteger(1, serial).toByteArray()),
                ecdsaSha256, name, validity, name,
                kp.getPublic().getEncoded(),                    // SubjectPublicKeyInfo
                tlv(0xA3, seq(basicConstraints)));
        Signature sig = Signature.getInstance("SHA256withECDSA");
        sig.initSign(kp.getPrivate());
        sig.update(tbs);
        byte[] signature = sig.sign();
        byte[] bits = new byte[signature.length + 1];               // BIT STRING: no unused bits
        System.arraycopy(signature, 0, bits, 1, signature.length);
        byte[] cert = seq(tbs, ecdsaSha256, tlv(0x03, bits));
        return new byte[][]{kp.getPrivate().getEncoded(), cert};
    }

    static String pem(byte[] certDer) {
        String b64 = Base64.encodeToString(certDer, Base64.NO_WRAP);
        StringBuilder sb = new StringBuilder("-----BEGIN CERTIFICATE-----\n");
        for (int i = 0; i < b64.length(); i += 64) {
            sb.append(b64, i, Math.min(b64.length(), i + 64)).append('\n');
        }
        return sb.append("-----END CERTIFICATE-----\n").toString();
    }

    static X509Certificate parse(byte[] der) throws CertificateException {
        return (X509Certificate) CertificateFactory.getInstance("X.509")
                .generateCertificate(new ByteArrayInputStream(der));
    }

    static String sha256Hex(byte[] data) throws Exception {
        StringBuilder sb = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-256").digest(data)) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    /** Mutual TLS 1.3 to the paired hub: our key/cert, and only the exact pinned hub certificate. */
    public static SSLSocketFactory hubFactory(byte[] keyDer, byte[] certDer, byte[] hubCertDer) throws Exception {
        PrivateKey key = KeyFactory.getInstance("EC").generatePrivate(new PKCS8EncodedKeySpec(keyDer));
        X509Certificate cert = parse(certDer);
        SSLContext ctx = SSLContext.getInstance("TLSv1.3");
        ctx.init(new KeyManager[]{new FixedKeyManager(key, cert)}, new TrustManager[]{new Pinned(hubCertDer)}, null);
        return ctx.getSocketFactory();
    }

    /** For pairing: accept the server's certificate here and check it against the fingerprint afterwards. */
    static SSLSocketFactory pairingFactory() throws Exception {
        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(null, new TrustManager[]{new Pinned(null)}, null);
        return ctx.getSocketFactory();
    }

    private static final class Pinned implements X509TrustManager {
        private final byte[] der;  // null: accept anything (pairing checks the fingerprint itself)

        Pinned(byte[] der) {
            this.der = der;
        }

        @Override
        public void checkServerTrusted(X509Certificate[] chain, String authType) throws CertificateException {
            if (der != null && (chain.length == 0 || !Arrays.equals(chain[0].getEncoded(), der))) {
                throw new CertificateException("not the paired hub's certificate");
            }
        }

        @Override
        public void checkClientTrusted(X509Certificate[] chain, String authType) throws CertificateException {
            throw new CertificateException("client role only");
        }

        @Override
        public X509Certificate[] getAcceptedIssuers() {
            return new X509Certificate[0];
        }
    }

    private static final class FixedKeyManager implements X509KeyManager {
        private final PrivateKey key;
        private final X509Certificate cert;

        FixedKeyManager(PrivateKey key, X509Certificate cert) {
            this.key = key;
            this.cert = cert;
        }

        @Override
        public String chooseClientAlias(String[] keyType, Principal[] issuers, Socket socket) {
            return "agent";
        }

        @Override
        public String[] getClientAliases(String keyType, Principal[] issuers) {
            return new String[]{"agent"};
        }

        @Override
        public X509Certificate[] getCertificateChain(String alias) {
            return new X509Certificate[]{cert};
        }

        @Override
        public PrivateKey getPrivateKey(String alias) {
            return key;
        }

        @Override
        public String chooseServerAlias(String keyType, Principal[] issuers, Socket socket) {
            return null;
        }

        @Override
        public String[] getServerAliases(String keyType, Principal[] issuers) {
            return null;
        }
    }

    // --- DER -------------------------------------------------------------------------------------------

    static byte[] tlv(int tag, byte[] value) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.write(tag);
        int n = value.length;
        if (n < 0x80) {
            out.write(n);
        } else {
            byte[] len = BigInteger.valueOf(n).toByteArray();
            int skip = len[0] == 0 ? 1 : 0;
            out.write(0x80 | (len.length - skip));
            out.write(len, skip, len.length - skip);
        }
        out.write(value, 0, n);
        return out.toByteArray();
    }

    static byte[] seq(byte[]... parts) {
        return tlv(0x30, concat(parts));
    }

    static byte[] set(byte[]... parts) {
        return tlv(0x31, concat(parts));
    }

    static byte[] oid(String dotted) {
        String[] p = dotted.split("\\.");
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.write(Integer.parseInt(p[0]) * 40 + Integer.parseInt(p[1]));
        for (int i = 2; i < p.length; i++) {
            long v = Long.parseLong(p[i]);
            byte[] tmp = new byte[10];
            int k = tmp.length;
            tmp[--k] = (byte) (v & 0x7F);
            while ((v >>= 7) > 0) tmp[--k] = (byte) (0x80 | (v & 0x7F));
            out.write(tmp, k, tmp.length - k);
        }
        return tlv(0x06, out.toByteArray());
    }

    static byte[] utcTime(long millis) {
        SimpleDateFormat f = new SimpleDateFormat("yyMMddHHmmss'Z'", Locale.US);
        f.setTimeZone(TimeZone.getTimeZone("UTC"));
        return tlv(0x17, f.format(new Date(millis)).getBytes(StandardCharsets.US_ASCII));
    }

    private static byte[] concat(byte[]... parts) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        for (byte[] b : parts) out.write(b, 0, b.length);
        return out.toByteArray();
    }
}
