package com.leo2agust.labs;

import android.app.Activity;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.EditText;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.ScrollView;
import android.graphics.Color;
import android.graphics.Typeface;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import android.graphics.drawable.GradientDrawable;

public class SettingsActivity extends Activity {

    private SharedPreferences prefs;
    private EditText urlInput;
    private EditText brandInput;
    private CheckBox[] tabChecks = new CheckBox[MainActivity.NAV_ITEMS.length];
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
        root.setPadding(dp(20), dp(24), dp(20), dp(20));

        // Title
        TextView title = new TextView(this);
        title.setText("⚙️ Labs Settings");
        title.setTextSize(22);
        title.setTypeface(null, Typeface.BOLD);
        title.setTextColor(Color.parseColor("#17243d"));
        title.setPadding(0, 0, 0, dp(6));
        root.addView(title);

        TextView sub = new TextView(this);
        sub.setText("Ubah URL server & navigasi bawah.\nIsi alamat server Labs kamu sendiri.");
        sub.setTextSize(12);
        sub.setTextColor(Color.parseColor("#8896a8"));
        sub.setPadding(0, 0, 0, dp(18));
        root.addView(sub);

        // Section: Server URL
        root.addView(sectionLabel("SERVER LABS"));
        urlInput = new EditText(this);
        urlInput.setText(prefs.getString("labs_url", ""));
        urlInput.setTextSize(14);
        urlInput.setSingleLine(true);
        urlInput.setHint("https://labs.domain-kamu.com");
        urlInput.setPadding(dp(12), dp(10), dp(12), dp(10));
        urlInput.setBackground(rounded(Color.parseColor("#ffffff"), Color.parseColor("#d8d2c5")));
        root.addView(urlInput);

        // Auto-detect brand dari URL: labs.xxx.domain → xxx
        urlInput.addTextChangedListener(new android.text.TextWatcher() {
            public void beforeTextChanged(CharSequence s, int a, int b, int c) {}
            public void onTextChanged(CharSequence s, int a, int b, int c) {
                String t = s.toString().trim();
                String detected = detectBrandFromUrl(t);
                if (!detected.isEmpty()) {
                    brandInput.setText(detected);
                }
            }
            public void afterTextChanged(android.text.Editable s) {}
        });

        TextView urlHelp = new TextView(this);
        urlHelp.setText("Wajib diisi — alamat server Labs kamu sendiri. Jangan pakai URL orang lain.");
        urlHelp.setTextSize(11);
        urlHelp.setTextColor(Color.parseColor("#8896a8"));
        urlHelp.setPadding(0, dp(6), 0, dp(16));
        root.addView(urlHelp);

        // Section: Nama Branding
        root.addView(sectionLabel("NAMA BRANDING (OTOMATIS)"));
        brandInput = new EditText(this);
        brandInput.setText(prefs.getString("brand_name", ""));
        brandInput.setTextSize(14);
        brandInput.setSingleLine(true);
        brandInput.setHint("leo2agust");
        brandInput.setPadding(dp(12), dp(10), dp(12), dp(10));
        brandInput.setBackground(rounded(Color.parseColor("#ffffff"), Color.parseColor("#d8d2c5")));
        root.addView(brandInput);
        TextView brandHelp = new TextView(this);
        brandHelp.setText("Otomatis terisi dari URL (labs.NAMA.domain → NAMA). Bisa diedit manual.");
        brandHelp.setTextSize(11);
        brandHelp.setTextColor(Color.parseColor("#8896a8"));
        brandHelp.setPadding(0, dp(6), 0, dp(16));
        root.addView(brandHelp);

        // Section: Bottom nav
        root.addView(sectionLabel("NAVIGASI BAWAH (PILIH TAB)"));
        for (int i = 0; i < MainActivity.NAV_ITEMS.length; i++) {
            CheckBox cb = new CheckBox(this);
            cb.setText(MainActivity.NAV_ITEMS[i][1] + " — " + MainActivity.NAV_ITEMS[i][2]);
            cb.setTextSize(14);
            cb.setTextColor(Color.parseColor("#17243d"));
            cb.setChecked(prefs.getBoolean(TAB_KEYS[i], true));
            cb.setPadding(dp(4), dp(4), 0, dp(4));
            tabChecks[i] = cb;
            root.addView(cb);
        }

        TextView navHelp = new TextView(this);
        navHelp.setText("Tabs yang dicentang akan tampil di navigasi bawah app.");
        navHelp.setTextSize(11);
        navHelp.setTextColor(Color.parseColor("#8896a8"));
        navHelp.setPadding(0, dp(6), 0, dp(18));
        root.addView(navHelp);

        // Save button
        Button save = new Button(this);
        save.setText("💾 Simpan");
        save.setTextSize(15);
        save.setTextColor(Color.WHITE);
        save.setBackground(rounded(Color.parseColor("#dc6268"), Color.parseColor("#dc6268")));
        save.setPadding(dp(10), dp(12), dp(10), dp(12));
        save.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) { saveAndClose(); }
        });
        root.addView(save);

        // Version
        TextView ver = new TextView(this);
        try {
            ver.setText("v" + getPackageManager().getPackageInfo(getPackageName(), 0).versionName);
        } catch (Exception e) {
            ver.setText("v1.0.1");
        }
        ver.setTextSize(10);
        ver.setTextColor(Color.parseColor("#b5ad9d"));
        ver.setGravity(android.view.Gravity.CENTER);
        ver.setPadding(0, dp(16), 0, 0);
        root.addView(ver);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(root);
        setContentView(scroll);
    }

    private TextView sectionLabel(String t) {
        TextView tv = new TextView(this);
        tv.setText(t);
        tv.setTextSize(11);
        tv.setTypeface(null, Typeface.BOLD);
        tv.setTextColor(Color.parseColor("#dc6268"));
        tv.setLetterSpacing(0.06f);
        tv.setPadding(0, dp(4), 0, dp(8));
        return tv;
    }

    private GradientDrawable rounded(int fill, int stroke) {
        GradientDrawable g = new GradientDrawable();
        g.setShape(GradientDrawable.RECTANGLE);
        g.setCornerRadius(dp(10));
        g.setColor(fill);
        if (stroke != fill) g.setStroke(dp(1), stroke);
        return g;
    }

    private void saveAndClose() {
        String url = urlInput.getText().toString().trim();
        if (url.isEmpty()) {
            url = MainActivity.LABS_URL;
        }
        if (!url.startsWith("http")) url = "https://" + url;
        String brand = brandInput.getText().toString().trim();
        if (brand.isEmpty()) {
            brand = detectBrandFromUrl(url);
        }
        SharedPreferences.Editor e = prefs.edit();
        e.putString("labs_url", url);
        if (!brand.isEmpty()) e.putString("brand_name", brand);
        for (int i = 0; i < tabChecks.length; i++) {
            e.putBoolean(TAB_KEYS[i], tabChecks[i].isChecked());
        }
        e.apply();
        setResult(RESULT_OK);
        finish();
    }

    // Deteksi brand dari URL: https://labs.NAMA.domain → NAMA; https://NAMA.domain → NAMA
    private String detectBrandFromUrl(String url) {
        try {
            String u = url.trim();
            if (!u.startsWith("http")) u = "https://" + u;
            String host = new java.net.URL(u).getHost();
            String[] parts = host.split("\\.");
            if (parts.length >= 3 && parts[0].equals("labs")) {
                return parts[1];
            } else if (parts.length >= 2) {
                return parts[0];
            }
        } catch (Exception e) {
            // abaikan
        }
        return "";
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density);
    }
}
