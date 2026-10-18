package online.thrax.switcher;

import android.content.ClipData;
import android.content.ClipDescription;
import android.content.ClipboardManager;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.PixelFormat;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.PersistableBundle;
import android.provider.Settings;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The tablet's side of clipboard sync.
 *
 * Writing is easy: any app may set the clipboard. Reading is not: since Android 10 only the focused app
 * (or the keyboard) may read it, and change listeners aren't called for background apps. But each time
 * Android refuses us, ClipboardService logs "Denying clipboard access to <package>" — so with READ_LOGS
 * (granted over ADB) we watch the log for that line as our "something was copied" signal, then add a
 * 1×1 invisible overlay window (SYSTEM_ALERT_WINDOW, also granted over ADB), which briefly takes focus
 * and lets us read the clip like a foreground app, and remove it again.
 */
public final class ClipboardBridge {
    public static final String TEXT = "text/plain;charset=utf-8";
    public static final String PNG = "image/png";
    static final int MAX_BYTES = 16 << 20;
    private static final String TAG = "SwitcherClip";
    private static final long OVERLAY_TIMEOUT_MS = 1500;
    private static final long QUIET_AFTER_READ_MS = 800;

    public interface Sink {
        void copied(String mime, byte[] data, boolean secret);

        void status(String what);

        /** False when Android won't show us other apps' log lines (Android 13+ asks the user, and refuses
         *  apps in the background), so copies made on the device go unnoticed. */
        void logAccess(boolean ok);
    }

    private static final long LOG_ACCESS_GRACE_MS = 20_000;
    private static final Pattern IMG_SRC = Pattern.compile("<img[^>]+src=[\"']([^\"']+)[\"']", Pattern.CASE_INSENSITIVE);
    private static final Pattern PID = Pattern.compile("^\\S+\\(\\s*(\\d+)\\)");

    private final Context ctx;
    private final ClipboardManager cm;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Sink sink;
    private volatile String lastHash;      // last item sent or applied (echo suppression)
    private volatile long quietUntil;      // ignore log triggers caused by our own read attempts
    private volatile boolean running = true;
    private View overlay;
    private volatile Process logcat;
    private volatile boolean logAccess;
    private volatile boolean restartNow;

    private final ClipboardManager.OnPrimaryClipChangedListener listener = () -> trigger("listener");

    public ClipboardBridge(Context ctx, Sink sink) {
        this.ctx = ctx;
        this.sink = sink;
        this.cm = (ClipboardManager) ctx.getSystemService(Context.CLIPBOARD_SERVICE);
        // Delivered only while we're focused (e.g. the app is open); the log watcher covers the rest.
        cm.addPrimaryClipChangedListener(listener);
        Thread t = new Thread(this::watchLog, "clip-log");
        t.setDaemon(true);
        t.start();
    }

    public void stop() {
        running = false;
        cm.removePrimaryClipChangedListener(listener);
        Process p = logcat;
        if (p != null) p.destroy();
    }

    // --- hub -> tablet ---------------------------------------------------------------------------------

    public void apply(String mime, byte[] data, boolean secret) {
        lastHash = hash(mime, data);
        main.post(() -> {
            try {
                ClipData clip;
                if (TEXT.equals(mime)) {
                    clip = ClipData.newPlainText("Switcher", new String(data, StandardCharsets.UTF_8));
                } else if (PNG.equals(mime)) {
                    clip = ClipData.newUri(ctx.getContentResolver(), "image", ClipProvider.publish(ctx, data));
                } else {
                    return;
                }
                if (secret && Build.VERSION.SDK_INT >= 33) {
                    PersistableBundle extras = new PersistableBundle();
                    extras.putBoolean("android.content.extra.IS_SENSITIVE", true);
                    clip.getDescription().setExtras(extras);
                }
                cm.setPrimaryClip(clip);
            } catch (Exception e) {
                Log.w(TAG, "could not set clipboard", e);
                sink.status("could not set clipboard: " + e.getMessage());
            }
        });
    }

    // --- tablet -> hub ---------------------------------------------------------------------------------

    /** Start a new log reader, e.g. while our activity is on screen so Android 13+ can ask the user. */
    public void restartLogWatch() {
        restartNow = true;
        Process p = logcat;
        if (p != null) p.destroy();
    }

    public boolean hasLogAccess() {
        return logAccess;
    }

    private void watchLog() {
        String me = ctx.getPackageName();
        int myPid = android.os.Process.myPid();
        while (running) {
            logAccess = false;
            long started = System.currentTimeMillis();
            main.postDelayed(() -> {
                if (running && !logAccess && System.currentTimeMillis() - started >= LOG_ACCESS_GRACE_MS) {
                    sink.status("can't see copies made on this device: log access not allowed");
                    sink.logAccess(false);
                }
            }, LOG_ACCESS_GRACE_MS);
            try {
                // -T 1: start at the newest line; only warnings and errors (the denial is an error).
                logcat = Runtime.getRuntime().exec(new String[]{"logcat", "-T", "1", "-v", "brief", "*:W"});
                BufferedReader r = new BufferedReader(new InputStreamReader(logcat.getInputStream()));
                String line;
                while (running && (line = r.readLine()) != null) {
                    if (!logAccess) {
                        Matcher m = PID.matcher(line);
                        if (m.find() && Integer.parseInt(m.group(1)) != myPid) {
                            logAccess = true;  // we see other processes' lines: READ_LOGS is in effect
                            sink.status("watching for copies");
                            sink.logAccess(true);
                        }
                    }
                    if (line.contains(me) && line.toLowerCase().contains("clipboard") && line.contains("Denying")) {
                        trigger("log");
                    }
                }
            } catch (Exception e) {
                Log.w(TAG, "logcat watch failed", e);
            }
            Process p = logcat;
            if (p != null) p.destroy();
            if (restartNow) {
                restartNow = false;
                continue;
            }
            try {
                Thread.sleep(2000);
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    private void trigger(String why) {
        if (System.currentTimeMillis() < quietUntil) return;
        Log.d(TAG, "clipboard changed? (" + why + ")");
        main.post(this::readViaOverlay);
    }

    /** Read now if we're allowed; otherwise borrow focus with an invisible overlay window. */
    private void readViaOverlay() {
        if (overlay != null) return;
        if (!Settings.canDrawOverlays(ctx)) {
            sink.status("can't read copies: 'display over other apps' not granted (reinstall from the hub)");
            return;
        }
        WindowManager wm = (WindowManager) ctx.getSystemService(Context.WINDOW_SERVICE);
        View v = new View(ctx) {
            @Override
            public void onWindowFocusChanged(boolean hasFocus) {
                super.onWindowFocusChanged(hasFocus);
                if (hasFocus) {
                    readNow();
                    removeOverlay(wm);
                }
            }
        };
        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(1, 1,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE,
                PixelFormat.TRANSLUCENT);
        lp.gravity = Gravity.TOP | Gravity.START;
        try {
            wm.addView(v, lp);
            overlay = v;
            main.postDelayed(() -> removeOverlay(wm), OVERLAY_TIMEOUT_MS);
        } catch (Exception e) {
            Log.w(TAG, "overlay failed", e);
            sink.status("overlay failed: " + e.getMessage());
        }
    }

    private void removeOverlay(WindowManager wm) {
        if (overlay == null) return;
        try {
            wm.removeView(overlay);
        } catch (Exception ignored) {
        }
        overlay = null;
    }

    private void readNow() {
        quietUntil = System.currentTimeMillis() + QUIET_AFTER_READ_MS;
        ClipData clip;
        try {
            clip = cm.getPrimaryClip();
        } catch (SecurityException e) {
            Log.w(TAG, "read refused: " + e.getMessage());
            return;
        }
        if (clip == null || clip.getItemCount() == 0) {
            Log.w(TAG, "read: no clip (focus not granted?)");
            return;
        }
        ClipDescription desc = clip.getDescription();
        boolean secret = false;
        if (desc != null && Build.VERSION.SDK_INT >= 24 && desc.getExtras() != null) {
            secret = desc.getExtras().getBoolean("android.content.extra.IS_SENSITIVE", false);
        }
        ClipData.Item item = clip.getItemAt(0);
        String mime = null;
        byte[] data = null;
        try {
            // Any attached URI may be the picture, whatever the clip claims: Samsung Notes, for one, labels an
            // image copy text/html and attaches the image as a content:// URI.
            if (item.getUri() != null) {
                try {
                    data = toPng(item.getUri());
                    if (data != null) mime = PNG;
                } catch (Exception e) {
                    Log.w(TAG, "could not open " + item.getUri() + ": " + e);
                }
            }
            if (data == null && item.getHtmlText() != null) {
                Matcher img = IMG_SRC.matcher(item.getHtmlText());
                if (img.find()) {
                    try {
                        data = toPng(Uri.parse(img.group(1)));
                        if (data != null) mime = PNG;
                    } catch (Exception e) {
                        Log.w(TAG, "could not open " + img.group(1) + ": " + e);
                    }
                }
            }
            if (data == null) {
                CharSequence text = item.coerceToText(ctx);
                if (text != null && text.length() > 0) {
                    data = text.toString().getBytes(StandardCharsets.UTF_8);
                    mime = TEXT;
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "could not read clip", e);
            return;
        }
        Log.i(TAG, "read: " + (desc != null ? desc.getMimeType(0) : "?") + " uri=" + item.getUri()
                + " html=" + (item.getHtmlText() == null ? "-" : item.getHtmlText().substring(0, Math.min(160, item.getHtmlText().length())))
                + " -> " + mime + " " + (data == null ? "nothing" : data.length + " bytes"));
        if (data == null || data.length > MAX_BYTES) return;
        String h = hash(mime, data);
        if (h.equals(lastHash)) {
            Log.i(TAG, "read: same as the last item applied/sent, not sending");
            return;
        }
        lastHash = h;
        sink.copied(mime, data, secret);
    }

    private byte[] toPng(Uri uri) throws Exception {
        try (InputStream in = ctx.getContentResolver().openInputStream(uri)) {
            if (in == null) return null;
            ByteArrayOutputStream raw = new ByteArrayOutputStream();
            byte[] buf = new byte[65536];
            int n;
            while ((n = in.read(buf)) > 0) {
                raw.write(buf, 0, n);
                if (raw.size() > MAX_BYTES) return null;
            }
            byte[] bytes = raw.toByteArray();
            if (bytes.length > 8 && bytes[0] == (byte) 0x89 && bytes[1] == 'P') return bytes;  // already PNG
            Bitmap bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
            if (bmp == null) return null;
            ByteArrayOutputStream png = new ByteArrayOutputStream();
            bmp.compress(Bitmap.CompressFormat.PNG, 100, png);
            return png.toByteArray();
        }
    }

    static String hash(String mime, byte[] data) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            md.update(mime.getBytes(StandardCharsets.UTF_8));
            md.update((byte) 0);
            md.update(data);
            StringBuilder sb = new StringBuilder();
            for (byte b : md.digest()) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

}
