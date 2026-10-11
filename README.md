# BOT by Zierra - Shared Edition

Bot desktop untuk memantau grup Telegram berlabel `*MASUK*`, menampilkan
ringkasan job, dan Terima/Tawar otomatis lewat Chrome Debug Mode.

## Isi repo

- `listener_gui.py` — aplikasi utama (Tkinter)
- `browser_actions.py` — otomasi Chrome (Selenium)
- `notif.wav` — suara notifikasi, ikut di-bundle ke dalam `.exe`
- `version.json` — dibaca otomatis oleh fitur auto-updater di dalam bot
  (`UPDATE_CHECK_URL`), jangan diubah formatnya
- `.github/workflows/build.yml` — build & rilis `.exe` OTOMATIS (lihat
  bagian "Rilis versi baru" di bawah)

## Build ke .exe (manual/lokal, opsional)

Build sekarang sudah otomatis lewat GitHub Actions (lihat di bawah) —
bagian ini cuma perlu kalau mau coba build sendiri di komputer lokal:

```
rmdir /s /q build
rmdir /s /q dist
del *.spec
py -m PyInstaller --onefile --windowed --name BOT_by_Zierra ^
   --add-data "notif.wav;." --collect-all selenium --hidden-import keyboard ^
   listener_gui.py
```

## Rilis versi baru (OTOMATIS)

Build & upload `.exe` sudah otomatis lewat GitHub Actions
(`.github/workflows/build.yml`). Setiap push ke `main` yang mengubah
`listener_gui.py`/`browser_actions.py`/`notif.wav`/`version.json`:

1. GitHub menjalankan build di komputer Windows sementara (gratis untuk
   repo public), persis seperti command manual di atas.
2. Hasil `.exe`-nya di-upload ke satu GitHub Release tetap bernama
   **"latest"** — asset lama di release itu ditimpa tiap build baru.
3. Karena nama release-nya selalu sama, link download-nya juga selalu
   sama:
   `https://github.com/mhmdsyafiq007/bot-zierra/releases/download/latest/BOT_by_Zierra.exe`
   — ini sudah terpasang permanen sebagai `exe_url` di `version.json`,
   **tidak perlu diubah-ubah lagi tiap rilis**.

Jadi langkah rilis versi baru tinggal:

1. Naikkan `APP_VERSION` di `listener_gui.py` DAN `version` di
   `version.json` (harus sama)
2. (opsional) update `notes` di `version.json` — catatan rilis yang
   ditampilkan ke pengguna
3. Commit & push ke `main`

Otomatis setelahnya: GitHub Actions build `.exe` baru -> upload ke
Release "latest" -> bot pengguna lama mendeteksi versi baru saat dibuka
-> ditawari update otomatis (unduh `.exe` baru, tutup sebentar, timpa
yang lama, buka ulang sendiri). Progress build bisa dipantau di tab
**Actions** repo ini.

`exe_url` cuma dipakai kalau bot sedang jalan sebagai `.exe` hasil
build (bukan dari source). Kalau suatu saat build otomatis gagal/mau
dimatikan, cukup kosongkan `exe_url` — updater otomatis fallback ke
cara lama (buka `url` di browser untuk diunduh manual).
