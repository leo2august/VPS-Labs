package com.leo2agust.labs;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.widget.RemoteViews;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;

public class LabsWidgetProvider extends AppWidgetProvider {

    @Override
    public void onUpdate(Context context, AppWidgetManager appWidgetManager, int[] appWidgetIds) {
        for (int id : appWidgetIds) {
            updateWidget(context, appWidgetManager, id);
        }
    }

    static void updateWidget(Context context, AppWidgetManager manager, int widgetId) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_labs);
        views.setTextViewText(R.id.widget_title, "Labs");
        views.setTextViewText(R.id.widget_status, "Memeriksa…");
        views.setInt(R.id.widget_dot, "setColorFilter", 0xFFd97706);

        Intent open = new Intent(context, MainActivity.class);
        PendingIntent pi = PendingIntent.getActivity(context, 0, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        views.setOnClickPendingIntent(R.id.widget_root, pi);

        manager.updateAppWidget(widgetId, views);
        checkHealth(context, manager, widgetId);
    }

    static void checkHealth(final Context context, final AppWidgetManager manager, final int widgetId) {
        new Thread(new Runnable() {
            public void run() {
                String result = "Offline";
                int color = 0xFFdc2626;
                try {
                    // baca URL dari prefs (bukan hardcode) — kalau kosong, jangan cek
                    String base = context.getSharedPreferences("labs_prefs", Context.MODE_PRIVATE)
                            .getString("labs_url", "");
                    if (base == null || base.trim().isEmpty()) {
                        result = "Belum diatur";
                        color = 0xFFd97706;
                        final String res0 = result;
                        final int col0 = color;
                        new Handler(Looper.getMainLooper()).post(new Runnable() {
                            public void run() {
                                RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_labs);
                                views.setTextViewText(R.id.widget_status, res0);
                                views.setInt(R.id.widget_dot, "setColorFilter", col0);
                                manager.updateAppWidget(widgetId, views);
                            }
                        });
                        return;
                    }
                    URL u = new URL(base + "/health");
                    HttpURLConnection c = (HttpURLConnection) u.openConnection();
                    c.setConnectTimeout(8000);
                    c.setReadTimeout(8000);
                    int code = c.getResponseCode();
                    if (code == 200) {
                        BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream()));
                        StringBuilder sb = new StringBuilder();
                        String l;
                        while ((l = r.readLine()) != null) sb.append(l);
                        String body = sb.toString();
                        if (body.contains("\"ok\":true") || body.contains("true")) {
                            result = "Online";
                            color = 0xFF16a34a;
                        } else {
                            result = "Degraded";
                            color = 0xFFd97706;
                        }
                    } else {
                        result = "HTTP " + code;
                        color = 0xFFd97706;
                    }
                    c.disconnect();
                } catch (Exception e) {
                    result = "Offline";
                    color = 0xFFdc2626;
                }
                final String res = result;
                final int col = color;
                new Handler(Looper.getMainLooper()).post(new Runnable() {
                    public void run() {
                        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_labs);
                        views.setTextViewText(R.id.widget_status, res);
                        views.setInt(R.id.widget_dot, "setColorFilter", col);
                        manager.updateAppWidget(widgetId, views);
                    }
                });
            }
        }).start();
    }
}
