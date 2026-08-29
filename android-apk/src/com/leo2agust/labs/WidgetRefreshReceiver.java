package com.leo2agust.labs;

import android.appwidget.AppWidgetManager;
import android.content.BroadcastReceiver;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;

public class WidgetRefreshReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        AppWidgetManager manager = AppWidgetManager.getInstance(context);
        int[] ids = manager.getAppWidgetIds(new ComponentName(context, LabsStatusWidget.class));
        for (int id : ids) LabsStatusWidget.update(context, manager, id);
        ids = manager.getAppWidgetIds(new ComponentName(context, LabsPerfWidget.class));
        for (int id : ids) LabsPerfWidget.update(context, manager, id);
        ids = manager.getAppWidgetIds(new ComponentName(context, LabsLogWidget.class));
        for (int id : ids) LabsLogWidget.update(context, manager, id);
        ids = manager.getAppWidgetIds(new ComponentName(context, LabsStorageWidget.class));
        for (int id : ids) LabsStorageWidget.update(context, manager, id);
    }
}
