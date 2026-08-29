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
import android.widget.LinearLayout;
import android.widget.Button;
import android.graphics.Color;
import android.view.Gravity;
import android.view.ViewGroup;
import android.content.res.Configuration;
import android.content.SharedPreferences;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.view.KeyEvent;
import java.util.HashMap;
import java.util.Map;
import java.util.ArrayList;
import java.util.List;

public class MainActivity extends Activity {

    private WebView webView;
    private LinearLayout bottomNav;
    private Map<String, Button> navButtons = new HashMap<>();
    private SharedPreferences prefs;
    static final String LABS_URL = "https://labs.leo2agust.my.id";
    static final String[][] NAV_ITEMS = {
        {"overview", "Overview", "🏠"},
        {"performance", "Perf", "📈"},
        {"services", "Services", "🛠"},
        {"chat", "Chat", "💬"},
        {"config", "Config", "⚙"},
    };
    private static final String[] TAB_KEYS = {"tab_overview","tab_performance","tab_services","tab_chat","tab_config"};

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

        // WebView
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
        ws.setAllowFileAccess(false);
        ws.setAllowContentAccess(false);
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
            }
        });

        webView.setWebChromeClient(new WebChromeClient());

        // JS interface: web -> native (page change from sidebar)
        webView.addJavascriptInterface(new Object() {
            @JavascriptInterface
            public void onPageChange(final String page) {
                runOnUiThread(new Runnable() { public void run() { updateNavActive(page); } });
            }
        }, "LabsBridge");

        // Bottom navigation bar (native) — tab diambil dari prefs
        bottomNav = new LinearLayout(this);
        bottomNav.setOrientation(LinearLayout.HORIZONTAL);
        bottomNav.setLayoutParams(new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dpToPx(58)));
        bottomNav.setBackgroundColor(Color.parseColor("#17243d"));
        bottomNav.setPadding(dpToPx(2), dpToPx(2), dpToPx(2), dpToPx(2));

        buildBottomNav();

        root.addView(webView);
        root.addView(bottomNav);
        setContentView(root);

        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState);
        } else {
            webView.loadUrl(baseUrl);
        }
    }

    private void buildBottomNav() {
        navButtons.clear();
        bottomNav.removeAllViews();
        List<String[]> visible = new ArrayList<>();
        for (int i = 0; i < NAV_ITEMS.length; i++) {
            if (prefs.getBoolean(TAB_KEYS[i], true)) {
                visible.add(NAV_ITEMS[i]);
            }
        }
        // Always add Settings gear as last tab
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
        if (!page.equals("__settings")) {
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
            // Settings berubah (URL / tab) → rebuild nav & reload URL
            buildBottomNav();
            String baseUrl = prefs.getString("labs_url", LABS_URL);
            String cur = webView.getUrl();
            if (cur == null || !cur.startsWith(baseUrl)) {
                webView.loadUrl(baseUrl);
            } else {
                webView.reload();
            }
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
