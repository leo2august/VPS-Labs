# Migrasi 9router → Labs (Twin Architecture)

Panduan lengkap untuk setup, migrasi akun, dan penggunaan Labs sebagai
pengganti/pelengkap 9router. Ikuti urutan ini biar tidak error.

---

## 1. Konsep Twin DB

Labs dan 9router berbagi **SATU SQLite yang sama**:
`/home/ubuntu/.9router/db/data.sqlite`

- Labs membaca/menulis langsung ke file ini (mode WAL + busy_timeout)
- 9router juga membaca/menulis file yang sama
- Hasilnya: **akun yang dibuat di 9router langsung muncul di Labs, dan sebaliknya**

File kunci di Labs:
| File | Fungsi |
|------|--------|
| `lab_db.py` | Helper koneksi SQLite (WAL, read/write) |
| `lab_quota.py` | Membaca provider quota dari DB |
| `lab_router_accounts.py` | Kelola akun (toggle, delete, test, import) |
| `lab_9router_bridge.py` | Bridge ke 9router service (device login OAuth) |
| `lab_oauth.py` | OAuth engine mandiri (token refresh, import) |
| `lab_proxy.py` | Endpoint OpenAI-compatible `/v1/*` tanpa 9router |

---

## 2. Prasyarat

- **9router terinstall** di `/usr/lib/node_modules/9router`
- **Service systemd** `9router.service` (di-ENABLE otomatis oleh Labs saat perlu)
- **Labs (vps-audit)** berjalan sebagai Flask app

### Setup service 9router (sekali saja):

```bash
sudo tee /etc/systemd/system/9router.service > /dev/null <<'EOF'
[Unit]
Description=9Router - AI Model Router (on-demand token issuer)
After=network.target

[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu
ExecStart=/usr/bin/node /usr/bin/9router -p 20128 --no-browser --skip-update
Restart=always
RestartSec=5
Environment=NODE_ENV=production
Environment=HOST=127.0.0.1
Environment=PATH=/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl unmask 9router.service 2>/dev/null || true
sudo systemctl enable --now 9router.service
```

> **PENTING:** Jika service pernah di-`mask` (untuk hemat RAM), unmask dulu:
> `sudo systemctl unmask 9router.service`

---

## 3. Alur Login OAuth (kiro / github / qwen / kilocode)

Provider ini butuh 9router untuk device login (karena OAuth signing key
hanya ada di 9router):

```
1. Labs → Provider Quota → "+ Tambah akun"
2. Pilih: Kiro AI | GitHub Copilot | Qwen Code | Kilocode
3. Labs otomatis menyalakan 9router (systemd start, ~3-15 detik)
4. Labs minta device code dari 9router → tampilkan link + kode login
5. User buka link di browser → login (AWS Builder ID / GitHub)
6. User klik "Allow access"
7. 9router selesaikan login → simpan akun ke DB
8. Labs baca akun baru dari DB → tampilkan di Provider Quota
9. Watchdog: 9router otomatis mati 15 menit kemudian (hemat RAM)
```

### Kenapa 9router dibutuhkan untuk OAuth?
- AWS Builder ID (kiro) butuh signing key internal 9router
- 9router punya client registration yang sudah terhubung ke AWS
- Labs tidak bisa membuat client baru (butuh private key AWS)
- Jadi: **9router = token-issuer on-demand**, Labs = UI + DB twin

---

## 4. Alur Import Token Kiro (tanpa login ulang)

Kalau sudah punya refreshToken kiro (dari Kiro IDE / AWS SSO cache):

```
1. Buka file: ~/.aws/sso/cache/kiro-auth-token.json
   (Windows: %USERPROFILE%\.aws\sso\cache\)
2. Salin nilai "refreshToken" (diawali aorAAAAAG)
3. Labs → Provider Quota → "+ Tambah akun" → "Kiro Import"
4. Paste refreshToken → "Import Kiro token"
5. Akun kiro masuk ke DB (format sama persis dengan 9router)
```

---

## 5. Custom Provider (B.AI, Gatekey, SeekAI, dll)

Custom provider dari config Hermes (`config.yaml`) otomatis muncul di
Provider Quota sebagai grup **config** — bisa di-test & dilihat modelnya.

### Tambah custom provider via UI:
```
1. Provider Quota → "+ Tambah akun" → "Custom Provider"
2. Isi: Nama, Base URL, API Key, Models (pisahkan koma)
3. "Tambah Custom" → tersimpan ke config.yaml + muncul di quota
```

### Edit / rename:
- Grup config → tombol **Edit** → ganti nama
- Atau di halaman Router → edit modal (nama, base_url, api_key, models)

---

## 6. API Proxy (tanpa 9router)

Labs punya endpoint OpenAI-compatible yang memakai token dari DB:

| Endpoint | Fungsi |
|----------|--------|
| `POST /v1/chat/completions` | Chat completion (login Labs required) |
| `GET /v1/models` | Daftar model tersedia |

Ini memungkinkan Hermes/klien lain memakai Labs sebagai router langsung,
tanpa 9router hidup, untuk provider API-key (deepseek, gatekey, dll).

---

## 7. Watchdog Auto-Off 9router

- 9router menyala otomatis saat dibutuhkan login OAuth
- **15 menit setelah terdeteksi nyala** → dimatikan otomatis (`systemctl stop`)
- Ini menghemat RAM (~180MB) saat tidak dipakai
- Login berikutnya → menyala lagi otomatis

---

## 8. Troubleshooting

| Masalah | Solusi |
|---------|--------|
| `9router tidak merespons` | Cek `systemctl status 9router` — start ulang manual |
| `invalid_client` saat poll | Kredensial AWS expired. Login ulang dari Kiro IDE / 9router |
| Akun tidak muncul di Labs | Cek DB: `sqlite3 /home/ubuntu/.9router/db/data.sqlite "SELECT COUNT(*) FROM providerConnections"` |
| Custom provider tidak muncul | Refresh halaman; pastikan config.yaml valid YAML |
| Port 20128 tidak terbuka | `systemctl start 9router.service` |
| Permission denied jobs.json | `sudo chown ubuntu:ubuntu /home/ubuntu/.hermes/cron/jobs.json` |

---

## 9. Catatan Penting

1. **Jangan hapus 9router** — masih dibutuhkan untuk OAuth kiro/github/qwen/kilocode
2. **DB adalah sumber kebenaran** — Labs dan 9router baca/tulis file yang sama
3. **Kredensial OAuth kiro punya masa berlaku** — kalau expired, login ulang via Kiro IDE di desktop, lalu import refreshToken ke Labs
4. **Custom provider** di config.yaml — Labs tidak perlu 9router untuk memakainya
5. **Backup** otomatis tetap berjalan (rclone gdrive+mega)
