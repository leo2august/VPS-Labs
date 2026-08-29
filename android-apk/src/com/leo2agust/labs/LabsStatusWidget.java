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

public class LabsStatusWidget extends AppWidgetProvider {
    @Override
    public void onUpdate(Context context, AppWidgetManager mgr, int[] ids) {
        for (int id : ids) update(context, mgr, id);
    }
    static void update(Context ctx, AppWidgetManager mgr, int wid) {
        RemoteViews v = new RemoteViews(ctx.getPackageName(), R.layout.widget_status);
        v.setTextViewText(R.id.ws_title, "Labs");
        v.setTextViewText(R.id.ws_status, "Memeriksa…");
        v.setTextColor(R.id.ws_dot, 0xFFd97706);
        v.setOnClickPendingIntent(R.id.ws_root, PendingIntent.getActivity(ctx, 0,
            new Intent(ctx, MainActivity.class),
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
        mgr.updateAppWidget(wid, v);
        check(ctx, mgr, wid);
    }
    static void check(final Context ctx, final AppWidgetManager mgr, final int wid) {
        new Thread(new Runnable() {
            public void run() {
                String res = "Offline"; int col = 0xFFdc2626;
                String base = ctx.getSharedPreferences("labs_prefs", Context.MODE_PRIVATE).getString("labs_url", "");
                if (base.isEmpty()) { res = "Belum diatur"; col = 0xFFd97706; }
                else {
                    try {
                        URL u = new URL(base + "/health");
                        HttpURLConnection c = (HttpURLConnection) u.openConnection();
                        c.setConnectTimeout(8000); c.setReadTimeout(8000);
                        int code = c.getResponseCode();
                        BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream()));
                        StringBuilder sb = new StringBuilder(); String l;
                        while ((l = r.readLine()) != null) sb.append(l);
                        String body = sb.toString();
                        if (code == 200 && (body.contains("\"ok\":true") || body.contains("true"))) {
                            res = "Online"; col = 0xFF16a34a;
                        } else { res = "Degraded"; col = 0xFFd97706; }
                        c.disconnect();
                    } catch (Exception e) { res = "Offline"; col = 0xFFdc2626; }
                }
                final String fr = res; final int fc = col;
                new Handler(Looper.getMainLooper()).post(new Runnable() {
                    public void run() {
                        RemoteViews v = new RemoteViews(ctx.getPackageName(), R.layout.widget_status);
                        v.setTextViewText(R.id.ws_status, fr);
                        v.setTextColor(R.id.ws_dot, fc);
                        mgr.updateAppWidget(wid, v);
                    }
                });
            }
        }).start();
    }
}