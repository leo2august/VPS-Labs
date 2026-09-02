package app.vpslabs.dashboard;

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
import android.widget.Toast;
import android.os.Handler;
import android.os.Looper;
import android.content.ClipboardManager;
import android.content.ClipData;
import java.util.ArrayList;
import java.util.List;

public class InstallationActivity extends Activity {

    private LinearLayout content;
    private List<TextView> codeViews = new ArrayList<>();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            getWindow().setStatusBarColor(Color.parseColor("#0f1a2c"));
            getWindow().setNavigationBarColor(Color.parseColor("#17243d"));
        }

        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setLayoutParams(new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        content.setBackgroundColor(Color.parseColor("#f5f1e9"));
        content.setPadding(dp(20), dp(24), dp(20), dp(24));

        TextView title = new TextView(this);
        title.setText("📲 Installasi Labs");
        title.setTextSize(22);
        title.setTypeface(null, Typeface.BOLD);
        title.setTextColor(Color.parseColor("#17243d"));
        content.addView(title);

        TextView sub = new TextView(this);
        sub.setText("Labs berjalan di VPS kamu. Akses bisa lewat domain HTTPS atau langsung IP VPS.");
        sub.setTextSize(12);
        sub.setTextColor(Color.parseColor("#8896a8"));
        sub.setPadding(0, dp(6), 0, dp(18));
        content.addView(sub);

        // Opsi A
        content.addView(stepTitle("CARA A — Instal lewat VPS (disarankan)"));
        content.addView(stepText("1. Siapkan VPS Ubuntu 22.04+ (min. 1 GB RAM)."));
        content.addView(stepText("2. SSH ke server kamu:"));
        content.addView(code("ssh user@IP_VPS"));
        content.addView(stepText("3. Clone template Labs:"));
        content.addView(code("git clone https://github.com/OWNER/VPS-Labs.git\ncd VPS-Labs"));
        content.addView(stepText("4. Jalankan installer:"));
        content.addView(code("sudo bash install.sh"));
        content.addView(stepText("5. Ikuti prompt: isi nama branding, port, dan password admin."));
        content.addView(stepText("6. Pilih jalur akses di bawah, lalu login."));

        content.addView(stepTitle("AKSES A — Domain + HTTPS (disarankan)"));
        content.addView(stepText("1. Arahkan DNS domain/subdomain ke IP VPS."));
        content.addView(stepText("2. Reverse proxy domain ke 127.0.0.1:9118 memakai Caddy/Nginx."));
        content.addView(code("labs.domain.com {\n    reverse_proxy 127.0.0.1:9118\n}"));
        content.addView(stepText("3. Isi Settings: https://labs.domain.com"));

        content.addView(stepTitle("AKSES B — Langsung IP VPS"));
        content.addView(stepText("1. Set LABS_HOST=0.0.0.0 pada Environment service Labs, lalu restart service."));
        content.addView(code("LABS_HOST=0.0.0.0\nLABS_PORT=9118\nLABS_SECURE_COOKIE=0"));
        content.addView(stepText("2. Izinkan port 9118 hanya dari IP kamu/VPN di firewall."));
        content.addView(code("sudo ufw allow from IP_KAMU to any port 9118 proto tcp"));
        content.addView(stepText("3. Isi Settings: http://IP_VPS:9118"));
        content.addView(stepText("Peringatan: HTTP publik tidak terenkripsi. Pakai VPN/private network; jangan buka port 9118 untuk semua alamat."));

        // Opsi B
        content.addView(stepTitle("CARA B — Pakai Agen / AI Assistant"));
        content.addView(stepText("1. Berikan instruksi ini ke asisten AI kamu (mis. Hermes):"));
        content.addView(code("Instal VPS Labs di server ini:\n" +
                "1. git clone https://github.com/OWNER/VPS-Labs.git ~/labs\n" +
                "2. cd ~/labs " + "&&" + " bash install.sh\n" +
                "3. Isi brand name, sub-judul, port, dan password admin\n" +
                "4. Pastikan service labs aktif dan lapor URL loginnya"));
        content.addView(stepText("2. Setelah instal, ubah URL di Settings app ini ke server kamu."));

        // Setelah instal
        content.addView(stepTitle("Setelah instal"));
        content.addView(stepText("1. Buka Settings app ini (⚙️) → isi URL domain HTTPS atau http://IP_VPS:9118."));
        content.addView(stepText("2. Simpan, lalu app otomatis memuat dashboard Labs kamu."));

        Button open = new Button(this);
        open.setText("🌐 Buka Repo GitHub");
        open.setTextSize(14);
        open.setTextColor(Color.WHITE);
        open.setBackgroundColor(Color.parseColor("#dc6268"));
        open.setPadding(dp(10), dp(14), dp(10), dp(14));
        open.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/OWNER/VPS-Labs")));
                } catch (Exception ignored) {}
            }
        });
        content.addView(open);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(content);
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

    // Code block + tombol salin
    private LinearLayout code(String t) {
        final String text = t;
        LinearLayout wrapper = new LinearLayout(this);
        wrapper.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout.LayoutParams wlp = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        wlp.setMargins(0, dp(6), 0, dp(6));
        wrapper.setLayoutParams(wlp);

        TextView tv = new TextView(this);
        tv.setText(t);
        tv.setTextSize(11.5f);
        tv.setTypeface(Typeface.MONOSPACE);
        tv.setTextColor(Color.parseColor("#eef5ff"));
        tv.setBackgroundColor(Color.parseColor("#17243d"));
        tv.setPadding(dp(12), dp(10), dp(8), dp(10));
        tv.setLayoutParams(new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        codeViews.add(tv);
        wrapper.addView(tv);

        Button copy = new Button(this);
        copy.setText("⧉");
        copy.setTextSize(16);
        copy.setTextColor(Color.parseColor("#f5f1e9"));
        copy.setBackgroundColor(Color.parseColor("#dc6268"));
        copy.setPadding(dp(6), dp(8), dp(6), dp(8));
        copy.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                ClipboardManager cm = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
                cm.setPrimaryClip(ClipData.newPlainText("labs-code", text));
                Toast.makeText(InstallationActivity.this, "✅ Tersalin", Toast.LENGTH_SHORT).show();
            }
        });
        wrapper.addView(copy);
        return wrapper;
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density);
    }
}
