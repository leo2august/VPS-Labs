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
import org.json.JSONArray;
import org.json.JSONObject;

public class LabsLogWidget extends AppWidgetProvider {
    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        for (int id : ids) update(context, mgr, id);
    }
    static void update(Context ctx, AppWidgetManager mgr, int wid) {
        RemoteViews v = new RemoteViews(ctx.getPackageName(), R.layout.widget_log);
        v.setTextViewText(R.id.wl_title, "Labs · Logs");
        v.setOnClickPendingIntent(R.id.wl_root, PendingIntent.getActivity(ctx, 2,
            new Intent(ctx, MainActivity.class),
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
        mgr.updateAppWidget(wid, v);
        check(ctx, mgr, wid);
    }
    static void check(final Context ctx, final AppWidgetManager mgr, final int wid) {
        new Thread(new Runnable() {
            public void run() {
                String l1 = "—", l2 = "—", l3 = "—";
                String base = ctx.getSharedPreferences("labs_prefs", Context.MODE_PRIVATE).getString("labs_url", "");
                if (base.isEmpty()) { l1 = "URL belum diatur"; }
                else {
                    try {
                        URL u = new URL(base + "/api/overview");
                        HttpURLConnection c = (HttpURLConnection) u.openConnection();
                        c.setConnectTimeout(8000); c.setReadTimeout(8000);
                        c.setRequestProperty("Accept", "application/json");
                        int code = c.getResponseCode();
                        if (code == 200) {
                            BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream()));
                            StringBuilder sb = new StringBuilder(); String ln;
                            while ((ln = r.readLine()) != null) sb.append(ln);
                            JSONObject d = new JSONObject(sb.toString());
                            // layanan: ambil nama + state teratas
                            JSONArray svc = d.optJSONArray("services");
                            if (svc != null && svc.length() > 0) {
                                StringBuilder parts = new StringBuilder();
                                for (int i = 0; i < svc.length() && i < 3; i++) {
                                    JSONObject s = svc.optJSONObject(i);
                                    if (s != null) {
                                        String name = s.optString("name", "");
                                        String state = s.optString("state", "");
                                        if (!name.isEmpty()) parts.append(name).append(": ").append(state).append(" · ");
                                    }
                                }
                                String all = parts.toString();
                                String[] lines = all.split(" · ");
                                if (lines.length > 0) l1 = lines[0];
                                if (lines.length > 1) l2 = lines[1];
                                if (lines.length > 2) l3 = lines[2];
                            }
                        } else {
                            l1 = "HTTP " + code + " (login?)";
                        }
                        c.disconnect();
                    } catch (Exception e) { l1 = "Offline"; }
                }
                final String f1 = l1, f2 = l2, f3 = l3;
                new Handler(Looper.getMainLooper()).post(new Runnable() {
                    public void run() {
                        RemoteViews v = new RemoteViews(ctx.getPackageName(), R.layout.widget_log);
                        v.setTextViewText(R.id.wl_line1, f1);
                        v.setTextViewText(R.id.wl_line2, f2);
                        v.setTextViewText(R.id.wl_line3, f3);
                        mgr.updateAppWidget(wid, v);
                    }
                });
            }
        }).start();
    }
}