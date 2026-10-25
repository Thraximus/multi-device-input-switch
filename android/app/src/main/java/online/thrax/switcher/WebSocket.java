package online.thrax.switcher;

import android.util.Base64;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;

import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;

/**
 * Minimal WebSocket client (RFC 6455) over TLS: text messages, ping/pong, close. Enough for the hub's
 * JSON protocol without pulling a library into the APK.
 */
public final class WebSocket implements AutoCloseable {
    static final int MAX_MESSAGE = 32 << 20;

    private final SSLSocket socket;
    private final DataInputStream in;
    private final OutputStream out;
    private final SecureRandom random = new SecureRandom();

    private WebSocket(SSLSocket socket, InputStream in) throws IOException {
        this.socket = socket;
        this.in = new DataInputStream(in);
        this.out = socket.getOutputStream();
    }

    public static WebSocket connect(SSLSocketFactory factory, String host, int port, int readTimeoutMs) throws IOException {
        SSLSocket s = (SSLSocket) factory.createSocket();
        try {
            s.connect(new InetSocketAddress(host, port), 10_000);
            s.setTcpNoDelay(true);  // small messages (touchpad movement) go out at once
            s.setSoTimeout(readTimeoutMs);
            s.startHandshake();
            byte[] nonce = new byte[16];
            new SecureRandom().nextBytes(nonce);
            String req = "GET / HTTP/1.1\r\nHost: " + host + ":" + port + "\r\nUpgrade: websocket\r\n"
                    + "Connection: Upgrade\r\nSec-WebSocket-Key: " + Base64.encodeToString(nonce, Base64.NO_WRAP)
                    + "\r\nSec-WebSocket-Version: 13\r\n\r\n";
            OutputStream o = s.getOutputStream();
            o.write(req.getBytes(StandardCharsets.US_ASCII));
            o.flush();
            InputStream in = new BufferedInputStream(s.getInputStream());
            String status = readLine(in);
            if (!status.contains(" 101 ")) throw new IOException("websocket upgrade refused: " + status);
            while (!readLine(in).isEmpty()) { /* skip headers */ }
            return new WebSocket(s, in);
        } catch (IOException e) {
            try {
                s.close();
            } catch (IOException ignored) {
            }
            throw e;
        }
    }

    SSLSocket socket() {
        return socket;
    }

    public void send(String text) throws IOException {
        frame(0x1, text.getBytes(StandardCharsets.UTF_8));
    }

    public void ping() throws IOException {
        frame(0x9, new byte[0]);
    }

    /** Next text message; answers pings on the way. Throws EOFException when the hub closes. */
    public String receive() throws IOException {
        ByteArrayOutputStream message = new ByteArrayOutputStream();
        while (true) {
            int b0 = in.readUnsignedByte();
            int b1 = in.readUnsignedByte();
            boolean fin = (b0 & 0x80) != 0;
            int opcode = b0 & 0x0F;
            long len = b1 & 0x7F;
            if (len == 126) len = in.readUnsignedShort();
            else if (len == 127) len = in.readLong();
            if (len < 0 || message.size() + len > MAX_MESSAGE) throw new IOException("message too large");
            byte[] mask = null;
            if ((b1 & 0x80) != 0) {
                mask = new byte[4];
                in.readFully(mask);
            }
            byte[] payload = new byte[(int) len];
            in.readFully(payload);
            if (mask != null) for (int i = 0; i < payload.length; i++) payload[i] ^= mask[i & 3];
            switch (opcode) {
                case 0x9:
                    frame(0xA, payload);
                    break;
                case 0xA:
                    break;
                case 0x8:
                    throw new EOFException("hub closed the connection");
                default:  // text, binary, continuation
                    message.write(payload, 0, payload.length);
                    if (fin) return message.toString("UTF-8");
            }
        }
    }

    private synchronized void frame(int opcode, byte[] payload) throws IOException {
        ByteArrayOutputStream f = new ByteArrayOutputStream(payload.length + 14);
        f.write(0x80 | opcode);
        int n = payload.length;
        if (n < 126) {
            f.write(0x80 | n);
        } else if (n < 65536) {
            f.write(0x80 | 126);
            f.write(n >> 8);
            f.write(n);
        } else {
            f.write(0x80 | 127);
            for (int i = 7; i >= 0; i--) f.write((int) ((long) n >> (8 * i)));
        }
        byte[] mask = new byte[4];  // clients must mask every frame
        random.nextBytes(mask);
        f.write(mask, 0, 4);
        for (int i = 0; i < n; i++) f.write(payload[i] ^ mask[i & 3]);
        out.write(f.toByteArray());
        out.flush();
    }

    private static String readLine(InputStream in) throws IOException {
        StringBuilder sb = new StringBuilder();
        int c;
        while ((c = in.read()) != -1) {
            if (c == '\n') break;
            if (c != '\r') sb.append((char) c);
            if (sb.length() > 8192) throw new IOException("header line too long");
        }
        if (c == -1 && sb.length() == 0) throw new EOFException();
        return sb.toString();
    }

    @Override
    public void close() {
        try {
            frame(0x8, new byte[]{0x03, (byte) 0xE8});  // 1000 normal closure
        } catch (IOException ignored) {
        }
        try {
            socket.close();
        } catch (IOException ignored) {
        }
    }
}
