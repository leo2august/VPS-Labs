package com.leo2agust.labs;

import android.app.Activity;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Button;
import android.widget.ScrollView;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Build;
import android.content.Intent;
import android.net.Uri;

public class InstallationActivity extends Activity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            getWindow().setStatusBarColor(Color.parseColor("#0f1a2c"));
            getWindow().setNavigationBarColor(Color.parseColor("#17243d"));
        }

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setLayoutParams(new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        root.setBackgroundColor(Color.parseColor("#f5f1e9"));
        root.setPadding(dp(20), dp(24), dp(20), dp(24));

        TextView title = new TextView(this);
        title.setText("📲 Installasi Labs");
        title.setTextSize(22);
        title.setTypeface(null, Typeface.BOLD);
        title.setTextColor(Color.parseColor("#17243d"));
        root.addView(title);

        TextView sub = new TextView(this);
        sub.setText("Labs adalah dashboard VPS yang berjalan di server kamu sendiri. Ada 2 cara install.");
        sub.setTextSize(12);
        sub.setTextColor(Color.parseColor("#8896a8"));
        sub.setPadding(0, dp(6), 0, dp(18));
        root.addView(sub);

        // Opsi A
        root.addView(stepTitle("CARA A — Instal lewat VPS (disarankan)"));
        root.addView(stepText("1. Siapkan VPS Ubuntu 22.04+ (min. 1 GB RAM)."));
        root.addView(stepText("2. SSH ke server kamu:"));
        root.addView(code("ssh user@IP_VPS"));
        root.addView(stepText("3. Clone template Labs:"));
        root.addView(code("git clone https://github.com/leo2august/VPS-Labs.git\ncd VPS-Labs"));
        root.addView(stepText("4. Jalankan installer:"));
        root.addView(code("sudo bash install.sh"));
        root.addView(stepText("5. Ikuti prompt: isi nama branding, port, dan password admin."));
        root.addView(stepText("6. Setelah selesai, buka https://DOMAIN_ATAU_IP:9118 dan login."));

        // Opsi B
        root.addView(stepTitle("CARA B — Pakai Agen / AI Assistant"));
        root.addView(stepText("1. Berikan instruksi ini ke asisten AI kamu (mis. Hermes):"));
        root.addView(code("Instal VPS Labs di server ini:\n" +
                "1. git clone https://github.com/leo2august/VPS-Labs.git ~/labs\n" +
                "2. cd ~/labs " + "&&" + " bash install.sh\n" +
                "3. Isi brand name, sub-judul, port, dan password admin\n" +
                "4. Pastikan service labs aktif dan lapor URL loginnya"));
        root.addView(stepText("2. Setelah instal, ubah URL di Settings app ini ke server kamu."));

        // Note
        root.addView(stepTitle("Setelah instal"));
        root.addView(stepText("• Buka Settings app ini (⚙️) → ganti URL server ke domain/IP kamu."));
        root.addView(stepText("• Default repo adalah milik Leo2agust — fork dulu jika ingin pakai branding sendiri."));

        Button open = new Button(this);
        open.setText("🌐 Buka Repo GitHub");
        open.setTextSize(14);
        open.setTextColor(Color.WHITE);
        open.setBackgroundColor(Color.parseColor("#dc6268"));
        open.setPadding(dp(10), dp(14), dp(10), dp(14));
        open.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/leo2august/VPS-Labs")));
                } catch (Exception ignored) {}
            }
        });
        root.addView(open);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(root);
        setContentView(scroll);
    }

    private TextView stepTitle(String t) {
        TextView tv = new TextView(this);
        tv.setText(t);
        tv.setTextSize(14);
        tv.setTypeface(null, Typeface.BOLD);
        tv.setTextColor(Color.parseColor("#dc6268"));
        tv.setPadding(0, dp(14), 0, dp(6));
        return tv;
    }

    private TextView stepText(String t) {
        TextView tv = new TextView(this);
        tv.setText(t);
        tv.setTextSize(13);
        tv.setTextColor(Color.parseColor("#33415c"));
        tv.setPadding(0, dp(3), 0, dp(3));
        return tv;
    }

    private TextView code(String t) {
        TextView tv = new TextView(this);
        tv.setText(t);
        tv.setTextSize(11.5f);
        tv.setTypeface(Typeface.MONOSPACE);
        tv.setTextColor(Color.parseColor("#eef5ff"));
        tv.setBackgroundColor(Color.parseColor("#17243d"));
        tv.setPadding(dp(12), dp(10), dp(12), dp(10));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, dp(6), 0, dp(6));
        tv.setLayoutParams(lp);
        return tv;
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density);
    }
}
