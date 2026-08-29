package com.leo2agust.labs;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;
import android.widget.RemoteViews;
import org.json.JSONObject;

public class LabsStorageWidget extends AppWidgetProvider {
    @Override public void onEnabled(Context context) { WidgetHttp.scheduleAutoRefresh(context); }
    @Override public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if ("com.leo2agust.labs.REFRESH_WIDGET".equals(intent.getAction())) {
            int id = intent.getIntExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID);
            if (id != AppWidgetManager.INVALID_APPWIDGET_ID) update(context, AppWidgetManager.getInstance(context), id);
        }
    }
    @Override public void onUpdate(Context context, AppWidgetManager manager, int[] ids) {
        for (int id : ids) update(context, manager, id);
    }

    static void update(final Context context, final AppWidgetManager manager, final int id) {
        RemoteViews loading = views(context, "—", "—", "—", "Memuat kapasitas…");
        loading.setOnClickPendingIntent(R.id.wd_root, openApp(context));
        loading.setOnClickPendingIntent(R.id.wd_refresh, WidgetHttp.refresh(context, LabsStorageWidget.class, id, 2300));
        manager.updateAppWidget(id, loading);
        new Thread(new Runnable() {
            public void run() {
                String disk = "—", used = "—", free = "—", meta;
                try {
                    JSONObject data = WidgetHttp.get(context, "/api/overview");
                    disk = WidgetHttp.percent(data.optDouble("disk", 0)) + "%";
                    long total = data.optLong("disk_total", 0);
                    long usedBytes = data.optLong("disk_used", 0);
                    used = size(usedBytes);
                    free = size(Math.max(0, total - usedBytes));
                    meta = WidgetHttp.updatedNow();
                } catch (Exception error) {
                    meta = WidgetHttp.errorText(error);
                }
                final RemoteViews result = views(context, disk, used, free, meta);
                result.setOnClickPendingIntent(R.id.wd_root, openApp(context));
                result.setOnClickPendingIntent(R.id.wd_refresh, WidgetHttp.refresh(context, LabsStorageWidget.class, id, 2300));
                new Handler(Looper.getMainLooper()).post(new Runnable() {
                    public void run() { manager.updateAppWidget(id, result); }
                });
            }
        }).start();
    }

    private static RemoteViews views(Context context, String disk, String used, String free, String meta) {
        RemoteViews view = new RemoteViews(context.getPackageName(), R.layout.widget_storage);
        view.setTextViewText(R.id.wd_title, WidgetHttp.brand(context));
        view.setTextViewText(R.id.wd_percent, disk);
        view.setTextViewText(R.id.wd_used, used);
        view.setTextViewText(R.id.wd_free, free);
        view.setTextViewText(R.id.wd_meta, meta);
        return view;
    }

    private static String size(long bytes) {
        if (bytes <= 0) return "—";
        double gb = bytes / 1073741824.0;
        return (Math.round(gb * 10) / 10.0) + " GB";
    }

    private static PendingIntent openApp(Context context) {
        return PendingIntent.getActivity(context, 23, new Intent(context, MainActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }
}
