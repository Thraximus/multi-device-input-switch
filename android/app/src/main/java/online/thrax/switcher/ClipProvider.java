package online.thrax.switcher;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.nio.file.Files;

/** Serves the image we last put on the clipboard (content://<app id>.clip/clip-<hash>.png). */
public final class ClipProvider extends ContentProvider {
    /** Matches android:authorities="${applicationId}.clip" in the manifest. */
    static String authority(Context ctx) {
        return ctx.getPackageName() + ".clip";
    }

    /** Store an image and return its URI; older images are removed (only the current clip is served). */
    static Uri publish(Context ctx, byte[] png) throws IOException {
        File dir = new File(ctx.getCacheDir(), "clip");
        dir.mkdirs();
        String name = "clip-" + ClipboardBridge.hash(ClipboardBridge.PNG, png).substring(0, 16) + ".png";
        File[] old = dir.listFiles();
        if (old != null) for (File f : old) if (!f.getName().equals(name)) f.delete();
        Files.write(new File(dir, name).toPath(), png);
        return Uri.parse("content://" + authority(ctx) + "/" + name);
    }

    private File file(Uri uri) throws FileNotFoundException {
        String name = uri.getLastPathSegment();
        if (name == null || !name.matches("clip-[0-9a-f]{16}\\.png")) throw new FileNotFoundException(uri.toString());
        File f = new File(new File(getContext().getCacheDir(), "clip"), name);
        if (!f.isFile()) throw new FileNotFoundException(uri.toString());
        return f;
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public String getType(Uri uri) {
        return "image/png";
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        return ParcelFileDescriptor.open(file(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] args, String sort) {
        MatrixCursor c = new MatrixCursor(new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE});
        try {
            File f = file(uri);
            c.addRow(new Object[]{f.getName(), f.length()});
        } catch (FileNotFoundException ignored) {
        }
        return c;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        return null;
    }

    @Override
    public int delete(Uri uri, String selection, String[] args) {
        return 0;
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] args) {
        return 0;
    }
}
