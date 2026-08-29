package com.leo2agust.labs;

import android.content.Context;
import android.webkit.CookieManager;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import org.json.JSONObject;

final class WidgetHttp {
    private WidgetHttp() {}

    static String base(Context context) {
        String value = context.getSharedPreferences("labs_prefs", Context.MODE_PRIVATE)
                .getString("labs_url", "").trim();
        while (value.endsWith("/")) value = value.substring(0, value.length() - 1);
        return value;
    }

    static String brand(Context context) {
        String value = context.getSharedPreferences("labs_prefs", Context.MODE_PRIVATE)
                .getString("brand_name", "").trim();
        return value.isEmpty() ? "Labs" : value;
    }

    static JSONObject get(Context context, String path) throws Exception {
        String base = base(context);
        if (base.isEmpty()) throw new IllegalStateException("SETUP");
        String url = base + path;
        HttpURLConnection connection = (HttpURLConnection) new URL(url).openConnection();
        connection.setConnectTimeout(8000);
        connection.setReadTimeout(8000);
        connection.setRequestProperty("Accept", "application/json");
        connection.setRequestProperty("User-Agent", "LabsWidget/1.0");
        String cookie = CookieManager.getInstance().getCookie(base);
        if (cookie != null && !cookie.trim().isEmpty()) {
            connection.setRequestProperty("Cookie", cookie);
        }
        int code = connection.getResponseCode();
        InputStream stream = code >= 200 && code < 300
                ? connection.getInputStream() : connection.getErrorStream();
        String body = read(stream);
        connection.disconnect();
        if (code == 401 || code == 403) throw new SecurityException("LOGIN");
        if (code < 200 || code >= 300) throw new Exception("HTTP " + code);
        return new JSONObject(body);
    }

    static String errorText(Exception error) {
        if (error instanceof IllegalStateException) return "Atur server di Settings";
        if (error instanceof SecurityException) return "Buka app lalu login";
        return "Server tidak terjangkau";
    }

    static int percent(double value) {
        return (int) Math.max(0, Math.min(100, Math.round(value)));
    }

    private static String read(InputStream stream) throws Exception {
        if (stream == null) return "{}";
        BufferedReader reader = new BufferedReader(new InputStreamReader(stream, "UTF-8"));
        StringBuilder result = new StringBuilder();
        String line;
        while ((line = reader.readLine()) != null) result.append(line);
        reader.close();
        return result.toString();
    }
}
