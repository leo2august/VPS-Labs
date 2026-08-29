package com.leo2agust.labs;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;
import android.widget.RemoteViews;
import org.json.JSONArray;
import org.json.JSONObject;

public class LabsLogWidget extends AppWidgetProvider {
    @Override public void onUpdate(Context context, AppWidgetManager manager, int[] ids) {
        for (int id : ids) update(context, manager, id);
    }

    static void update(final Context context, final AppWidgetManager manager, final int id) {
        RemoteViews loading = views(context, "Mengambil aktivitas…", "", "", "LIVE");
        loading.setOnClickPendingIntent(R.id.wl_root, openApp(context));
        manager.updateAppWidget(id, loading);
        new Thread(new Runnable() {
            public void run() {
                String line1 = "—", line2 = "—", line3 = "—", badge = "LOGS";
                try {
                    JSONObject data = WidgetHttp.get(context, "/api/lab/activity?limit=3");
                    JSONArray events = data.optJSONArray("events");
                    badge = data.optInt("active_sessions", 0) + " ACTIVE";
                    if (events != null && events.length() > 0) line1 = summary(events.optJSONObject(0));
                    if (events != null && events.length() > 1) line2 = summary(events.optJSONObject(1));
                    if (events != null && events.length() > 2) line3 = summary(events.optJSONObject(2));
                } catch (Exception error) {
                    line1 = WidgetHttp.errorText(error);
                    line2 = "Tap widget untuk membuka Labs";
                    line3 = "";
                    badge = "PAUSED";
                }
                final RemoteViews result = views(context, line1, line2, line3, badge);
                result.setOnClickPendingIntent(R.id.wl_root, openApp(context));
                new Handler(Looper.getMainLooper()).post(new Runnable() {
                    public void run() { manager.updateAppWidget(id, result); }
                });
            }
        }).start();
    }

    private static String summary(JSONObject event) {
        if (event == null) return "—";
        String phase = event.optString("phase", "event").toUpperCase();
        String text = event.optString("summary", "Aktivitas Labs");
        if (text.length() > 54) text = text.substring(0, 51) + "…";
        return phase + "  ·  " + text;
    }

    private static RemoteViews views(Context context, String a, String b, String c, String badge) {
        RemoteViews view = new RemoteViews(context.getPackageName(), R.layout.widget_log);
        view.setTextViewText(R.id.wl_title, WidgetHttp.brand(context) + " · Process Logs");
        view.setTextViewText(R.id.wl_badge, badge);
        view.setTextViewText(R.id.wl_line1, a);
        view.setTextViewText(R.id.wl_line2, b);
        view.setTextViewText(R.id.wl_line3, c);
        return view;
    }

    private static PendingIntent openApp(Context context) {
        return PendingIntent.getActivity(context, 22, new Intent(context, MainActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }
}
