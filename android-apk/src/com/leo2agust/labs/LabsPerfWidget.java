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

public class LabsPerfWidget extends AppWidgetProvider {
    @Override public void onUpdate(Context context, AppWidgetManager manager, int[] ids) {
        for (int id : ids) update(context, manager, id);
    }

    static void update(final Context context, final AppWidgetManager manager, final int id) {
        RemoteViews loading = views(context, "—", "—", "—", "Menghubungkan server…");
        loading.setOnClickPendingIntent(R.id.wp_root, openApp(context, 21));
        manager.updateAppWidget(id, loading);
        new Thread(new Runnable() {
            public void run() {
                String cpu = "—", ram = "—", disk = "—", meta;
                try {
                    JSONObject data = WidgetHttp.get(context, "/api/overview");
                    cpu = WidgetHttp.percent(data.optDouble("cpu", 0)) + "%";
                    ram = WidgetHttp.percent(data.optDouble("memory", 0)) + "%";
                    disk = WidgetHttp.percent(data.optDouble("disk", 0)) + "%";
                    int processes = data.optInt("processes", 0);
                    meta = processes + " processes · tap untuk detail";
                } catch (Exception error) {
                    meta = WidgetHttp.errorText(error);
                }
                final RemoteViews result = views(context, cpu, ram, disk, meta);
                result.setOnClickPendingIntent(R.id.wp_root, openApp(context, 21));
                new Handler(Looper.getMainLooper()).post(new Runnable() {
                    public void run() { manager.updateAppWidget(id, result); }
                });
            }
        }).start();
    }

    private static RemoteViews views(Context context, String cpu, String ram, String disk, String meta) {
        RemoteViews view = new RemoteViews(context.getPackageName(), R.layout.widget_perf);
        view.setTextViewText(R.id.wp_title, WidgetHttp.brand(context));
        view.setTextViewText(R.id.wp_cpu, cpu);
        view.setTextViewText(R.id.wp_ram, ram);
        view.setTextViewText(R.id.wp_disk, disk);
        view.setTextViewText(R.id.wp_meta, meta);
        return view;
    }

    private static PendingIntent openApp(Context context, int request) {
        return PendingIntent.getActivity(context, request, new Intent(context, MainActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }
}
