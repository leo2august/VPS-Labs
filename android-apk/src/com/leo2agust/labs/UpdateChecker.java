package com.leo2agust.labs;

import android.app.AlertDialog;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Cek update Labs APK dari GitHub Releases (leo2august/VPS-Labs).
 * Membandingkan tag versi remote vs versi lokal; kalau ada yang lebih baru,
 * tampilkan dialog dengan tombol Unduh.
 */
public class UpdateChecker {

    public interface UpdateListener {
        void onResult(boolean hasUpdate, String latestVersion, String apkUrl, String notes);
    }

    private static final String RELEASES_API = "https://api.github.com/repos/leo2august/VPS-Labs/releases/latest";
    private static final String ASSET_NAME = "Labs-";

    public static void check(final Context context, final boolean silentIfCurrent, final UpdateListener listener) {
        new Thread(new Runnable() {
            public void run() {
                String tag = "";
                String apkUrl = "";
                String notes = "";
                boolean ok = false;
                try {
                    URL u = new URL(RELEASES_API);
                    HttpURLConnection c = (HttpURLConnection) u.openConnection();
                    c.setConnectTimeout(8000);
                    c.setReadTimeout(8000);
                    c.setRequestProperty("Accept", "application/vnd.github+json");
                    c.setRequestProperty("User-Agent", "LabsAPK");
                    int code = c.getResponseCode();
                    if (code == 200) {
                        BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream()));
                        StringBuilder sb = new StringBuilder();
                        String l;
                        while ((l = r.readLine()) != null) sb.append(l);
                        String body = sb.toString();
                        // parse tag_name
                        int i = body.indexOf("\"tag_name\":");
                        if (i >= 0) {
                            int s = body.indexOf('"', i + 11) + 1;
                            int e = body.indexOf('"', s);
                            tag = body.substring(s, e);
                        }
                        // parse body (catatan rilis)
                        int bi = body.indexOf("\"body\":");
                        if (bi >= 0) {
                            int s = body.indexOf('"', bi + 7) + 1;
                            int e = body.indexOf('"', s);
                            if (e > s) notes = body.substring(s, e).replace("\\n", "\n").replace("\\r", "").replace("\\\"", "\"");
                        }
                        // cari asset APK
                        int ai = body.indexOf("\"name\":\"" + ASSET_NAME);
                        while (ai >= 0) {
                            int di = body.indexOf("\"browser_download_url\":", ai);
                            if (di >= 0) {
                                int s = body.indexOf('"', di + 23) + 1;
                                int e = body.indexOf('"', s);
                                String candidate = body.substring(s, e);
                                if (candidate.endsWith(".apk")) {
                                    apkUrl = candidate;
                                    break;
                                }
                            }
                            ai = body.indexOf("\"name\":\"" + ASSET_NAME, ai + 1);
                        }
                        ok = true;
                    }
                    c.disconnect();
                } catch (Exception e) {
                    ok = false;
                }

                final String fTag = tag;
                final String fUrl = apkUrl;
                final String fNotes = notes;
                final boolean fOk = ok;

                new Handler(Looper.getMainLooper()).post(new Runnable() {
                    public void run() {
                        String current = localVersion(context);
                        boolean hasUpdate = fOk && fUrl.length() > 0 && !fTag.isEmpty() && !fTag.equalsIgnoreCase(current) && !fTag.equalsIgnoreCase("v" + current);
                        if (listener != null) {
                            listener.onResult(hasUpdate, fTag, fUrl, fNotes);
                        } else if (hasUpdate) {
                            showDialog(context, fTag, fUrl, fNotes);
                        } else if (!silentIfCurrent) {
                            Toast.makeText(context, "Labs sudah versi terbaru (" + current + ")", Toast.LENGTH_SHORT).show();
                        }
                    }
                });
            }
        }).start();
    }

    private static String localVersion(Context context) {
        try {
            return context.getPackageManager().getPackageInfo(context.getPackageName(), 0).versionName;
        } catch (Exception e) {
            return "1.0.2";
        }
    }

    private static void showDialog(final Context context, final String tag, final String apkUrl, final String notes) {
        AlertDialog.Builder b = new AlertDialog.Builder(context);
        b.setTitle("🔄 Update tersedia: " + tag);
        StringBuilder msg = new StringBuilder("Versi baru Labs APK sudah rilis.");
        if (notes != null && notes.trim().length() > 0) {
            msg.append("\n\n").append(notes.trim());
        }
        b.setMessage(msg.toString());
        b.setPositiveButton("⬇️ Unduh", new DialogInterface.OnClickListener() {
            public void onClick(DialogInterface d, int w) {
                try {
                    context.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(apkUrl)));
                } catch (Exception e) {
                    Toast.makeText(context, "Gagal buka tautan unduhan", Toast.LENGTH_LONG).show();
                }
            }
        });
        b.setNegativeButton("Nanti", null);
        b.show();
    }
}
