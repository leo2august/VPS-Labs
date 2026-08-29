package com.leo2agust.labs;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;
import android.widget.RemoteViews;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import org.json.JSONObject;
import org.json.JSONArray;

public class LabsPerfWidget extends AppWidgetProvider {
    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        for (int id : ids) update(context, mgr, id);
    }
    static void update(Context ctx, AppWidgetManager mgr, int wid) {
        RemoteViews v = new RemoteViews(ctx.getPackageName(), R.layout.widget_perf);
        v.setTextViewText(R.id.wp_title, "Labs");
        v.setTextViewText(R.id.wp_meta, "Memeriksa…");
        v.setOnClickPendingIntent(R.id.wp_root, PendingIntent.getActivity(ctx, 1,
            new Intent(ctx, MainActivity.class),
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
        mgr.updateAppWidget(wid, v);
        check(ctx, mgr, wid);
    }
    static void check(final Context ctx, final AppWidgetManager mgr, final int wid) {
        new Thread(new Runnable() {
            public void run() {
                String cpu = "--", ram = "--", disk = "--", meta = "Offline";
                String base = ctx.getSharedPreferences("labs_prefs", Context.MODE_PRIVATE).getString("labs_url", "");
                if (base.isEmpty()) meta = "URL belum diatur";
                else {
                    try {
                        URL u = new URL(base + "/api/overview");
                        HttpURLConnection c = (HttpURLConnection) u.openConnection();
                        c.setConnectTimeout(8000); c.setReadTimeout(8000);
                        c.setRequestProperty("Accept", "application/json");
                        int code = c.getResponseCode();
                        if (code == 200) {
                            BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream()));
                            StringBuilder sb = new StringBuilder(); String l;
                            while ((l = r.readLine()) != null) sb.append(l);
                            JSONObject d = new JSONObject(sb.toString());
                            JSONObject sys = d.optJSONObject("system");
                            JSONObject mem = d.optJSONObject("memory");
                            JSONObject diskObj = d.optJSONObject("disk");
                            if (sys != null) cpu = Math.round(sys.optDouble("cpu_percent", 0)) + "%";
                            if (mem != null) {
                                long used = mem.optLong("used", 0), total = mem.optLong("total", 1);
                                ram = Math.round(100.0 * used / Math.max(1, total)) + "%";
                            }
                            if (diskObj != null) {
                                long used = diskObj.optLong("used", 0), total = diskObj.optLong("total", 1);
                                disk = Math.round(100.0 * used / Math.max(1, total)) + "%";
                            }
                            meta = "CPU · RAM · DISK";
                        } else {
                            meta = "HTTP " + code + " (login?)";
                        }
                        c.disconnect();
                    } catch (Exception e) { meta = "Offline"; }
                }
                final String f1 = cpu, f2 = ram, f3 = disk, f4 = meta;
                new Handler(Looper.getMainLooper()).post(new Runnable() {
                    public void run() {
                        RemoteViews v = new RemoteViews(ctx.getPackageName(), R.layout.widget_perf);
                        v.setTextViewText(R.id.wp_cpu, f1);
                        v.setTextViewText(R.id.wp_ram, f2);
                        v.setTextViewText(R.id.wp_disk, f3);
                        v.setTextViewText(R.id.wp_meta, f4);
                        mgr.updateAppWidget(wid, v);
                    }
                });
            }
        }).start();
    }
}