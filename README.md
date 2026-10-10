# BOT by Zierra - Shared Edition

Bot desktop untuk memantau grup Telegram berlabel `*MASUK*`, menampilkan
ringkasan job, dan Terima/Tawar otomatis lewat Chrome Debug Mode.

## Isi repo

- `listener_gui.py` — aplikasi utama (Tkinter)
- `browser_actions.py` — otomasi Chrome (Selenium)
- `version.json` — dibaca otomatis oleh fitur auto-updater di dalam bot
  (`UPDATE_CHECK_URL`), jangan diubah formatnya

## Build ke .exe

```
rmdir /s /q build
rmdir /s /q dist
del *.spec
py -m PyInstaller --onefile --windowed --name BOT_by_Zierra ^
   --add-data "notif.wav;." --collect-all selenium --hidden-import keyboard ^
   listener_gui.py
```

## Rilis versi baru

1. Naikkan `APP_VERSION` di `listener_gui.py`
2. Build ulang `.exe` (lihat langkah Build di atas), lalu buat GitHub
   Release baru di repo ini dan upload `BOT_by_Zierra.exe` sebagai asset
   rilis itu
3. Update `version.json`:
   - `version` — samakan dengan `APP_VERSION`
   - `url` — link halaman rilis (fallback: dibuka di browser kalau
     update otomatis tidak bisa jalan)
   - `exe_url` — link **download langsung** ke asset `.exe` rilis itu
     (klik kanan nama file di halaman Release -> "Copy link address").
     Kalau diisi, pengguna yang sudah pakai `.exe` hasil build (bukan
     menjalankan dari source) akan ditawari **update otomatis**: bot
     mengunduh `.exe` baru, menutup diri sebentar, menimpa `.exe` lama,
     lalu membuka ulang sendiri — tidak perlu download manual lagi.
     Kalau dikosongkan, fallback ke cara lama (buka `url` di browser).
4. Commit & push — semua pengguna lama otomatis dapat notif update saat
   bot dibuka
