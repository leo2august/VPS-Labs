package com.leo2agust.labs;

import android.app.Activity;
import android.os.Bundle;
import android.view.View;
import android.webkit.WebChromeClient;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.CookieManager;
import android.webkit.WebSettings;
import android.webkit.JavascriptInterface;
import android.webkit.DownloadListener;
import android.widget.LinearLayout;
import android.widget.Button;
import android.graphics.Color;
import android.view.Gravity;
import android.view.ViewGroup;
import android.content.res.Configuration;
import android.content.SharedPreferences;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.Manifest;
import android.content.pm.PackageManager;
import android.webkit.URLUtil;
import android.widget.Toast;
import android.app.DownloadManager;
import android.net.Uri;
import java.io.File;
import java.util.HashMap;
import java.util.Map;
import java.util.ArrayList;
import java.util.List;

public class MainActivity extends Activity {

    private WebView webView;
    private LinearLayout bottomNav;
    private Map<String, Button> navButtons = new HashMap<>();
    private SharedPreferences prefs;
    static final String LABS_URL = "";
    // page id, label, compact icon, default visible (maks. 5 + Settings)
    static final String[][] NAV_ITEMS = {
        {"overview", "Overview", "OV", "1"},
        {"performance", "Perf", "PF", "1"},
        {"services", "Services", "SV", "1"},
        {"chat", "Chat", "CH", "1"},
        {"attachments", "Files", "FL", "1"},
        {"activity", "Logs", "LG", "0"},
        {"security", "Security", "SC", "0"},
        {"storage", "Storage", "ST", "0"},
        {"usage", "Usage", "US", "0"},
        {"notifications", "Alerts", "NT", "0"},
        {"router", "Router", "RT", "0"},
        {"quota", "Quota", "QT", "0"},
        {"backup", "Backup", "BK", "0"},
        {"update", "Update", "UP", "0"},
        {"sessions", "Sessions", "SS", "0"},
        {"config", "Config", "CF", "0"}
    };
    private static final int REQ_STORAGE = 2001;
    private boolean isLoginNav = false; // track nav state untuk cegah kedip
    private long lastWidgetRefresh = 0;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences("labs_prefs", Context.MODE_PRIVATE);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            getWindow().setStatusBarColor(Color.parseColor("#0f1a2c"));
            getWindow().setNavigationBarColor(Color.parseColor("#17243d"));
        }

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setLayoutParams(new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        root.setBackgroundColor(Color.parseColor("#f5f1e9"));

        webView = new WebView(this);
        LinearLayout.LayoutParams wvParams = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f);
        webView.setLayoutParams(wvParams);
        webView.setOverScrollMode(WebView.OVER_SCROLL_NEVER);

        WebSettings ws = webView.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        ws.setDatabaseEnabled(true);
        ws.setCacheMode(WebSettings.LOAD_DEFAULT);
        ws.setLoadWithOverviewMode(true);
        ws.setUseWideViewPort(true);
        ws.setAllowFileAccess(true);
        ws.setAllowContentAccess(true);
        ws.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        ws.setUserAgentString(ws.getUserAgentString() + " LabsAPK/1.0");

        CookieManager.getInstance().setAcceptCookie(true);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true);
        }

        final String baseUrl = prefs.getString("labs_url", LABS_URL);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                if (url != null && url.startsWith(baseUrl)) {
                    return false;
                }
                return true;
            }
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                injectNavBridge();
                updateNavForUrl(url);
                injectBrand();
                if (url != null && !url.contains("/login")) {
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) CookieManager.getInstance().flush();
                    refreshWidgets();
                }
            }
        });

        webView.setWebChromeClient(new WebChromeClient());

        // Download attachments — simpan ke Download/Labs
        webView.setDownloadListener(new DownloadListener() {
            public void onDownloadStart(String url, String userAgent, String contentDisposition, String mimetype, long contentLength) {
                try {
                    String fileName = URLUtil.guessFileName(url, contentDisposition, mimetype);
                    requestStorageIfNeeded(url, userAgent, contentDisposition, mimetype, fileName);
                } catch (Exception e) {
                    Toast.makeText(MainActivity.this, "Download gagal: " + e.getMessage(), Toast.LENGTH_LONG).show();
                }
            }
        });

        webView.addJavascriptInterface(new Object() {
            @JavascriptInterface
            public void onPageChange(final String page) {
                runOnUiThread(new Runnable() { public void run() { updateNavActive(page); } });
            }
        }, "LabsBridge");

        bottomNav = new LinearLayout(this);
        bottomNav.setOrientation(LinearLayout.HORIZONTAL);
        bottomNav.setLayoutParams(new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dpToPx(58)));
        bottomNav.setBackgroundColor(Color.parseColor("#17243d"));
        bottomNav.setPadding(dpToPx(2), dpToPx(2), dpToPx(2), dpToPx(2));

        String initBase = prefs.getString("labs_url", LABS_URL);
        if (initBase == null || initBase.trim().isEmpty()) {
            isLoginNav = true;
            buildLoginNav();
        } else {
            buildBottomNav();
        }

        root.addView(webView);
        root.addView(bottomNav);
        setContentView(root);

        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState);
        } else {
            String base = prefs.getString("labs_url", LABS_URL);
            if (base == null || base.trim().isEmpty()) {
                showSetupPrompt();
            } else {
                webView.loadUrl(base);
            }
        }

        // Cek update dari GitHub Releases (setelah 3 detik, sekali saat app dibuka)
        new Handler(Looper.getMainLooper()).postDelayed(new Runnable() {
            public void run() {
                UpdateChecker.check(MainActivity.this, true, null);
            }
        }, 3000);
    }

    // Layar pertama kali: URL belum di-set → arahkan ke Settings
    private void showSetupPrompt() {
        android.app.AlertDialog.Builder b = new android.app.AlertDialog.Builder(this);
        b.setTitle("⚙️ Konfigurasi Server");
        b.setMessage("URL server Labs belum diatur.\n\n" +
                "Isi alamat server kamu (mis. https://labs.domain-kamu.com) di menu Settings (⚙️) terlebih dahulu.\n\n" +
                "Belum punya server? Buka tab Installation (📲) untuk panduan deploy Labs ke VPS kamu.");
        b.setPositiveButton("Buka Settings", new android.content.DialogInterface.OnClickListener() {
            public void onClick(android.content.DialogInterface d, int w) {
                Intent i = new Intent(MainActivity.this, SettingsActivity.class);
                startActivityForResult(i, 100);
            }
        });
        b.setNegativeButton("Panduan Instal", new android.content.DialogInterface.OnClickListener() {
            public void onClick(android.content.DialogInterface d, int w) {
                startActivity(new Intent(MainActivity.this, InstallationActivity.class));
            }
        });
        b.setCancelable(false);
        b.show();
    }

    private void requestStorageIfNeeded(final String url, final String ua, final String cd, final String mime, final String fileName) {
        boolean need = false;
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            need = checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE)
                    != PackageManager.PERMISSION_GRANTED;
        }
        if (need) {
            requestPermissions(new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQ_STORAGE);
            // simpan pending download utk dilanjutkan setelah grant
            pendingUrl = url; pendingUa = ua; pendingCd = cd; pendingMime = mime; pendingName = fileName;
        } else {
            doDownload(url, ua, cd, mime, fileName);
        }
    }

    private String pendingUrl, pendingUa, pendingCd, pendingMime, pendingName;

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQ_STORAGE) {
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED && pendingUrl != null) {
                doDownload(pendingUrl, pendingUa, pendingCd, pendingMime, pendingName);
                pendingUrl = null;
            } else {
                Toast.makeText(this, "Izin penyimpanan ditolak — download dibatalkan.", Toast.LENGTH_LONG).show();
            }
        }
    }

    private void doDownload(String url, String ua, String cd, String mime, String fileName) {
        try {
            File dir;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                dir = new File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "Labs");
            } else {
                dir = new File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS), "Labs");
            }
            if (!dir.exists()) dir.mkdirs();
            File out = new File(dir, fileName);
            DownloadManager.Request req = new DownloadManager.Request(Uri.parse(url));
            req.setTitle(fileName);
            req.setDescription("Labs attachment");
            req.setDestinationUri(Uri.fromFile(out));
            req.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            DownloadManager dm = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
            dm.enqueue(req);
            Toast.makeText(this, "Mengunduh ke Download/Labs/" + fileName, Toast.LENGTH_LONG).show();
        } catch (Exception e) {
            Toast.makeText(this, "Download gagal: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private void updateNavForUrl(String url) {
        boolean isLogin = url != null && url.contains("/login");
        // Hanya rebuild kalau state nav benar-benar berubah (cegah kedip)
        if (isLogin && !isLoginNav) {
            isLoginNav = true;
            runOnUiThread(new Runnable() { public void run() { buildLoginNav(); } });
        } else if (!isLogin && isLoginNav) {
            isLoginNav = false;
            runOnUiThread(new Runnable() { public void run() { buildBottomNav(); } });
        }
        // kalau state sama, jangan sentuh nav (tidak kedip)
    }

    private void buildLoginNav() {
        navButtons.clear();
        bottomNav.removeAllViews();
        Button login = makeNavButton("__login", "Login", "🔐");
        login.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                String base = prefs.getString("labs_url", LABS_URL);
                webView.loadUrl(base + "/login");
            }
        });
        bottomNav.addView(login);
        Button inst = makeNavButton("__install", "Install", "📲");
        inst.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                Intent i = new Intent(MainActivity.this, InstallationActivity.class);
                startActivity(i);
            }
        });
        bottomNav.addView(inst);
        Button gear = makeNavButton("__settings", "Set", "⚙️");
        gear.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                Intent i = new Intent(MainActivity.this, SettingsActivity.class);
                startActivityForResult(i, 100);
            }
        });
        bottomNav.addView(gear);
    }

    private void buildBottomNav() {
        navButtons.clear();
        bottomNav.removeAllViews();
        List<String[]> visible = new ArrayList<>();
        for (int i = 0; i < NAV_ITEMS.length; i++) {
            boolean defaultOn = "1".equals(NAV_ITEMS[i][3]);
            if (prefs.getBoolean("tab_" + NAV_ITEMS[i][0], defaultOn) && visible.size() < 5) {
                visible.add(NAV_ITEMS[i]);
            }
        }
        for (String[] item : visible) {
            Button btn = makeNavButton(item[0], item[1], item[2]);
            navButtons.put(item[0], btn);
            bottomNav.addView(btn);
        }
        Button gear = makeNavButton("__settings", "Set", "⚙️");
        gear.setTextColor(Color.parseColor("#8896a8"));
        gear.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                Intent i = new Intent(MainActivity.this, SettingsActivity.class);
                startActivityForResult(i, 100);
            }
        });
        bottomNav.addView(gear);
        updateNavActive("overview");
    }

    private Button makeNavButton(String page, String label, String icon) {
        Button btn = new Button(this);
        btn.setLayoutParams(new LinearLayout.LayoutParams(
            0, ViewGroup.LayoutParams.MATCH_PARENT, 1f));
        btn.setGravity(Gravity.CENTER);
        btn.setPadding(0, dpToPx(3), 0, dpToPx(3));
        btn.setText(icon + "\n" + label);
        btn.setTextSize(9);
        btn.setSingleLine(false);
        btn.setLines(2);
        btn.setAllCaps(false);
        btn.setBackgroundColor(Color.TRANSPARENT);
        btn.setTextColor(Color.parseColor("#8896a8"));
        btn.setTag(page);
        if (!page.equals("__settings") && !page.equals("__login") && !page.equals("__install")) {
            final String p = page;
            btn.setOnClickListener(new View.OnClickListener() {
                public void onClick(View v) { navigateTo(p); }
            });
        }
        return btn;
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == 100) {
            String base = prefs.getString("labs_url", LABS_URL);
            if (base == null || base.trim().isEmpty()) {
                buildLoginNav();
                showSetupPrompt();
            } else {
                buildBottomNav();
                // auto-detect brand dari URL
                autoDetectBrand(base);
                String cur = webView.getUrl();
                if (cur == null || !cur.startsWith(base)) {
                    webView.loadUrl(base);
                } else {
                    webView.reload();
                }
            }
        }
    }

    // Auto-detect brand dari URL: labs.xxx.domain → "xxx"
    private void autoDetectBrand(String url) {
        try {
            String host = new java.net.URL(url).getHost();
            String[] parts = host.split("\\.");
            String brand = "";
            if (parts.length >= 3 && parts[0].equals("labs")) {
                brand = parts[1];
            } else if (parts.length >= 2) {
                brand = parts[0];
            }
            if (brand.isEmpty()) return;
            // simpan ke lab-settings via API (butuh login) — skip dulu
            // simpan ke prefs lokal untuk referensi
            prefs.edit().putString("_detected_brand", brand).apply();
        } catch (Exception e) {
            // abaikan
        }
    }

    private void navigateTo(String page) {
        updateNavActive(page);
        String js = "javascript:(function(){"
            + "var b=document.querySelector('.nav button[data-page=\"" + page + "\"]');"
            + "if(b){b.click();return;}var menu=document.querySelector('.menu');"
            + "if(menu){menu.click();setTimeout(function(){"
            + "var b2=document.querySelector('.nav button[data-page=\"" + page + "\"]');if(b2)b2.click();"
            + "},250);}else{location.hash='#'+page;}"
            + "})()";
        webView.evaluateJavascript(js, null);
    }

    private void updateNavActive(String page) {
        for (Map.Entry<String, Button> entry : navButtons.entrySet()) {
            boolean active = entry.getKey().equals(page);
            entry.getValue().setTextColor(Color.parseColor(active ? "#dc6268" : "#8896a8"));
            entry.getValue().setAlpha(active ? 1f : 0.55f);
        }
    }

    private void injectNavBridge() {
        String js = "javascript:(function(){"
            + "if(window.__labsNavInjected)return;window.__labsNavInjected=true;"
            + "var nav=document.querySelector('.nav');if(!nav)return;"
            + "var obs=new MutationObserver(function(){"
            + "var a=nav.querySelector('.active');"
            + "if(a){var p=a.getAttribute('data-page');if(p&&window.LabsBridge)window.LabsBridge.onPageChange(p);}"
            + "});obs.observe(nav,{attributes:true,childList:true,subtree:true,attributeFilter:['class']});"
            + "})()";
        webView.evaluateJavascript(js, null);
    }

    // Terapkan brand terdeteksi ke halaman web (sidebar, title, brand name)
    private void injectBrand() {
        final String brand = prefs.getString("brand_name", "");
        if (brand == null || brand.isEmpty()) return;
        String js = "javascript:(function(){"
            + "var bn=" + jsonStr(brand) + ";"
            + "var b=document.querySelector('.brand b');if(b)b.textContent=bn;"
            + "var mb=document.querySelector('.mobile-brand');if(mb)mb.innerHTML='<span class=\"seal\">雲</span> '+bn;"
            + "if(document.title.indexOf('·')>=0)document.title=bn+' · '+document.title.split('·')[1].trim();"
            + "var saved={};try{saved=JSON.parse(localStorage.getItem('labs-settings')||'{}')}catch(e){}"
            + "saved.brand_name=bn;localStorage.setItem('labs-settings',JSON.stringify(saved));"
            + "})()";
        webView.evaluateJavascript(js, null);
    }

    private void refreshWidgets() {
        long now = System.currentTimeMillis();
        if (now - lastWidgetRefresh < 5000) return;
        lastWidgetRefresh = now;
        Class<?>[] providers = new Class<?>[]{LabsStatusWidget.class, LabsPerfWidget.class,
                LabsLogWidget.class, LabsStorageWidget.class};
        android.appwidget.AppWidgetManager manager = android.appwidget.AppWidgetManager.getInstance(this);
        for (Class<?> provider : providers) {
            android.content.ComponentName component = new android.content.ComponentName(this, provider);
            int[] ids = manager.getAppWidgetIds(component);
            if (ids.length == 0) continue;
            Intent intent = new Intent(android.appwidget.AppWidgetManager.ACTION_APPWIDGET_UPDATE);
            intent.setComponent(component);
            intent.putExtra(android.appwidget.AppWidgetManager.EXTRA_APPWIDGET_IDS, ids);
            sendBroadcast(intent);
        }
    }

    private static String jsonStr(String s) {
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n") + "\"";
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        webView.saveState(outState);
    }

    @Override
    protected void onRestoreInstanceState(Bundle savedInstanceState) {
        super.onRestoreInstanceState(savedInstanceState);
    }

    private int dpToPx(int dp) {
        return (int) (dp * getResources().getDisplayMetrics().density);
    }
}
