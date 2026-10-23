"""
BOT by Zierra - Shared Edition
Mendengarkan pesan berlabel *MASUK* di grup Telegram, lalu membuka link-nya
otomatis di browser dan membunyikan notifikasi.

Build ke exe:
  py -m PyInstaller --onefile --windowed --name BOT_by_Zierra ^
     --add-data "notif.wav;." listener_gui.py
"""

import os
import re
import sys
import json
import time
import random
import asyncio
import threading
import webbrowser
import tkinter as tk
from tkinter import messagebox, ttk

from telethon import TelegramClient, events
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PasswordHashInvalidError,
    FloodWaitError,
)

try:
    import winsound
except ImportError:
    winsound = None

try:
    import keyboard
except ImportError:
    keyboard = None

from browser_actions import (BrowserActions, compute_tawar_price,
                             compute_tawar_price_from_client)


# ---------------------------------------------------------------- paths
def resource_path(name):
    """File yang ikut ter-bundle di dalam exe (read-only), mis. notif.wav."""
    try:
        base = sys._MEIPASS
    except AttributeError:
        base = os.path.abspath(".")
    return os.path.join(base, name)


def external_path(name):
    """File yang perlu ditulis di samping exe, mis. config.json / .session."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.abspath(".")
    return os.path.join(base, name)


CONFIG_FILE = external_path("config.json")
SESSION_NAME = external_path("kawalu_shared")
SOUND_FILE = resource_path("notif.wav")

TARGET_CHAT = "KAWALU - Parafrase Notif"
LABEL = "*MASUK*"
# Dipakai fitur Catatan Job Proses — pesan *PAID*/*DONE* di grup yang
# sama, dicocokkan ke job kita lewat Ref (lihat _handle_status_broadcast).
# Kalau format label di grup ternyata beda, cukup sesuaikan dua baris ini.
LABEL_PAID = "*PAID*"
LABEL_DONE = "*DONE*"
# Pesan "REJECT NEGO" di grup yang sama = client menolak tawaran kita.
# Dicocokkan tanpa peduli huruf besar/kecil & tanda bintang (lihat
# _handle_reject_nego). Kalau tulisannya di grup ternyata beda, cukup
# sesuaikan baris ini.
LABEL_REJECT = "REJECT NEGO"
URL_PATTERN = re.compile(r"https?://\S+")
REF_PATTERN = re.compile(r"Ref:\s*(\S+)")
# Cadangan kalau pesan REJECT NEGO tidak memakai penulisan "Ref: ...".
REF_FALLBACK_PATTERN = re.compile(
    r"\b((?:PARAFRASE|HUMANIZER|FIXFILE)-[A-Za-z0-9]+)")
ID_PATTERN = re.compile(r"/([a-f0-9]{24})(?:[/?]|$)")

REF_COLLECTION = {
    "PARAFRASE": "parafrases",
    "HUMANIZER": "humanizers",
    # Dikonfirmasi lewat Network tab (Request URL order-detail):
    # collection=order-fix-files — sebelumnya salah tebak "fixfiles",
    # itu sebabnya popup Fix File kosong.
    "FIXFILE": "order-fix-files",
}


def ref_to_collection(ref):
    if not ref:
        return None
    prefix = ref.split("-")[0].upper()
    return REF_COLLECTION.get(prefix)


# Dipakai notifikasi suara beda per jenis (lihat play_sound) — bisa
# langsung ditentukan dari prefix Ref tanpa menunggu API, supaya bunyinya
# secepat pesan Telegram masuk.
REF_JENIS_LABEL = {
    "PARAFRASE": "Parafrase",
    "HUMANIZER": "Humanizer",
    "FIXFILE": "Fix File",
}

# Jeda antar pengecekan status koneksi (detik)
WATCHDOG_INTERVAL = 5

# ---------------------------------------------------------------- updater
# Versi aplikasi ini. UPDATE_CHECK_URL kosong = fitur cek update nonaktif
# (tidak ada tempat untuk dicek). Isi dengan link mentah (raw) ke sebuah
# file JSON publik berisi mis. {"version": "1.1.0", "url": "https://...",
# "notes": "..."} — misalnya raw.githubusercontent.com kalau exe-nya
# dibagikan lewat repo GitHub. Dicek sekali tiap aplikasi dibuka.
APP_VERSION = "1.15.2"
UPDATE_CHECK_URL = ("https://raw.githubusercontent.com/mhmdsyafiq007/"
                     "bot-zierra/main/version.json")


def build_client(api_id, api_hash):
    """Buat TelegramClient yang mencoba menyambung ulang tanpa batas.

    connection_retries=None -> Telethon tidak pernah menyerah reconnect
    sendiri saat koneksi terputus (default bawaan hanya beberapa kali lalu
    berhenti, itulah sebabnya sebelumnya listener diam saat jaringan drop).
    """
    return TelegramClient(
        SESSION_NAME,
        api_id,
        api_hash,
        connection_retries=None,   # retry tanpa batas
        retry_delay=2,             # jeda 2 detik tiap percobaan
        auto_reconnect=True,
        request_retries=5,
        timeout=10,
    )


# ---------------------------------------------------------------- palet
BG = "#0E1621"          # latar jendela
CARD = "#17212B"        # panel
CARD_EDGE = "#22303F"   # garis tepi panel
FIELD = "#1E2A38"       # isian
FIELD_EDGE = "#2C3D4F"
ACCENT = "#2AABEE"
ACCENT_DEEP = "#1A6FA8"
ACCENT_SOFT = "#173042"
TEXT = "#E7EEF5"
MUTED = "#8598AA"
GREEN = "#3FBF7F"
AMBER = "#D9A441"
RED = "#E0605E"

FONT = "Segoe UI"


def mix(c1, c2, t):
    """Campur dua warna hex dengan rasio t (0..1)."""
    a = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


# ---------------------------------------------------------------- widget
class RoundedButton(tk.Canvas):
    def __init__(self, parent, text, command, width=150, height=42,
                 fill=ACCENT, hover=None, radius=13, bg=None, subtle=False):
        bg = bg or parent["bg"]
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self.command = command
        self.enabled = True
        self.fill = fill
        self.hover = hover or mix(fill, "#ffffff", 0.16)
        self.subtle = subtle

        self.shape = round_rect(self, 1, 1, width - 1, height - 1, radius,
                                fill=fill, outline=mix(fill, "#ffffff", 0.22))
        self.label = self.create_text(width / 2, height / 2, text=text,
                                      fill=TEXT if subtle else "#FFFFFF",
                                      font=(FONT, 10, "bold"))

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _on_enter(self, _):
        if self.enabled:
            self.itemconfig(self.shape, fill=self.hover)
            self.config(cursor="hand2")

    def _on_leave(self, _):
        if self.enabled:
            self.itemconfig(self.shape, fill=self.fill)

    def _on_click(self, _):
        if self.enabled and self.command:
            self.command()

    def set_text(self, text):
        self.itemconfig(self.label, text=text)

    def set_enabled(self, value):
        self.enabled = value
        if value:
            self.itemconfig(self.shape, fill=self.fill,
                            outline=mix(self.fill, "#ffffff", 0.22))
            self.itemconfig(self.label, fill=TEXT if self.subtle else "#FFFFFF")
            self.config(cursor="hand2")
        else:
            self.itemconfig(self.shape, fill=ACCENT_SOFT, outline=FIELD_EDGE)
            self.itemconfig(self.label, fill=MUTED)
            self.config(cursor="")


class Field(tk.Frame):
    """Kolom isian dengan garis aksen yang menyala saat difokuskan."""

    def __init__(self, parent, label, show=None, tip=None):
        super().__init__(parent, bg=CARD)
        title = tk.Label(self, text=label.upper(), bg=CARD, fg=MUTED,
                         font=(FONT, 8, "bold"), anchor="w")
        title.pack(fill="x", pady=(0, 5))
        if tip:
            HoverTip(title, tip)

        box = tk.Frame(self, bg=FIELD_EDGE, padx=1, pady=1)
        box.pack(fill="x")

        inner = tk.Frame(box, bg=FIELD)
        inner.pack(fill="x")

        self.entry = tk.Entry(inner, bg=FIELD, fg=TEXT, relief="flat", show=show,
                              font=(FONT, 11), insertbackground=ACCENT,
                              highlightthickness=0, bd=0)
        self.entry.pack(fill="x", padx=11, pady=9)

        self.underline = tk.Frame(self, bg=FIELD_EDGE, height=2)
        self.underline.pack(fill="x")

        self.entry.bind("<FocusIn>", lambda _: self.underline.config(bg=ACCENT))
        self.entry.bind("<FocusOut>", lambda _: self.underline.config(bg=FIELD_EDGE))

    def get(self):
        return self.entry.get()

    def insert(self, value):
        self.entry.insert(0, value)

    def focus(self):
        self.entry.focus_set()


TIP_AUTO_TERIMA = (
    "Kalau aktif, job yang masuk dan memenuhi syarat di bawah di-Terima "
    "otomatis setelah jeda acak (Delay). Auto Tawar dicek lebih dulu; Auto "
    "Terima jadi cadangan kalau job tidak ditawar. Kalau tawaran ditolak "
    "client (pesan REJECT NEGO) dan status kembali waiting-confirmation, "
    "job yang memenuhi syarat ini juga langsung di-Terima."
)
TIP_AUTO_TAWAR = (
    "Kalau aktif, job yang memenuhi syarat Auto Tawar di bawah ditawar "
    "otomatis setelah jeda acak (Delay). Auto Tawar dicek lebih dulu "
    "daripada Auto Terima; satu job hanya memicu satu aksi otomatis."
)
TIP_TARGET_TAWAR = (
    "Harga tawar dihitung sebagai persentase dari Harga Estimasi (boleh "
    "lebih dari 100%), lalu dibulatkan ke bawah ke ribuan. Kalau tidak "
    "dicentang, bot memakai tawar default: sedikit di bawah Harga Estimasi. "
    "Apa pun metodenya, harga tawar tidak pernah di bawah Harga Client."
)
TIP_RANGE = (
    "Kalau Harga Estimasi job berada di antara Harga Min dan Max, dasar "
    "persentase dinaikkan menjadi Harga Max. Jadi persentase di atas 100% "
    "menjamin tawar di atas Harga Max. Contoh: rentang 60.000-100.000, "
    "estimasi 67.000, persentase 120% -> 100.000 x 120% = 120.000."
)
TIP_DEFAULT_FLOOR = (
    "Pengaturan tawar default: kalau Harga Estimasi DAN Harga Client sama-sama "
    "di bawah angka ini, tawar default menjadi angka ini. Contoh: 100000 "
    "berarti job dengan estimasi dan client di bawah Rp100.000 ditawar "
    "Rp100.000. Hanya berlaku saat memakai tawar default (Target Tawar % "
    "tidak dicentang, atau Harga Estimasi di luar rentang harga)."
)
TIP_OUTSIDE = (
    "Kalau Harga Estimasi job di luar rentang, bot memakai tawar default "
    "(sedikit di bawah Harga Estimasi), bukan persentase. Jadi persentase "
    "di atas 100% hanya berlaku untuk job di dalam rentang. Kalau tidak "
    "dicentang, di luar rentang tetap memakai persentase biasa."
)


def tip_jenis(aksi, jenis):
    return (f"Aktifkan {aksi} untuk job {jenis}. Syarat khusus {jenis} "
            f"diatur di panel {jenis.upper()} di bawah.")


_TIP_DEADLINE_MIN = (
    "Sisa waktu sampai deadline job minimal sekian jam. Syarat deadline "
    "hanya berlaku kalau Deadline min DAN max sama-sama diisi."
)
_TIP_DEADLINE_MAX = (
    "Sisa waktu sampai deadline job maksimal sekian jam. Syarat deadline "
    "hanya berlaku kalau Deadline min DAN max sama-sama diisi."
)
_TIP_WORD_MIN = (
    "Jumlah kata minimal (sama atau lebih). Khusus Humanizer; kosongkan "
    "kalau tidak dibatasi."
)
_TIP_WORD_MAX = (
    "Jumlah kata harus KURANG dari angka ini. Khusus Humanizer; kosongkan "
    "kalau tidak dibatasi."
)

# Penjelasan judul field syarat Auto Terima (kunci = key di field_defs).
TIPS_TERIMA = {
    "min_target": "Target Persentase job minimal sama dengan angka ini. "
                  "Di bawahnya ditolak.",
    "min_client_pct": "Perbandingan Harga Client dengan Harga Estimasi "
                      "(Client / Estimasi x 100). Job di bawah persentase ini "
                      "ditolak. Contoh: 80 berarti harga client minimal 80% "
                      "dari estimasi.",
    "min_client_price": "Batas minimal HARGA CLIENT dalam rupiah (harga "
                        "yang diajukan client, bukan Harga Estimasi). Job "
                        "dengan Harga Client di bawah angka ini ditolak. "
                        "Contoh: 75000 berarti Harga Client minimal Rp75.000, "
                        "berapa pun persentasenya terhadap estimasi.",
    "deadline_min_h": _TIP_DEADLINE_MIN,
    "deadline_max_h": _TIP_DEADLINE_MAX,
    "min_word_count": _TIP_WORD_MIN,
    "max_word_count": _TIP_WORD_MAX,
}

# Penjelasan judul field syarat Auto Tawar.
TIPS_TAWAR = {
    "target_min": "Target Persentase job minimal sama dengan angka ini.",
    "target_max": "Target Persentase job maksimal sama dengan angka ini. "
                  "Di atasnya ditolak.",
    "max_client_pct": "Perbandingan Harga Client dengan Harga Estimasi "
                      "(Client / Estimasi x 100). Job hanya ditawar kalau "
                      "persentasenya DI BAWAH angka ini. Contoh: 79 berarti "
                      "harga client harus kurang dari 79% estimasi.",
    "deadline_min_h": _TIP_DEADLINE_MIN,
    "deadline_max_h": _TIP_DEADLINE_MAX,
    "min_word_count": _TIP_WORD_MIN,
    "max_word_count": _TIP_WORD_MAX,
}

TIP_DELAY_TERIMA = (
    "Jeda acak (milidetik) antara job terdeteksi memenuhi syarat dan Terima "
    "otomatis dijalankan. Angka pertama batas bawah, kedua batas atas. "
    "Isi 0 dan 0 untuk tanpa jeda."
)
TIP_DELAY_TAWAR = (
    "Jeda acak (milidetik) antara job terdeteksi memenuhi syarat dan Tawar "
    "otomatis dijalankan. Angka pertama batas bawah, kedua batas atas. "
    "Isi 0 dan 0 untuk tanpa jeda."
)
TIP_SHORTCUT_TERIMA = (
    "Tombol pintas keyboard untuk Terima job yang sedang aktif. Klik "
    "kotaknya, tekan tombol atau kombinasi baru, lalu klik Simpan."
)
TIP_SHORTCUT_TAWAR = (
    "Tombol pintas keyboard untuk Tawar job yang sedang aktif. Klik "
    "kotaknya, tekan tombol atau kombinasi baru, lalu klik Simpan."
)
TIP_VOLUME = "Mengatur kerasnya suara notifikasi saat job baru masuk."
TIP_SERVICE_INCLUDE = (
    "Job Fix File hanya diproses kalau punya SALAH SATU layanan yang "
    "dicentang di sini (boleh punya layanan lain juga). Kosong berarti "
    "semua layanan boleh."
)
TIP_SERVICE_EXCLUDE = (
    "Job Fix File ditolak kalau punya SALAH SATU layanan yang dicentang "
    "di sini."
)
TIP_CHROME_DEBUG = (
    "Chrome khusus (mode debug) yang dikendalikan bot. Pasang / Buka "
    "membuka Chrome itu kalau belum aktif (tidak akan membuka dobel). "
    "Shortcut Desktop membuat pintasan untuk membukanya."
)



class HoverTip:
    """Penjelasan kecil yang muncul saat kursor diarahkan ke sebuah widget
    (tanpa klik) dan hilang saat kursor pergi. Dipakai untuk judul field
    maupun tanda seru (InfoTip)."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def show(self, event=None):
        if self.tip is not None:
            return
        w = self.widget
        tip = tk.Toplevel(w)
        tip.wm_overrideredirect(True)
        try:
            tip.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        box = tk.Frame(tip, bg=ACCENT, padx=1, pady=1)
        box.pack()
        tk.Label(box, text=self.text, bg=FIELD, fg=TEXT, font=(FONT, 8),
                 justify="left", wraplength=290, padx=10, pady=8).pack()
        tip.update_idletasks()
        tw, th = tip.winfo_reqwidth(), tip.winfo_reqheight()
        x = w.winfo_rootx() + 4
        y = w.winfo_rooty() + w.winfo_height() + 4
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        if x + tw > sw - 8:
            x = max(8, sw - tw - 8)
        if y + th > sh - 8:
            y = max(8, w.winfo_rooty() - th - 4)
        tip.wm_geometry(f"+{x}+{y}")
        self.tip = tip

    def hide(self, event=None):
        if self.tip is not None:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None


class InfoTip(tk.Canvas):
    """Tanda seru kecil di samping checkbox; penjelasan muncul saat kursor
    diarahkan ke atasnya (tanpa klik)."""

    SIZE = 16

    def __init__(self, parent, text):
        bg = parent["bg"]
        super().__init__(parent, width=self.SIZE, height=self.SIZE, bg=bg,
                         highlightthickness=0, bd=0, cursor="question_arrow")
        self.circle = self.create_oval(1, 1, self.SIZE - 1, self.SIZE - 1,
                                       outline=ACCENT, fill=bg, width=1)
        self.create_text(self.SIZE / 2, self.SIZE / 2 + 0.5, text="!",
                         fill=ACCENT, font=(FONT, 8, "bold"))
        self._bg = bg
        self._hover = HoverTip(self, text)
        self.bind("<Enter>", lambda _: self.itemconfig(self.circle, fill=ACCENT_SOFT), add="+")
        self.bind("<Leave>", self._unhighlight, add="+")

    def _unhighlight(self, _=None):
        try:
            self.itemconfig(self.circle, fill=self._bg)
        except tk.TclError:
            pass


# ---------------------------------------------------------------- worker
class TelegramWorker:
    """Event loop asyncio di thread terpisah agar jendela tidak membeku."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.client = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro, done=None):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        if done is not None:
            def _cb(f):
                try:
                    done(f.result(), None)
                except Exception as exc:          # noqa: BLE001
                    done(None, exc)
            future.add_done_callback(_cb)
        return future

    def shutdown(self):
        if self.client is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self.client.disconnect(), self.loop).result(timeout=5)
            except Exception:                     # noqa: BLE001
                pass
        self.loop.call_soon_threadsafe(self.loop.stop)


# ---------------------------------------------------------------- aplikasi
class App(tk.Tk):
    STEPS = ("Kredensial", "Nomor", "Kode", "Sandi")

    def __init__(self):
        super().__init__()
        self.title("BOT by Zierra - Shared")
        self.geometry("470x560")
        self.minsize(380, 360)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.worker = TelegramWorker()
        self.is_paused = True
        self.online = True          # status koneksi, dipantau watchdog
        self.watchdog_running = False
        self.watchdog_active = False  # reconnect hanya aktif saat listener jalan
        self.opened_urls = set()
        self.phone = None
        self.api_id = None
        self.api_hash = None

        self.browser = BrowserActions(log=self._safe_log)
        self.hotkey_terima = "f2"
        self.hotkey_tawar = "f3"
        self._hotkeys_registered = False
        self.job_popup = None
        self.current_job = {}
        self.poll_token = 0
        self.popup_position = self.load_popup_position()  # (x, y) atau None

        auto = self.load_auto_settings()
        self.auto_enabled = auto["enabled"]
        self.auto_delay_min_ms = auto["delay_min_ms"]
        self.auto_delay_max_ms = auto["delay_max_ms"]
        # Syarat sekarang per-jenis (bisa lebih dari satu jenis aktif
        # bersamaan) -> {"Parafrase": {...}, "Humanizer": {...}, "Fix File": {...}}
        self.auto_jenis_settings = auto["jenis_settings"]

        # Auto Tawar: fitur terpisah dari Auto Terima, syarat sendiri per
        # jenis, delay sendiri. Kalau job lolos syarat Auto Terima DAN
        # Auto Tawar sekaligus, Auto Terima yang menang (dicek duluan di
        # _maybe_auto_actions) — Auto Tawar jadi fallback untuk job yang
        # kurang bagus harganya tapi masih worth ditawar.
        auto_tawar = self.load_auto_tawar_settings()
        self.auto_tawar_enabled = auto_tawar["enabled"]
        self.auto_tawar_delay_min_ms = auto_tawar["delay_min_ms"]
        self.auto_tawar_delay_max_ms = auto_tawar["delay_max_ms"]
        self.auto_tawar_jenis_settings = auto_tawar["jenis_settings"]
        # Satu token dedupe dipakai bersama Auto Terima & Auto Tawar —
        # begitu salah satu aksi terpicu untuk suatu job, yang lain tidak
        # boleh ikut terpicu juga buat job yang sama.
        self.auto_action_fired_token = None

        # Catatan Job Proses: job yang sudah diterima lalu status-nya
        # dipantau sampai berubah (mis. dari "Menunggu Pembayaran" jadi
        # "Proses"), dicatat di sini untuk dilihat hari itu saja.
        self.job_notes_list = self.load_job_notes()
        self.notes_popup = None
        self.notes_popup_position = self.load_notes_popup_position()

        # Job yang baru diterima & masih dipantau (ref -> job snapshot).
        # Dipakai _watch_job_status (polling tiap 45 detik, fallback) dan
        # juga dicek tiap pesan *PAID*/*DONE* masuk di grup Telegram —
        # supaya status bisa terdeteksi lebih cepat tanpa nunggu polling.
        # Runtime-only, tidak disimpan ke config.json.
        self.watched_jobs = {}
        # ref -> {id, collection, url} untuk semua job *MASUK* yang pernah
        # dilihat sesi ini; dipakai _handle_reject_nego kalau pesan REJECT
        # NEGO tidak membawa link job. _reject_inflight mencegah satu ref
        # diproses ganda kalau pesannya datang berulang.
        self.known_jobs = {}
        self._reject_inflight = set()

        self.sound_volume = self.load_sound_volume()
        self._volume_save_job = None
        self._set_wave_volume(self.sound_volume)

        # Tinggi kotak log bisa ditarik manual (lihat grip di show_log_screen
        # dan _on_log_resize_*), diingat untuk sesi berikutnya.
        self.log_height = self.load_log_height()
        self._log_resize_start_y = None
        self._log_resize_start_h = None
        self._log_resize_dragged = False

        # Dropdown ttk (Combobox) punya listbox popup terpisah dari widget
        # utamanya — tanpa ini, listbox-nya selalu putih bawaan OS
        # walaupun field combobox-nya sendiri sudah digelapkan lewat style.
        self.option_add("*TCombobox*Listbox.background", FIELD)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DEEP)
        self.option_add("*TCombobox*Listbox.selectForeground", TEXT)

        # Style ttk dark-mode didaftarkan sekali di sini (bukan di tiap
        # layar) supaya sudah siap dipakai scrollbar body yang dibangun
        # lebih awal lewat _build_shell().
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.Vertical.TScrollbar",
                        background=FIELD_EDGE, troughcolor=CARD,
                        bordercolor=CARD, arrowcolor=TEXT,
                        darkcolor=CARD, lightcolor=CARD)
        style.map("Dark.Vertical.TScrollbar",
                  background=[("active", ACCENT_DEEP), ("pressed", ACCENT_DEEP)])
        # Scrollbar HORIZONTAL body — dipasang kalau jendela terlalu
        # sempit untuk menampung semua kolom syarat sekaligus (mis. panel
        # Humanizer dengan banyak field), lihat _on_body_canvas_resize.
        style.configure("Dark.Horizontal.TScrollbar",
                        background=FIELD_EDGE, troughcolor=CARD,
                        bordercolor=CARD, arrowcolor=TEXT,
                        darkcolor=CARD, lightcolor=CARD)
        style.map("Dark.Horizontal.TScrollbar",
                  background=[("active", ACCENT_DEEP), ("pressed", ACCENT_DEEP)])
        # Varian trough FIELD khusus untuk scrollbar di sebelah log box
        # (yang latarnya FIELD, bukan CARD seperti body).
        style.configure("DarkField.Vertical.TScrollbar",
                        background=FIELD_EDGE, troughcolor=FIELD,
                        bordercolor=FIELD, arrowcolor=TEXT,
                        darkcolor=FIELD, lightcolor=FIELD)
        style.map("DarkField.Vertical.TScrollbar",
                  background=[("active", ACCENT_DEEP), ("pressed", ACCENT_DEEP)])
        style.configure("Dark.Horizontal.TScale", background=CARD,
                        troughcolor=FIELD, lightcolor=ACCENT_DEEP,
                        darkcolor=ACCENT_DEEP)
        style.map("Dark.Horizontal.TScale",
                  background=[("active", CARD)])

        self._build_shell()
        self.show_credentials_step()
        self.after(250, self.try_existing_session)
        # Title bar bawaan Windows defaultnya putih terang; ini tidak bisa
        # diwarnai lewat Tkinter biasa, harus lewat API DWM milik Windows.
        self.after(50, self._apply_windows_dark_titlebar)

    def _apply_windows_dark_titlebar(self):
        """Best-effort — hanya berlaku di Windows 10 versi 1809 ke atas /
        Windows 11. Dibungkus try/except penuh supaya tidak pernah
        menggagalkan aplikasi di OS lain atau versi Windows lama."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            value = ctypes.c_int(1)
            for attr in (20, 19):  # 20 = Win10 1903+/Win11, 19 = build lama
                res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
                )
                if res == 0:
                    break
        except Exception:                          # noqa: BLE001
            pass  # OS/versi tidak mendukung — tampilan tetap jalan normal

    # ------------------------------------------------------ kerangka
    def _build_shell(self):
        self.header = tk.Canvas(self, height=96, bg=BG, highlightthickness=0, bd=0)
        self.header.pack(fill="x")
        self.header.bind("<Configure>", self._draw_header)

        tk.Frame(self, bg=ACCENT_DEEP, height=1).pack(fill="x")

        outer = tk.Frame(self, bg=CARD_EDGE, padx=1, pady=1)
        outer.pack(fill="both", expand=True, padx=16, pady=(14, 10))

        inner = tk.Frame(outer, bg=CARD)
        inner.pack(fill="both", expand=True)

        # Seluruh isi "body" dibungkus Canvas+Scrollbar (vertikal DAN
        # horizontal) supaya bisa digulir kalau jendela terlalu kecil
        # untuk menampung semua panel sekaligus (vertikal — mis. Auto
        # Terima dengan banyak syarat) ATAU terlalu SEMPIT untuk
        # menampung semua kolom satu baris syarat (horizontal — mis.
        # panel Humanizer dengan banyak field, kepotong di kanan kalau
        # jendela windowed dikecilkan). Dua-duanya baru dipasang kalau
        # memang diperlukan (lihat _on_body_scroll/_on_body_hscroll),
        # supaya tidak selalu kelihatan kalau isinya pas.
        inner.grid_rowconfigure(0, weight=1)
        inner.grid_columnconfigure(0, weight=1)

        self.body_canvas = tk.Canvas(inner, bg=CARD, highlightthickness=0, bd=0)
        self.body_scroll = ttk.Scrollbar(inner, orient="vertical",
                                         command=self.body_canvas.yview,
                                         style="Dark.Vertical.TScrollbar")
        self.body_hscroll = ttk.Scrollbar(inner, orient="horizontal",
                                          command=self.body_canvas.xview,
                                          style="Dark.Horizontal.TScrollbar")
        self.body_canvas.configure(yscrollcommand=self._on_body_scroll,
                                   xscrollcommand=self._on_body_hscroll)
        self.body_canvas.grid(row=0, column=0, sticky="nsew")

        self.body = tk.Frame(self.body_canvas, bg=CARD, padx=22, pady=20)
        self.body_window = self.body_canvas.create_window(
            (0, 0), window=self.body, anchor="nw"
        )
        self.body.bind("<Configure>", self._on_body_content_resize)
        self.body_canvas.bind("<Configure>", self._on_body_canvas_resize)

        # Scroll roda mouse hanya aktif selagi kursor di atas body, supaya
        # tidak "mencuri" scroll widget lain (mis. dropdown) saat lewat.
        # Roda biasa = vertikal, Shift+roda = horizontal (konvensi umum).
        self.body_canvas.bind("<Enter>", lambda e: self._bind_mousewheel())
        self.body_canvas.bind("<Leave>", lambda e: self._unbind_mousewheel())

        self.footer = tk.Label(self, text="", bg=BG, fg=MUTED, font=(FONT, 8))
        self.footer.pack(pady=(0, 8))

    def _on_body_content_resize(self, event=None):
        self.body_canvas.configure(scrollregion=self.body_canvas.bbox("all"))

    def _on_body_canvas_resize(self, event):
        # Lebar isi body MENGIKUTI canvas kalau canvas lebih lebar dari
        # kebutuhan asli body (supaya elemen ber-sticky "ew" melebar
        # penuh seperti biasa) — tapi TIDAK dipaksa menyempit di bawah
        # lebar asli yang dibutuhkan isinya (winfo_reqwidth). Kalau
        # jendela dikecilkan sampai lebih sempit dari itu, body tetap
        # selebar aslinya dan scrollbar horizontal muncul untuk
        # menggulir ke bagian yang kepotong, bukan memaksa field-field
        # saling tindih/kepotong permanen.
        body_reqwidth = self.body.winfo_reqwidth()
        new_width = max(event.width, body_reqwidth)
        self.body_canvas.itemconfig(self.body_window, width=new_width)

    def _on_body_scroll(self, first, last):
        """Dipanggil tiap scrollregion berubah — scrollbar cuma dipasang
        kalau isi body memang lebih panjang dari area yang kelihatan."""
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.body_scroll.grid_remove()
        else:
            self.body_scroll.grid(row=0, column=1, sticky="ns")
        self.body_scroll.set(first, last)

    def _on_body_hscroll(self, first, last):
        """Sama seperti _on_body_scroll tapi untuk arah horizontal."""
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.body_hscroll.grid_remove()
        else:
            self.body_hscroll.grid(row=1, column=0, sticky="ew")
        self.body_hscroll.set(first, last)

    def _bind_mousewheel(self):
        self.body_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.body_canvas.bind_all("<Shift-MouseWheel>", self._on_mousewheel_h)

    def _unbind_mousewheel(self):
        self.body_canvas.unbind_all("<MouseWheel>")
        self.body_canvas.unbind_all("<Shift-MouseWheel>")

    def _on_mousewheel_h(self, event):
        self.body_canvas.xview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_mousewheel(self, event):
        self.body_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_log_mousewheel(self, event):
        """Dipasang di log_box sendiri (bukan bind_all), jadi cuma aktif
        kalau kursor memang di atas kotak log. Scroll log dulu sampai
        benar2 mentok (atas/bawah) — begitu diputar lagi melewati batas
        itu, baru diteruskan ke scroll body di belakangnya, supaya tidak
        kerasa "nyangkut" saat mau lanjut scroll halaman lewat kotak log.
        Selalu return "break" supaya event ini tidak diproses dobel oleh
        binding bind_all milik body (lihat _bind_mousewheel)."""
        first, last = self.log_box.yview()
        delta = int(-1 * (event.delta / 120))
        at_top = float(first) <= 0.0
        at_bottom = float(last) >= 1.0
        if (delta < 0 and at_top) or (delta > 0 and at_bottom):
            self._on_mousewheel(event)
        else:
            self.log_box.yview_scroll(delta, "units")
        return "break"

    def _draw_header(self, event=None):
        c = self.header
        c.delete("all")
        w = event.width if event else self.winfo_width()
        h = 96
        for x in range(max(w, 1)):
            c.create_line(x, 0, x, h, fill=mix(ACCENT_DEEP, "#0B2233", x / max(w, 1)))
        c.create_polygon(0, h, w * 0.55, 0, w * 0.75, 0, w * 0.18, h,
                         fill=mix(ACCENT, "#0B2233", 0.72), outline="")
        c.create_text(24, 40, text="BOT by Zierra", anchor="w",
                      fill="#FFFFFF", font=(FONT, 17, "bold"))
        c.create_text(26, 66, text="SHARED  EDITION", anchor="w",
                      fill=mix(ACCENT, "#FFFFFF", 0.5), font=(FONT, 8, "bold"))

    def clear_body(self):
        for child in self.body.winfo_children():
            child.destroy()

    def step_dots(self, active):
        row = tk.Frame(self.body, bg=CARD)
        row.pack(fill="x", pady=(0, 18))
        for i in range(len(self.STEPS)):
            dot = tk.Canvas(row, width=10, height=10, bg=CARD,
                            highlightthickness=0, bd=0)
            dot.create_oval(1, 1, 9, 9,
                            fill=ACCENT if i <= active else FIELD_EDGE, outline="")
            dot.pack(side="left")
            if i < len(self.STEPS) - 1:
                tk.Frame(row, bg=ACCENT if i < active else FIELD_EDGE,
                         height=2, width=42).pack(side="left", padx=5)
        tk.Label(self.body, text=self.STEPS[active], bg=CARD, fg=TEXT,
                 font=(FONT, 13, "bold"), anchor="w").pack(fill="x")

    def add_hint(self, text):
        tk.Label(self.body, text=text, bg=CARD, fg=MUTED, font=(FONT, 9),
                 wraplength=360, justify="left", anchor="w").pack(fill="x",
                                                                 pady=(2, 14))

    def add_status(self):
        self.status = tk.Label(self.body, text="", bg=CARD, fg=MUTED,
                               font=(FONT, 9), wraplength=360, justify="left")
        self.status.pack(fill="x", pady=(4, 0))

    def set_status(self, text, color=MUTED):
        if getattr(self, "status", None) and self.status.winfo_exists():
            self.status.config(text=text, fg=color)

    def ui(self, fn, *args):
        self.after(0, lambda: fn(*args))

    def _safe_log(self, text, tag="sys"):
        """Log yang aman dipanggil dari thread mana pun (hotkey, Selenium)."""
        self.ui(self.log, text, tag)

    # ------------------------------------------------------ keyboard helper
    def bind_enter(self, entry, command):
        """Tekan Enter pada Entry untuk menjalankan command."""
        entry.bind("<Return>", lambda _: command())

    def phone_auto_prefix(self, event=None):
        """Jika nomor diawali 0, otomatis ubah menjadi +62."""
        value = self.f_phone.get()
        if value.startswith("0"):
            self.f_phone.entry.delete(0, "end")
            self.f_phone.entry.insert(0, "+62" + value[1:])

    # ------------------------------------------------------ langkah login
    def show_credentials_step(self):
        self.clear_body()
        self.step_dots(0)
        self.add_hint("Ambil API ID dan API Hash di my.telegram.org, lalu tempel "
                      "di bawah. Hanya untuk pertama kali.")

        # Belum punya API ID/Hash? Tombol ini membuka halaman pendaftaran
        # resmi Telegram (login pakai nomor HP -> "API development tools"
        # -> isi App title & Short name -> API ID & API Hash muncul).
        # Pendaftarannya sendiri tidak bisa diotomatisasi (butuh kode OTP
        # dari Telegram), jadi bot cukup membukakan halamannya.
        RoundedButton(
            self.body, "Daftar & Ambil API ID / Hash",
            lambda: webbrowser.open("https://my.telegram.org/apps"),
            width=250, height=34, fill=ACCENT_SOFT, bg=CARD, subtle=True,
        ).pack(anchor="w", pady=(0, 6))
        # Tutorial bisa dilipat, default TERTUTUP. guide langsung di-pack
        # "after=toggle" (tanpa frame pembungkus) — frame pembungkus kosong
        # tetap mempertahankan tinggi lama di Tk setelah isinya dilepas,
        # yang bikin field di bawahnya berjarak jauh saat tutorial ditutup.
        guide_open = {"v": False}
        toggle = tk.Label(self.body, bg=CARD, fg=ACCENT, cursor="hand2",
                          font=(FONT, 8, "bold"), anchor="w")
        toggle.pack(anchor="w", pady=(0, 8))
        guide = tk.Frame(self.body, bg=CARD)

        def _apply_guide():
            if guide_open["v"]:
                guide.pack(fill="x", pady=(0, 12), after=toggle)
                toggle.config(text="\u25BE Sembunyikan tutorial API ID & Hash")
            else:
                guide.pack_forget()
                toggle.config(text="\u25B8 Tampilkan tutorial API ID & Hash")

        def _toggle_guide(_=None):
            guide_open["v"] = not guide_open["v"]
            _apply_guide()

        toggle.bind("<Button-1>", _toggle_guide)
        tk.Label(guide, text="CARA MENDAPATKAN API ID & API HASH", bg=CARD,
                 fg=MUTED, font=(FONT, 7, "bold"), anchor="w").pack(anchor="w")
        steps = [
            ("1.", "Klik tombol di atas (membuka my.telegram.org/apps), "
                   "masukkan nomor HP Telegram dengan kode negara, "
                   "mis. +628123456789, lalu klik Next."),
            ("2.", "Telegram di HP/PC mengirim kode login ke chat "
                   "\"Telegram\". Masukkan kode itu di halaman web, "
                   "lalu Sign In."),
            ("3.", "Klik \"API development tools\"."),
            ("4.", "Isi form: App title (bebas, mis. MyBot), Short name "
                   "(bebas, 5-32 huruf/angka, mis. mybot123), Platform: "
                   "Desktop. Kolom lain boleh dikosongkan. Klik Create "
                   "application."),
            ("5.", "Di halaman \"App configuration\" hanya dua nilai ini "
                   "yang disalin:"),
        ]
        for num, text in steps:
            line = tk.Frame(guide, bg=CARD)
            line.pack(fill="x", anchor="w", pady=(3, 0))
            tk.Label(line, text=num, bg=CARD, fg=ACCENT,
                     font=(FONT, 8, "bold"), width=2, anchor="ne").pack(
                side="left", anchor="n")
            tk.Label(line, text=text, bg=CARD, fg=TEXT, font=(FONT, 8),
                     justify="left", wraplength=290, anchor="w").pack(
                side="left", anchor="n")
        copy_box = tk.Frame(guide, bg=FIELD)
        copy_box.pack(fill="x", pady=(6, 0))
        tk.Label(copy_box,
                 text="App api_id  (angka, mis. 1234567)\n"
                      "   -> tempel ke kolom API ID",
                 bg=FIELD, fg=GREEN, font=(FONT, 8, "bold"),
                 justify="left", anchor="w").pack(
            anchor="w", padx=8, pady=(6, 2))
        tk.Label(copy_box,
                 text="App api_hash  (32 huruf & angka)\n"
                      "   -> tempel ke kolom API Hash",
                 bg=FIELD, fg=GREEN, font=(FONT, 8, "bold"),
                 justify="left", anchor="w").pack(
            anchor="w", padx=8, pady=(2, 6))
        tk.Label(guide,
                 text="Jangan salin Public keys / Production configuration "
                      "dan jangan bagikan api_hash ke siapa pun. Kalau muncul "
                      "tulisan ERROR, matikan VPN lalu coba lagi beberapa "
                      "saat kemudian.",
                 bg=CARD, fg=MUTED, font=(FONT, 8), justify="left",
                 wraplength=300, anchor="w").pack(anchor="w", pady=(6, 0))

        _apply_guide()

        self.f_api_id = Field(self.body, "API ID", tip=(
            "Nomor identitas aplikasi Telegram (berupa angka) dari "
            "my.telegram.org/apps, bagian App api_id."))
        self.f_api_id.pack(fill="x", pady=(0, 14))
        self.f_api_hash = Field(self.body, "API Hash", tip=(
            "Kode rahasia 32 huruf dan angka dari my.telegram.org/apps, "
            "bagian App api_hash. Jangan dibagikan ke siapa pun."))
        self.f_api_hash.pack(fill="x")

        self.bind_enter(self.f_api_id.entry, lambda: self.f_api_hash.focus())
        self.bind_enter(self.f_api_hash.entry, self.submit_credentials)

        self.btn_login = RoundedButton(self.body, "Login",
                                       self.submit_credentials, bg=CARD)
        self.btn_login.pack(pady=20)
        self.add_status()

        saved = self.load_config()
        if saved:
            self.f_api_id.insert(str(saved["api_id"]))
            self.f_api_hash.insert(saved["api_hash"])
        self.footer.config(text="Konfigurasi disimpan di folder aplikasi ini")

    def show_phone_step(self):
        self.clear_body()
        self.step_dots(1)
        self.add_hint("Gunakan format internasional, contoh +628123456789.")

        self.f_phone = Field(self.body, "Nomor telepon", tip=(
            "Nomor HP akun Telegram yang dipakai memantau grup, format "
            "internasional (+62...). Awalan 0 otomatis diubah jadi +62."))
        self.f_phone.pack(fill="x")
        self.f_phone.focus()

        self.f_phone.entry.bind("<KeyRelease>", self.phone_auto_prefix)
        self.bind_enter(self.f_phone.entry, self.submit_phone)

        self.btn_login = RoundedButton(self.body, "Kirim kode",
                                       self.submit_phone, bg=CARD)
        self.btn_login.pack(pady=20)
        self.add_status()

    def show_code_step(self):
        self.clear_body()
        self.step_dots(2)
        self.add_hint("Kode dikirim sebagai pesan di aplikasi Telegram kamu, "
                      "bukan SMS.")

        self.f_code = Field(self.body, "Kode verifikasi", tip=(
            "Kode login yang dikirim Telegram ke aplikasi Telegram Anda "
            "(biasanya di chat \"Telegram\", bukan SMS)."))
        self.f_code.pack(fill="x")
        self.f_code.focus()

        self.bind_enter(self.f_code.entry, self.submit_code)

        self.btn_login = RoundedButton(self.body, "Verifikasi",
                                       self.submit_code, bg=CARD)
        self.btn_login.pack(pady=20)
        self.add_status()

    def show_password_step(self):
        self.clear_body()
        self.step_dots(3)
        self.add_hint("Akun ini memakai verifikasi dua langkah. Pastikan benar "
                      "sejak percobaan pertama, Telegram membatasi percobaan gagal.")

        self.f_password = Field(self.body, "Password 2FA", show="\u2022", tip=(
            "Hanya diisi kalau akun Telegram memakai verifikasi dua langkah "
            "(password tambahan)."))
        self.f_password.pack(fill="x")
        self.f_password.focus()

        self.bind_enter(self.f_password.entry, self.submit_password)

        self.btn_login = RoundedButton(self.body, "Masuk",
                                       self.submit_password, bg=CARD)
        self.btn_login.pack(pady=20)
        self.add_status()

    # ------------------------------------------------------ layar log
    def show_log_screen(self, display_name=""):
        self.clear_body()
        self.geometry("640x680")

        # Baris 1 (kotak log) TIDAK diberi weight lagi — tingginya sekarang
        # ditentukan manual lewat grip di baris 2 (lihat _on_log_resize_*),
        # bukan otomatis mengikuti sisa ruang jendela. Kalau jendela lebih
        # tinggi dari total konten, sisanya cukup kosong — body sudah bisa
        # digulir lewat canvas di _build_shell jadi ini aman.
        self.body.grid_rowconfigure(0, weight=0)
        # minsize di baris 1 ini yang jadi sumber kebenaran tinggi kotak
        # log — bukan height= pada frame anaknya. grid Tkinter hitung
        # tinggi baris dari minsize baris itu sendiri; mengandalkan
        # height= + grid_propagate(False) pada widget anak saja kadang
        # tidak memicu ulang-hitung baris induknya.
        self.body.grid_rowconfigure(1, weight=0, minsize=self.log_height)
        self.body.grid_rowconfigure(2, weight=0)
        self.body.grid_rowconfigure(3, weight=0)
        self.body.grid_rowconfigure(4, weight=0)
        self.body.grid_rowconfigure(5, weight=0)
        self.body.grid_rowconfigure(6, weight=0)
        self.body.grid_rowconfigure(7, weight=0)
        self.body.grid_rowconfigure(8, weight=0)
        self.body.grid_rowconfigure(9, weight=0)
        self.body.grid_columnconfigure(0, weight=1)

        top = tk.Frame(self.body, bg=CARD)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 12))

        tk.Label(top, text="Log", bg=CARD, fg=TEXT,
                 font=(FONT, 14, "bold")).pack(side="left")

        pill = tk.Frame(top, bg=FIELD, padx=10, pady=4)
        pill.pack(side="right")

        self.dot = tk.Canvas(pill, width=9, height=9, bg=FIELD,
                             highlightthickness=0, bd=0)
        self.dot_id = self.dot.create_oval(1, 1, 8, 8, fill=AMBER, outline="")
        self.dot.pack(side="left", padx=(0, 7))

        self.state_label = tk.Label(pill, text="Berhenti", bg=FIELD, fg=AMBER,
                                    font=(FONT, 9, "bold"))
        self.state_label.pack(side="left")

        if display_name:
            tk.Label(top, text=display_name, bg=CARD, fg=MUTED,
                     font=(FONT, 9)).pack(side="right", padx=(0, 10))

        frame = tk.Frame(self.body, bg=FIELD_EDGE, padx=1, pady=1,
                         height=self.log_height)
        frame.grid(row=1, column=0, sticky="nsew")
        frame.grid_propagate(False)  # tinggi ikut self.log_height, bukan isi
        # grid_propagate di atas cuma ngunci frame sebagai SLAVE grid di
        # self.body (grid_rowconfigure minsize) — frame sendiri tidak
        # punya anak grid. Anak "inner"-nya pakai pack, jadi tanpa baris
        # ini frame tetap memantul balik ke ukuran natural Text widget
        # (~240px) tiap kali ditarik lebih kecil, mentok tidak bisa ke
        # bawah 240 walau minsize baris sudah diset lebih kecil.
        frame.pack_propagate(False)
        self.log_frame = frame

        inner = tk.Frame(frame, bg=FIELD)
        inner.pack(fill="both", expand=True)

        # tk.Scrollbar biasa tetap tampil putih/abu bawaan Windows walau
        # bg di-set — pakai style ttk dark yang sudah didaftarkan di
        # __init__ (dipakai bersama dengan scrollbar body).
        scroll = ttk.Scrollbar(inner, orient="vertical",
                               style="DarkField.Vertical.TScrollbar")
        scroll.pack(side="right", fill="y")

        self.log_box = tk.Text(inner, bg=FIELD, fg=TEXT, relief="flat", wrap="word",
                               font=("Consolas", 9), yscrollcommand=scroll.set,
                               state="disabled", padx=10, pady=8,
                               highlightthickness=0, bd=0,
                               selectbackground=ACCENT_DEEP)
        self.log_box.pack(fill="both", expand=True)
        scroll.config(command=self.log_box.yview)

        # Scroll roda mouse selagi kursor DI ATAS kotak log diutamakan
        # buat kotak log itu sendiri dulu (bukan ikut nge-scroll body di
        # belakangnya) — baru kalau log-nya sudah mentok (atas/bawah) dan
        # roda masih diputar ke arah yang sama, barulah body yang di-
        # scroll. Binding ini di widget log_box langsung (bukan bind_all)
        # jadi otomatis TIDAK berlaku kalau kursor di luar kotak log.
        self.log_box.bind("<MouseWheel>", self._on_log_mousewheel)

        self.log_box.tag_config("hit", foreground=GREEN)
        self.log_box.tag_config("skip", foreground=MUTED)
        self.log_box.tag_config("sys", foreground=ACCENT)
        self.log_box.tag_config("err", foreground=RED)

        # Grip buat menarik sisi bawah kotak log — tinggi kotak log jadi
        # bisa diatur sendiri, bukan tetap/otomatis lagi. Dibuat jelas
        # kelihatan (warna terang + menyala saat disentuh kursor) supaya
        # tidak ketinggalan di antara kotak log dan tombol Start.
        grip = tk.Frame(self.body, bg=CARD, height=14,
                        cursor="sb_v_double_arrow")
        grip.grid(row=2, column=0, sticky="ew", pady=(2, 8))
        grip.grid_propagate(False)

        handle = tk.Frame(grip, bg=FIELD_EDGE, height=5, width=70)
        handle.place(relx=0.5, rely=0.5, anchor="center")

        def _grip_hover(_):
            handle.config(bg=ACCENT)

        def _grip_leave(_):
            handle.config(bg=FIELD_EDGE)

        for widget in (grip, handle):
            widget.bind("<Enter>", _grip_hover)
            widget.bind("<Leave>", _grip_leave)
            widget.bind("<ButtonPress-1>", self._on_log_resize_start)
            widget.bind("<B1-Motion>", self._on_log_resize_drag)
            widget.bind("<ButtonRelease-1>", self._on_log_resize_end)

        bar = tk.Frame(self.body, bg=CARD, height=42)
        bar.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        bar.grid_propagate(False)

        self.btn_start = RoundedButton(bar, "Start", self.on_start,
                                       width=130, height=42, fill=GREEN, bg=CARD)
        self.btn_start.pack(side="left")

        self.btn_pause = RoundedButton(bar, "Pause", self.on_pause,
                                       width=130, height=42, fill=AMBER, bg=CARD)
        self.btn_pause.pack(side="right")

        settings_row = tk.Frame(self.body, bg=CARD)
        settings_row.grid(row=4, column=0, sticky="ew", pady=(12, 0))

        lbl_sc_terima = tk.Label(settings_row, text="SHORTCUT TERIMA", bg=CARD,
                                 fg=MUTED, font=(FONT, 7, "bold"))
        lbl_sc_terima.grid(row=0, column=0, sticky="w")
        HoverTip(lbl_sc_terima, TIP_SHORTCUT_TERIMA)
        lbl_sc_tawar = tk.Label(settings_row, text="SHORTCUT TAWAR", bg=CARD,
                                fg=MUTED, font=(FONT, 7, "bold"))
        lbl_sc_tawar.grid(row=0, column=1, sticky="w", padx=(14, 0))
        HoverTip(lbl_sc_tawar, TIP_SHORTCUT_TAWAR)

        # Hotkey tidak lagi diketik manual (rawan salah nama tombol) —
        # klik kotaknya lalu TEKAN tombol yang mau dipakai, otomatis
        # terisi. Lihat _start_hotkey_capture dkk.
        self._pending_hotkey_terima = self.hotkey_terima
        self._pending_hotkey_tawar = self.hotkey_tawar
        self._hotkey_capture_which = None
        self._hotkey_capture_widget = None
        self._hotkey_capture_token = None

        self.hotkey_field_terima = self._build_hotkey_field(
            settings_row, self.hotkey_terima, row=1, col=0, which="terima")
        self.hotkey_field_tawar = self._build_hotkey_field(
            settings_row, self.hotkey_tawar, row=1, col=1, which="tawar")

        RoundedButton(settings_row, "Simpan", self.save_hotkeys, width=90,
                     height=30, fill=ACCENT, bg=CARD).grid(
            row=1, column=2, sticky="w", padx=(14, 0))

        self._build_volume_panel()
        self._build_auto_terima_panel()
        self._build_auto_tawar_panel()
        self._build_notes_row()
        self._build_chrome_row()

        self.refresh_buttons()
        self.footer.config(text="Menunggu pesan berlabel *MASUK*")
        self.log("Login berhasil. Tekan Start untuk mulai memantau.", "sys")

    # ------------------------------------------------ pasang Chrome debug
    def _run_chrome_task(self, button, fn, busy_text, idle_text):
        """Jalankan fn di thread; tombol dikunci selama berjalan supaya
        klik berulang tidak menumpuk (kunci kedua ada di browser_actions:
        port sudah hidup -> tidak membuka lagi)."""
        if not button.enabled:
            return
        button.set_enabled(False)
        button.set_text(busy_text)

        def work():
            try:
                status, msg = fn()
            except Exception as exc:              # noqa: BLE001
                status, msg = "failed", str(exc)
            tag = "hit" if status in ("launched", "created") else (
                "sys" if status in ("already", "exists", "busy") else "err")
            self.ui(self.log, f"[CHROME] {msg}", tag)
            self.ui(button.set_text, idle_text)
            self.ui(button.set_enabled, True)

        threading.Thread(target=work, daemon=True).start()

    def on_setup_chrome_debug(self):
        self._run_chrome_task(self.btn_chrome_setup,
                              self.browser.setup_debug_chrome,
                              "Memproses...", "Pasang / Buka")

    def on_make_chrome_shortcut(self):
        self._run_chrome_task(self.btn_chrome_shortcut,
                              self.browser.create_desktop_shortcut,
                              "Membuat...", "Shortcut Desktop")

    # Tombol modifier yang diabaikan kalau ditekan sendirian saat capture
    # (bukan hotkey yang valid tanpa tombol lain) — tunggu tombol berikutnya.
    HOTKEY_MODIFIER_KEYSYMS = {
        "Shift_L", "Shift_R", "Control_L", "Control_R",
        "Alt_L", "Alt_R", "Super_L", "Super_R",
        "Caps_Lock", "Num_Lock", "Scroll_Lock",
    }

    def _build_hotkey_field(self, parent, initial_value, row, col, which):
        """Kotak kecil mirip field, tapi diklik (bukan diketik) untuk
        mengganti hotkey — klik lalu tekan tombol fisiknya langsung."""
        box = tk.Label(parent, text=initial_value.upper(), bg=FIELD, fg=TEXT,
                       font=(FONT, 10, "bold"), width=8, relief="flat",
                       cursor="hand2", padx=4, pady=5, takefocus=1)
        box.bind("<Button-1>",
                 lambda e, w=which, b=box: self._start_hotkey_capture(w, b))
        box.grid(row=row, column=col, sticky="w", pady=(3, 0))
        return box

    def _start_hotkey_capture(self, which, widget):
        """Dipanggil saat kotak hotkey diklik — masuk mode "menunggu
        tombol".

        Kalau pustaka `keyboard` tersedia, capture dilakukan lewat
        `keyboard.read_hotkey()` (BUKAN event Tkinter biasa) di thread
        terpisah — supaya SEMUA jenis tombol bisa ditangkap dengan andal,
        termasuk F1-F12 dan kombinasi seperti Alt+X. Tombol-tombol itu
        sering tidak nyampe sebagai <KeyPress> Tkinter biasa karena
        Windows menganggapnya "system key" (dipakai untuk buka menu,
        Alt+Tab, dsb — WM_SYSKEYDOWN), jadi bisa kepotong sebelum nyampe
        widget. `keyboard` baca langsung dari level sistem jadi tidak
        kena masalah itu, dan hasilnya (mis. "f2", "alt+x") sudah pas
        format yang sama dipakai keyboard.add_hotkey().

        Shortcut GLOBAL yang SEDANG aktif (lihat register_hotkeys) juga
        dimatikan sementara selama capture — kalau tidak, menekan tombol
        yang kebetulan sama dengan hotkey LAMA (mis. F2) saat memilih
        hotkey baru akan langsung memicu aksi Terima/Tawar sungguhan.
        Dihidupkan lagi (pakai hotkey yang masih TERSIMPAN sekarang,
        bukan yang baru dipilih — itu baru berlaku setelah klik Simpan)
        begitu capture selesai, baik berhasil, dibatalkan (Escape), atau
        kotaknya kehilangan fokus (mode fallback saja, lihat bawah)."""
        if self._hotkey_capture_widget is not None:
            self._cancel_hotkey_capture()  # batalkan capture lain yg nyangkut
        self._hotkey_capture_which = which
        self._hotkey_capture_widget = widget
        widget.configure(text="Tekan tombol...", fg=ACCENT)
        widget.focus_set()

        if keyboard is not None and self._hotkeys_registered:
            try:
                keyboard.unhook_all_hotkeys()
            except Exception:                      # noqa: BLE001
                pass
            self._hotkeys_registered = False

        if keyboard is not None:
            token = object()
            self._hotkey_capture_token = token
            threading.Thread(
                target=self._capture_hotkey_via_keyboard_lib,
                args=(token,), daemon=True,
            ).start()
        else:
            # Fallback kalau pustaka `keyboard` tidak terpasang sama
            # sekali — tetap jalan untuk tombol biasa lewat event
            # Tkinter, walau F1-F12/kombinasi Alt mungkin tidak selalu
            # tertangkap (keterbatasan Tkinter, bukan bug di sini).
            widget.bind("<KeyPress>", self._on_hotkey_capture_keypress)
            widget.bind("<FocusOut>", lambda e: self._cancel_hotkey_capture())

    def _capture_hotkey_via_keyboard_lib(self, token):
        """Jalan di thread terpisah (blocking) — menunggu SATU kombinasi
        tombol ditekan di mana saja, lalu kembali ke thread Tkinter lewat
        self.after() untuk menerapkannya (Tkinter tidak boleh disentuh
        dari luar main thread)."""
        try:
            combo = keyboard.read_hotkey(suppress=False)
        except Exception:                          # noqa: BLE001
            combo = None
        self.after(0, lambda: self._apply_captured_hotkey(token, combo))

    def _apply_captured_hotkey(self, token, combo):
        # Capture ini sudah dibatalkan/ditimpa capture lain sebelum
        # tombolnya sempat ditekan — abaikan hasilnya.
        if token is not self._hotkey_capture_token:
            return
        widget = self._hotkey_capture_widget
        if widget is None or not widget.winfo_exists():
            return
        if not combo or combo.lower() in ("esc", "escape"):
            self._cancel_hotkey_capture()  # batal, balik ke nilai lama
            return
        key_name = combo.lower()
        which = self._hotkey_capture_which
        if which == "terima":
            self._pending_hotkey_terima = key_name
        else:
            self._pending_hotkey_tawar = key_name
        widget.configure(text=key_name.upper(), fg=TEXT)
        self._finish_hotkey_capture()
        self._log_hotkey_captured(which, key_name)

    def _log_hotkey_captured(self, which, key_name):
        nama = "Terima" if which == "terima" else "Tawar"
        self.log(f"[SYS] Key sudah ditetapkan yaitu {key_name.upper()} "
                 f"(Shortcut {nama}). Klik Simpan untuk menerapkan.", "sys")

    def _on_hotkey_capture_keypress(self, event):
        """Jalur fallback (lihat _start_hotkey_capture) — dipakai HANYA
        kalau pustaka `keyboard` tidak terpasang."""
        keysym = event.keysym
        if keysym in self.HOTKEY_MODIFIER_KEYSYMS:
            return  # modifier sendirian — terus tunggu tombol berikutnya
        if keysym == "Escape":
            self._cancel_hotkey_capture()  # batal, balik ke nilai lama
            return
        # Keysym Tkinter untuk huruf ikut status Shift (mis. "A" kalau
        # Shift ditekan) — di-lower supaya konsisten dengan format yang
        # dipakai pustaka `keyboard` (huruf kecil semua, mis. "f2", "a").
        key_name = keysym.lower()
        which = self._hotkey_capture_which
        widget = self._hotkey_capture_widget
        if which == "terima":
            self._pending_hotkey_terima = key_name
        else:
            self._pending_hotkey_tawar = key_name
        widget.configure(text=key_name.upper(), fg=TEXT)
        self._finish_hotkey_capture()
        self._log_hotkey_captured(which, key_name)

    def _cancel_hotkey_capture(self):
        widget = self._hotkey_capture_widget
        if widget is None:
            return
        current = (self._pending_hotkey_terima
                   if self._hotkey_capture_which == "terima"
                   else self._pending_hotkey_tawar)
        widget.configure(text=current.upper(), fg=TEXT)
        self._finish_hotkey_capture()

    def _finish_hotkey_capture(self):
        widget = self._hotkey_capture_widget
        # token dibatalkan duluan supaya hasil thread capture yang
        # mungkin masih "ngambang" (menunggu tombol) diabaikan kalau
        # baru selesai belakangan.
        self._hotkey_capture_token = None
        widget.unbind("<KeyPress>")
        widget.unbind("<FocusOut>")
        self._hotkey_capture_widget = None
        self._hotkey_capture_which = None

        # Hidupkan lagi shortcut global yang dimatikan sementara di
        # _start_hotkey_capture — pakai hotkey yang masih TERSIMPAN
        # sekarang (self.hotkey_terima/tawar), bukan yang baru saja
        # ditangkap (itu baru efektif setelah klik Simpan -> save_hotkeys).
        # announce=False: pengaktifan ulang ini senyap, supaya log tidak
        # menampilkan shortcut LAMA yang membingungkan. Log "Shortcut
        # aktif" baru muncul setelah klik Simpan (save_hotkeys).
        if keyboard is not None:
            self.register_hotkeys(announce=False)

    # ------------------------------------------------------ volume notifikasi
    def _build_volume_panel(self):
        """Slider volume notif.wav. Efeknya lewat winmm (lihat
        _set_wave_volume) — di Windows modern ini cuma mengatur volume
        sesi aplikasi ini sendiri di Volume Mixer, tidak mengubah volume
        sistem/aplikasi lain."""
        row = tk.Frame(self.body, bg=CARD)
        row.grid(row=5, column=0, sticky="ew", pady=(12, 0))

        lbl_vol = tk.Label(row, text="VOLUME NOTIFIKASI", bg=CARD, fg=MUTED,
                           font=(FONT, 7, "bold"))
        lbl_vol.pack(side="left", padx=(0, 10))
        HoverTip(lbl_vol, TIP_VOLUME)

        self.volume_var = tk.IntVar(value=int(self.sound_volume))
        self.scale_volume = ttk.Scale(
            row, from_=0, to=100, orient="horizontal", length=160,
            variable=self.volume_var, style="Dark.Horizontal.TScale",
            command=self._on_volume_change,
        )
        self.scale_volume.pack(side="left")

        self.lbl_volume_pct = tk.Label(row, text=f"{int(self.sound_volume)}%",
                                       bg=CARD, fg=TEXT, font=(FONT, 9, "bold"),
                                       width=4, anchor="w")
        self.lbl_volume_pct.pack(side="left", padx=(8, 10))

        RoundedButton(row, "Tes", self.play_sound, width=64, height=28,
                     fill=ACCENT, bg=CARD, subtle=False).pack(side="left")

    def _on_volume_change(self, value):
        pct = int(float(value))
        self.sound_volume = pct
        if getattr(self, "lbl_volume_pct", None) and self.lbl_volume_pct.winfo_exists():
            self.lbl_volume_pct.config(text=f"{pct}%")
        self._set_wave_volume(pct)
        # Debounce penulisan config.json supaya tidak nulis file berkali-
        # kali tiap slider digeser sedikit — cukup ~0.4 detik setelah
        # geseran terakhir berhenti.
        if self._volume_save_job is not None:
            self.after_cancel(self._volume_save_job)
        self._volume_save_job = self.after(400, self.save_sound_volume)

    def _set_wave_volume(self, percent):
        """Atur volume output 'wave' (dipakai winsound.PlaySound) lewat API
        winmm. Di Windows Vista ke atas, winmm divirtualkan per-proses
        lewat Session API, jadi ini efeknya ke channel aplikasi ini saja
        di Volume Mixer — bukan volume sistem keseluruhan. Best-effort,
        dibungkus try/except penuh seperti fitur dark-titlebar."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            level = max(0, min(100, int(percent)))
            raw = int(level * 0xFFFF / 100)
            packed = (raw & 0xFFFF) | ((raw & 0xFFFF) << 16)
            ctypes.windll.winmm.waveOutSetVolume(0, packed)
        except Exception:                              # noqa: BLE001
            pass

    def load_sound_volume(self):
        data = self._read_raw_config()
        try:
            return max(0, min(100, int(data.get("sound_volume", 80))))
        except (TypeError, ValueError):
            return 80

    def save_sound_volume(self):
        self._volume_save_job = None
        data = self._read_raw_config()
        data["sound_volume"] = int(self.sound_volume)
        self._write_raw_config(data)

    # Jenis yang didukung Auto Terima. "Fix File" (dengan spasi) dipakai
    # supaya sama persis dengan info["jenis"] yang dikembalikan
    # browser_actions (COLLECTION_FIELDS) — jadi tidak perlu mapping lagi.
    JENIS_LIST = ("Parafrase", "Humanizer", "Fix File")

    # 6 checkbox layanan Fix File, dipakai untuk "service yang dipilih"
    # (include) maupun "service yang dihindari" (exclude). Label dibuat
    # sama dengan string yang dikirim API (field "service", dipisah "|")
    # supaya cocok langsung saat dibandingkan dengan service_list job.
    FIXFILE_SERVICES = (
        "Perbaikan Penomoran halaman",
        "Perbaikan format heading",
        "Daftar isi",
        "Daftar tabel, gambar, dan lainnya",
        "Daftar Pustaka",
        "Perbaikan typo",
    )

    def _build_criteria_flow(self, sub, field_defs, entries_out, row, tips=None):
        """Field-field syarat (Target min, Client min, Deadline, Kata,
        dst) disusun TETAP SEBARIS ke kanan selama masih muat di lebar
        jendela yang tersedia (persis seperti aslinya) — begitu jendela
        disempitkan sampai tidak muat lagi, field yang kelebihan otomatis
        "melipat" turun ke baris baru sendiri-sendiri, jadi tidak pernah
        kepotong dan tidak perlu discroll ke samping. Full-screen / cukup
        lebar -> tetap satu baris penuh ke kanan seperti biasa.

        `field_defs` = list (key, label, value) berurutan. Baris field
        ditaruh sebagai satu frame "flow" yang mengisi grid row=`row` di
        `sub` (columnspan=2). Return baris kosong berikutnya di `sub`
        (dipakai pemanggil untuk elemen lain setelahnya, mis. checkbox
        Target Tawar / service Fix File)."""
        flow = tk.Frame(sub, bg=CARD)
        flow.grid(row=row, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 6))
        flow.grid_propagate(False)
        flow.configure(height=1)

        items = []
        for key, label, value in field_defs:
            wrapper = tk.Frame(flow, bg=CARD)
            title = tk.Label(wrapper, text=label.upper(), bg=CARD, fg=MUTED,
                             font=(FONT, 7, "bold"), anchor="w")
            title.pack(anchor="w")
            if tips and key in tips:
                HoverTip(title, tips[key])
            entry = tk.Entry(wrapper, bg=FIELD, fg=TEXT, relief="flat",
                             width=8, font=(FONT, 10), insertbackground=ACCENT)
            entry.insert(0, "" if value is None else str(value))
            entry.pack(anchor="w", ipady=3, pady=(2, 0))
            entries_out[key] = entry
            items.append(wrapper)

        last_h = {"v": -1}

        def reflow(avail):
            if avail <= 1 or not items:
                return
            x = y = row_h = 0
            gap_x, gap_y = 18, 8
            for i, w in enumerate(items):
                w.update_idletasks()
                rw = w.winfo_reqwidth()
                rh = w.winfo_reqheight()
                if x > 0 and x + rw > avail:
                    x = 0
                    y += row_h + gap_y
                    row_h = 0
                w.place(x=x, y=y, width=rw, height=rh)
                x += rw + gap_x
                row_h = max(row_h, rh)
            new_h = y + row_h
            if new_h != last_h["v"]:
                last_h["v"] = new_h
                flow.configure(height=new_h)

        flow.bind("<Configure>", lambda e: reflow(e.width))
        return row + 1

    def _build_auto_terima_panel(self):
        """Panel syarat Auto Terima. Jenis sekarang multiselect (checkbox),
        tiap jenis yang dicentang memunculkan panel syaratnya sendiri-
        sendiri ("dipisahkan berdasarkan servicenya"). Semua panel
        tersembunyi total kalau Auto Terima dimatikan."""
        panel = tk.Frame(self.body, bg=CARD)
        panel.grid(row=6, column=0, sticky="ew", pady=(14, 0))
        panel.grid_columnconfigure(0, weight=1)

        tk.Label(panel, text="AUTO TERIMA", bg=CARD, fg=MUTED,
                 font=(FONT, 7, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4))

        toggle_row = tk.Frame(panel, bg=CARD)
        toggle_row.grid(row=1, column=0, sticky="w")

        self.auto_enabled_var = tk.BooleanVar(value=self.auto_enabled)
        tk.Checkbutton(
            toggle_row, text="Aktifkan", variable=self.auto_enabled_var,
            command=self._refresh_auto_visibility,
            bg=CARD, fg=TEXT, selectcolor=FIELD, activebackground=CARD,
            activeforeground=TEXT, font=(FONT, 9), anchor="w",
        ).pack(side="left")
        InfoTip(toggle_row, TIP_AUTO_TERIMA).pack(side="left", padx=(0, 10))

        # Dibungkus dalam satu frame supaya bisa disembunyikan sekaligus
        # (pack_forget) kalau "Aktifkan" dimatikan — lihat
        # _refresh_auto_visibility().
        self.auto_delay_box = tk.Frame(toggle_row, bg=CARD)

        lbl_delay = tk.Label(self.auto_delay_box, text="Delay (ms)", bg=CARD,
                             fg=MUTED, font=(FONT, 8))
        lbl_delay.pack(side="left", padx=(14, 4))
        HoverTip(lbl_delay, TIP_DELAY_TERIMA)

        self.e_auto_delay_min = tk.Entry(
            self.auto_delay_box, bg=FIELD, fg=TEXT, relief="flat", width=5,
            font=(FONT, 10), insertbackground=ACCENT,
        )
        self.e_auto_delay_min.insert(0, str(int(self.auto_delay_min_ms)))
        self.e_auto_delay_min.pack(side="left", ipady=3)

        tk.Label(self.auto_delay_box, text="-", bg=CARD, fg=MUTED,
                 font=(FONT, 9)).pack(side="left", padx=3)

        self.e_auto_delay_max = tk.Entry(
            self.auto_delay_box, bg=FIELD, fg=TEXT, relief="flat", width=5,
            font=(FONT, 10), insertbackground=ACCENT,
        )
        self.e_auto_delay_max.insert(0, str(int(self.auto_delay_max_ms)))
        self.e_auto_delay_max.pack(side="left", ipady=3)

        self.auto_delay_box.pack(side="left")

        self.auto_simpan_btn = RoundedButton(
            toggle_row, "Simpan", self.save_auto_terima_form,
            width=90, height=30, fill=ACCENT, bg=CARD)
        self.auto_simpan_btn.pack(side="left", padx=(14, 0))

        # --- baris checkbox pemilihan jenis (multiselect) ---
        self.auto_jenis_row = tk.Frame(panel, bg=CARD)
        jenis_row = self.auto_jenis_row
        jenis_row.grid(row=2, column=0, sticky="w", pady=(10, 0))

        tk.Label(jenis_row, text="Jenis:", bg=CARD, fg=MUTED,
                 font=(FONT, 8)).pack(side="left", padx=(0, 8))

        self.auto_jenis_vars = {}
        for jenis in self.JENIS_LIST:
            var = tk.BooleanVar(
                value=self.auto_jenis_settings.get(jenis, {}).get("enabled", False)
            )
            self.auto_jenis_vars[jenis] = var
            tk.Checkbutton(
                jenis_row, text=jenis, variable=var,
                command=self._refresh_auto_visibility,
                bg=CARD, fg=TEXT, selectcolor=FIELD, activebackground=CARD,
                activeforeground=TEXT, font=(FONT, 9), anchor="w",
            ).pack(side="left")
            InfoTip(jenis_row, tip_jenis("Auto Terima", jenis)).pack(
                side="left", padx=(2, 12))

        # --- panel-panel syarat, satu per jenis, dikumpulkan dalam satu
        # container yang disembunyikan total kalau "Aktifkan" dimatikan ---
        self.auto_fields_frame = tk.Frame(panel, bg=CARD)
        self.auto_fields_frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.auto_fields_frame.grid_columnconfigure(0, weight=1)

        self.auto_entries = {}             # {jenis: {field_key: Entry}}
        self.auto_jenis_panels = {}        # {jenis: Frame}
        self.auto_fixfile_include_vars = {}
        self.auto_fixfile_exclude_vars = {}

        row_i = 0
        for jenis in self.JENIS_LIST:
            sub = tk.Frame(self.auto_fields_frame, bg=CARD,
                           highlightbackground=FIELD_EDGE, highlightthickness=1)
            sub.grid(row=row_i, column=0, sticky="ew", pady=(0, 10),
                     ipadx=8, ipady=8)
            sub.grid_columnconfigure(0, weight=1)
            self.auto_jenis_panels[jenis] = sub
            row_i += 1

            tk.Label(sub, text=jenis.upper(), bg=CARD, fg=ACCENT,
                     font=(FONT, 8, "bold")).grid(
                row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 4))

            settings = self.auto_jenis_settings.get(jenis, {})
            self.auto_entries[jenis] = {}

            if jenis in ("Parafrase", "Humanizer"):
                # Target min artinya target HARUS DI ATAS angka ini (bukan
                # minimal termasuk) — isi 15 berarti target >15 yang lolos.
                # Sebaris ke kanan selama muat; otomatis melipat turun
                # per field kalau jendela terlalu sempit (lihat
                # _build_criteria_flow).
                field_defs = [
                    ("min_target", "Target min (%)", settings.get("min_target")),
                    ("min_client_pct", "Client min (%)", settings.get("min_client_pct")),
                    ("min_client_price", "Harga Client min (Rp)", settings.get("min_client_price")),
                    ("deadline_min_h", "Deadline min (jam)", settings.get("deadline_min_h")),
                    ("deadline_max_h", "Deadline max (jam)", settings.get("deadline_max_h")),
                ]
                if jenis == "Humanizer":
                    field_defs.append(
                        ("min_word_count", "Kata min", settings.get("min_word_count")))
                    field_defs.append(
                        ("max_word_count", "Kata max", settings.get("max_word_count")))
                self._build_criteria_flow(sub, field_defs, self.auto_entries[jenis],
                                          row=1, tips=TIPS_TERIMA)

            else:  # Fix File — tidak ada Target Persentase, diganti syarat
                   # perbandingan harga + deadline + pilihan layanan.
                field_defs = [
                    ("min_client_pct", "Client min (%)", settings.get("min_client_pct")),
                    ("min_client_price", "Harga Client min (Rp)", settings.get("min_client_price")),
                    ("deadline_min_h", "Deadline min (jam)", settings.get("deadline_min_h")),
                    ("deadline_max_h", "Deadline max (jam)", settings.get("deadline_max_h")),
                ]
                next_row = self._build_criteria_flow(
                    sub, field_defs, self.auto_entries[jenis], row=1,
                    tips=TIPS_TERIMA)

                # Grid (bukan pack side=left) supaya dua kotak ini benar2
                # berbagi lebar penuh "sub" secara merata dan tidak
                # mengumpul/mepet di kiri saat panel melebar.
                service_row = tk.Frame(sub, bg=CARD)
                service_row.grid(row=next_row, column=0, columnspan=2, sticky="ew",
                                 padx=8, pady=(10, 0))
                service_row.grid_columnconfigure(0, weight=1, uniform="svc")
                service_row.grid_columnconfigure(1, weight=1, uniform="svc")

                include_box = tk.Frame(service_row, bg=CARD)
                include_box.grid(row=0, column=0, sticky="new", padx=(0, 20))
                lbl_inc = tk.Label(include_box, text="SERVICE YANG DIPILIH",
                                   bg=CARD, fg=MUTED, font=(FONT, 7, "bold"),
                                   anchor="w")
                lbl_inc.pack(anchor="w", pady=(0, 2))
                HoverTip(lbl_inc, TIP_SERVICE_INCLUDE)
                tk.Label(include_box, text="(job boleh punya layanan lain juga)",
                         bg=CARD, fg=MUTED, font=(FONT, 7), anchor="w").pack(
                    anchor="w", pady=(0, 6))
                include_vals = set(settings.get("service_include") or [])
                for svc in self.FIXFILE_SERVICES:
                    var = tk.BooleanVar(value=svc in include_vals)
                    self.auto_fixfile_include_vars[svc] = var
                    tk.Checkbutton(
                        include_box, text=svc, variable=var,
                        bg=CARD, fg=TEXT, selectcolor=FIELD, activebackground=CARD,
                        activeforeground=TEXT, font=(FONT, 8), anchor="w",
                        wraplength=280, justify="left",
                    ).pack(anchor="w", fill="x", pady=1)

                exclude_box = tk.Frame(service_row, bg=CARD)
                exclude_box.grid(row=0, column=1, sticky="new")
                lbl_exc = tk.Label(exclude_box, text="SERVICE YANG DIHINDARI",
                                   bg=CARD, fg=MUTED, font=(FONT, 7, "bold"),
                                   anchor="w")
                lbl_exc.pack(anchor="w", pady=(0, 2))
                HoverTip(lbl_exc, TIP_SERVICE_EXCLUDE)
                tk.Label(exclude_box, text="(ada salah satu ini -> ditolak)",
                         bg=CARD, fg=MUTED, font=(FONT, 7), anchor="w").pack(
                    anchor="w", pady=(0, 6))
                exclude_vals = set(settings.get("service_exclude") or [])
                for svc in self.FIXFILE_SERVICES:
                    var = tk.BooleanVar(value=svc in exclude_vals)
                    self.auto_fixfile_exclude_vars[svc] = var
                    tk.Checkbutton(
                        exclude_box, text=svc, variable=var,
                        bg=CARD, fg=TEXT, selectcolor=FIELD, activebackground=CARD,
                        activeforeground=TEXT, font=(FONT, 8), anchor="w",
                        wraplength=280, justify="left",
                    ).pack(anchor="w", fill="x", pady=1)

        self._refresh_auto_visibility()

    def _refresh_auto_visibility(self):
        """Semua panel syarat, baris "Jenis:", dan field Delay tersembunyi
        kalau Auto Terima dimatikan. Kalau aktif, hanya panel jenis yang
        dicentang yang ditampilkan — bisa lebih dari satu sekaligus
        (multiselect)."""
        if not self.auto_enabled_var.get():
            self.auto_delay_box.pack_forget()
            self.auto_jenis_row.grid_remove()
            self.auto_fields_frame.grid_remove()
            return
        if not self.auto_delay_box.winfo_ismapped():
            self.auto_delay_box.pack(side="left", before=self.auto_simpan_btn)
        self.auto_jenis_row.grid()
        self.auto_fields_frame.grid()

        for jenis, sub in self.auto_jenis_panels.items():
            if self.auto_jenis_vars[jenis].get():
                sub.grid()
            else:
                sub.grid_remove()

    def save_auto_terima_form(self):
        def read(entry, default=0.0, allow_blank=False):
            txt = entry.get().strip()
            if not txt:
                return None if allow_blank else default
            return float(txt)

        try:
            self.auto_enabled = bool(self.auto_enabled_var.get())
            self.auto_delay_min_ms = float(self.e_auto_delay_min.get().strip() or 0)
            self.auto_delay_max_ms = float(self.e_auto_delay_max.get().strip() or 0)

            new_settings = {}
            for jenis in self.JENIS_LIST:
                entries = self.auto_entries[jenis]
                enabled = bool(self.auto_jenis_vars[jenis].get())

                if jenis in ("Parafrase", "Humanizer"):
                    s = {
                        "enabled": enabled,
                        "min_target": read(entries["min_target"]),
                        "min_client_pct": read(entries["min_client_pct"]),
                        "min_client_price": read(entries["min_client_price"],
                                                 allow_blank=True),
                        "deadline_min_h": read(entries["deadline_min_h"]),
                        "deadline_max_h": read(entries["deadline_max_h"]),
                    }
                    if jenis == "Humanizer":
                        s["min_word_count"] = read(entries["min_word_count"],
                                                   allow_blank=True)
                        s["max_word_count"] = read(entries["max_word_count"],
                                                   allow_blank=True)
                else:  # Fix File
                    s = {
                        "enabled": enabled,
                        "min_client_pct": read(entries["min_client_pct"]),
                        "min_client_price": read(entries["min_client_price"],
                                                 allow_blank=True),
                        "deadline_min_h": read(entries["deadline_min_h"]),
                        "deadline_max_h": read(entries["deadline_max_h"]),
                        "service_include": [
                            svc for svc in self.FIXFILE_SERVICES
                            if self.auto_fixfile_include_vars[svc].get()
                        ],
                        "service_exclude": [
                            svc for svc in self.FIXFILE_SERVICES
                            if self.auto_fixfile_exclude_vars[svc].get()
                        ],
                    }
                new_settings[jenis] = s
            self.auto_jenis_settings = new_settings
        except ValueError:
            self.log("[SYS] Nilai syarat Auto Terima tidak valid — pakai angka.",
                     "err")
            return

        self.save_auto_settings()
        aktif = [j for j in self.JENIS_LIST
                if self.auto_jenis_settings[j]["enabled"]]
        status = "AKTIF" if self.auto_enabled else "NONAKTIF"
        self.log(f"[SYS] Syarat Auto Terima disimpan ({status}, jenis aktif: "
                 f"{', '.join(aktif) if aktif else '-'}).", "sys")

    def _build_target_tawar_row(self, sub, jenis, settings, row):
        """Baris checkbox "Target Tawar (%)" per jenis di panel Auto
        Tawar. Kalau DICENTANG, kolom persentase muncul dan harga yang
        ditawar dihitung sebagai persentase dari Harga ESTIMASI (lihat
        compute_tawar_price_from_client di browser_actions.py — nama
        fungsinya tetap begitu karena rumusnya generik, tapi angka yang
        dioper sekarang Harga Estimasi, bukan Harga Client). Kalau TIDAK
        dicentang, kolom disembunyikan dan logika lama tetap dipakai:
        harga dihitung dari potongan bracket Harga Estimasi
        (compute_tawar_price).

        Di bawahnya ada baris kedua, "Tawar Rentang Harga" — cuma
        relevan (dan cuma ditampilkan) kalau "Target Tawar (%)" di atas
        aktif. Dipakai untuk job yang Harga Estimasi-nya KECIL tapi
        tetap mau ditawar jadi angka yang pantas: kalau dicentang dan
        Harga Estimasi job jatuh di antara Harga Min-Max di sini, DASAR
        perhitungan persentasenya dinaikkan jadi Harga Max (bukan Harga
        Estimasi asli yang kecil) sebelum dikali persentase — jadi
        persentase >100% bisa dipakai buat memastikan hasil tawarnya
        tetap di ATAS Harga Max itu walau Harga Estimasi aslinya jauh di
        bawahnya. Lihat _trigger_tawar untuk penerapannya.

        Return baris kosong berikutnya di `sub` (dipakai pemanggil kalau
        ada elemen lain setelahnya, mis. baris checkbox service Fix
        File) — fungsi ini SELALU memakai 2 baris grid (row & row+1),
        baris kedua itu cuma kosong kalau checkbox-nya tidak dicentang."""
        row_frame = tk.Frame(sub, bg=CARD)
        row_frame.grid(row=row, column=0, columnspan=6, sticky="w",
                        padx=8, pady=(10, 0))

        var = tk.BooleanVar(value=bool(settings.get("use_target_tawar")))
        self.auto_tawar_use_pct_vars[jenis] = var
        tk.Checkbutton(
            row_frame, text="Target Tawar (%) dari Harga Estimasi",
            variable=var,
            command=lambda j=jenis: self._refresh_target_tawar_field(j),
            bg=CARD, fg=TEXT, selectcolor=FIELD, activebackground=CARD,
            activeforeground=TEXT, font=(FONT, 8), anchor="w",
        ).pack(side="left")
        InfoTip(row_frame, TIP_TARGET_TAWAR).pack(side="left", padx=(2, 0))

        wrap = tk.Frame(row_frame, bg=CARD)
        pct_value = settings.get("target_tawar_pct")
        entry = tk.Entry(wrap, bg=FIELD, fg=TEXT, relief="flat", width=6,
                         font=(FONT, 10), insertbackground=ACCENT)
        entry.insert(0, "" if pct_value is None else str(pct_value))
        entry.pack(side="left", ipady=3)
        tk.Label(wrap, text="% (boleh >100)", bg=CARD, fg=MUTED,
                 font=(FONT, 8)).pack(side="left", padx=(3, 0))
        self.auto_tawar_pct_entries[jenis] = entry
        self.auto_tawar_pct_wraps[jenis] = wrap
        if var.get():
            wrap.pack(side="left", padx=(10, 0))

        # --- baris kedua: "Tawar Rentang Harga" (nested di bawah) ---
        range_row = tk.Frame(sub, bg=CARD)
        self.auto_tawar_range_rows[jenis] = range_row

        range_var = tk.BooleanVar(value=bool(settings.get("use_price_range")))
        self.auto_tawar_use_range_vars[jenis] = range_var
        tk.Checkbutton(
            range_row, text="Tawar Rentang Harga",
            variable=range_var,
            command=lambda j=jenis: self._refresh_price_range_field(j),
            bg=CARD, fg=TEXT, selectcolor=FIELD, activebackground=CARD,
            activeforeground=TEXT, font=(FONT, 8), anchor="w",
        ).pack(side="left")
        InfoTip(range_row, TIP_RANGE).pack(side="left", padx=(2, 0))

        range_wrap = tk.Frame(range_row, bg=CARD)
        rmin_val = settings.get("price_range_min")
        rmin_entry = tk.Entry(range_wrap, bg=FIELD, fg=TEXT, relief="flat",
                              width=9, font=(FONT, 10), insertbackground=ACCENT)
        rmin_entry.insert(0, "" if rmin_val is None else str(rmin_val))
        rmin_entry.pack(side="left", ipady=3)
        tk.Label(range_wrap, text="-", bg=CARD, fg=MUTED,
                 font=(FONT, 9)).pack(side="left", padx=4)
        rmax_val = settings.get("price_range_max")
        rmax_entry = tk.Entry(range_wrap, bg=FIELD, fg=TEXT, relief="flat",
                              width=9, font=(FONT, 10), insertbackground=ACCENT)
        rmax_entry.insert(0, "" if rmax_val is None else str(rmax_val))
        rmax_entry.pack(side="left", ipady=3)
        tk.Label(range_wrap, text="Rp (dasar % = Harga Max)",
                 bg=CARD, fg=MUTED, font=(FONT, 8)).pack(
            side="left", padx=(4, 0))
        self.auto_tawar_range_min_entries[jenis] = rmin_entry
        self.auto_tawar_range_max_entries[jenis] = rmax_entry
        self.auto_tawar_range_wraps[jenis] = range_wrap
        if range_var.get():
            range_wrap.pack(side="left", padx=(10, 0))

        # --- baris ketiga: perilaku DI LUAR rentang (nested di bawah) ---
        outside_row = tk.Frame(sub, bg=CARD)
        self.auto_tawar_outside_rows[jenis] = outside_row
        outside_var = tk.BooleanVar(
            value=bool(settings.get("outside_range_default")))
        self.auto_tawar_outside_vars[jenis] = outside_var
        tk.Checkbutton(
            outside_row,
            text="Di luar rentang: tawar default (di bawah Harga Estimasi)",
            variable=outside_var,
            bg=CARD, fg=TEXT, selectcolor=FIELD, activebackground=CARD,
            activeforeground=TEXT, font=(FONT, 8), anchor="w",
        ).pack(side="left")
        InfoTip(outside_row, TIP_OUTSIDE).pack(side="left", padx=(2, 0))

        self.auto_tawar_row_index[jenis] = row
        self._refresh_tawar_rows(jenis)

        # --- baris keempat (selalu tampil): pengaturan tawar default ---
        floor_row = tk.Frame(sub, bg=CARD)
        floor_row.grid(row=row + 3, column=0, columnspan=6, sticky="w",
                       padx=8, pady=(10, 0))
        floor_var = tk.BooleanVar(value=bool(settings.get("use_default_floor")))
        self.auto_tawar_floor_vars[jenis] = floor_var
        tk.Checkbutton(
            floor_row, text="Tawar default minimal", variable=floor_var,
            bg=CARD, fg=TEXT, selectcolor=FIELD, activebackground=CARD,
            activeforeground=TEXT, font=(FONT, 8), anchor="w",
        ).pack(side="left")
        InfoTip(floor_row, TIP_DEFAULT_FLOOR).pack(side="left", padx=(2, 8))
        floor_entry = tk.Entry(floor_row, bg=FIELD, fg=TEXT, relief="flat",
                               width=9, font=(FONT, 10),
                               insertbackground=ACCENT)
        floor_val = settings.get("default_floor_price")
        floor_entry.insert(0, "" if floor_val is None else str(floor_val))
        floor_entry.pack(side="left", ipady=3)
        tk.Label(floor_row, text="Rp", bg=CARD, fg=MUTED,
                 font=(FONT, 8)).pack(side="left", padx=(4, 0))
        self.auto_tawar_floor_entries[jenis] = floor_entry

        return row + 4

    def _refresh_tawar_rows(self, jenis):
        """Atur tampil/sembunyi tiga lapis checkbox Tawar per jenis:
        kolom persentase (kalau "Target Tawar (%)" dicentang), baris
        "Tawar Rentang Harga" (cuma kalau persentase aktif), dan baris
        "Di luar rentang: tawar default" (cuma kalau rentang harga aktif
        juga). Centang yang disembunyikan TIDAK dihapus, supaya pilihan
        user tetap ada kalau diaktifkan lagi."""
        wrap = self.auto_tawar_pct_wraps.get(jenis)
        range_row = self.auto_tawar_range_rows.get(jenis)
        range_wrap = self.auto_tawar_range_wraps.get(jenis)
        outside_row = self.auto_tawar_outside_rows.get(jenis)
        base = self.auto_tawar_row_index.get(jenis)
        if None in (wrap, range_row, range_wrap, outside_row, base):
            return
        pct_on = bool(self.auto_tawar_use_pct_vars[jenis].get())
        range_on = bool(self.auto_tawar_use_range_vars[jenis].get())

        if pct_on:
            wrap.pack(side="left", padx=(10, 0))
        else:
            wrap.pack_forget()

        if range_on:
            range_wrap.pack(side="left", padx=(10, 0))
        else:
            range_wrap.pack_forget()

        if pct_on:
            range_row.grid(row=base + 1, column=0, columnspan=6, sticky="w",
                           padx=8, pady=(6, 0))
        else:
            range_row.grid_remove()

        if pct_on and range_on:
            outside_row.grid(row=base + 2, column=0, columnspan=6, sticky="w",
                             padx=8, pady=(4, 0))
        else:
            outside_row.grid_remove()

    def _refresh_target_tawar_field(self, jenis):
        self._refresh_tawar_rows(jenis)

    def _refresh_price_range_field(self, jenis):
        self._refresh_tawar_rows(jenis)

    # ------------------------------------------------------ Auto Tawar
    # Fitur TERPISAH dari Auto Terima di atas — panel, syarat per jenis,
    # dan delay-nya sendiri-sendiri, supaya bisa diisi beda (mis. Auto
    # Terima baru jalan di atas 70% harga, Auto Tawar jadi fallback untuk
    # job di bawah itu yang masih worth ditawar). Struktur & pola kodenya
    # sengaja dibuat sama persis dengan panel Auto Terima supaya familiar.
    def _build_auto_tawar_panel(self):
        panel = tk.Frame(self.body, bg=CARD)
        panel.grid(row=7, column=0, sticky="ew", pady=(14, 0))
        panel.grid_columnconfigure(0, weight=1)

        tk.Label(panel, text="AUTO TAWAR", bg=CARD, fg=MUTED,
                 font=(FONT, 7, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4))

        toggle_row = tk.Frame(panel, bg=CARD)
        toggle_row.grid(row=1, column=0, sticky="w")

        self.auto_tawar_enabled_var = tk.BooleanVar(value=self.auto_tawar_enabled)
        tk.Checkbutton(
            toggle_row, text="Aktifkan", variable=self.auto_tawar_enabled_var,
            command=self._refresh_auto_tawar_visibility,
            bg=CARD, fg=TEXT, selectcolor=FIELD, activebackground=CARD,
            activeforeground=TEXT, font=(FONT, 9), anchor="w",
        ).pack(side="left")
        InfoTip(toggle_row, TIP_AUTO_TAWAR).pack(side="left", padx=(0, 10))

        self.auto_tawar_delay_box = tk.Frame(toggle_row, bg=CARD)

        lbl_delay_tawar = tk.Label(self.auto_tawar_delay_box, text="Delay (ms)",
                                   bg=CARD, fg=MUTED, font=(FONT, 8))
        lbl_delay_tawar.pack(side="left", padx=(14, 4))
        HoverTip(lbl_delay_tawar, TIP_DELAY_TAWAR)

        self.e_auto_tawar_delay_min = tk.Entry(
            self.auto_tawar_delay_box, bg=FIELD, fg=TEXT, relief="flat", width=5,
            font=(FONT, 10), insertbackground=ACCENT,
        )
        self.e_auto_tawar_delay_min.insert(0, str(int(self.auto_tawar_delay_min_ms)))
        self.e_auto_tawar_delay_min.pack(side="left", ipady=3)

        tk.Label(self.auto_tawar_delay_box, text="-", bg=CARD, fg=MUTED,
                 font=(FONT, 9)).pack(side="left", padx=3)

        self.e_auto_tawar_delay_max = tk.Entry(
            self.auto_tawar_delay_box, bg=FIELD, fg=TEXT, relief="flat", width=5,
            font=(FONT, 10), insertbackground=ACCENT,
        )
        self.e_auto_tawar_delay_max.insert(0, str(int(self.auto_tawar_delay_max_ms)))
        self.e_auto_tawar_delay_max.pack(side="left", ipady=3)

        self.auto_tawar_delay_box.pack(side="left")

        self.auto_tawar_simpan_btn = RoundedButton(
            toggle_row, "Simpan", self.save_auto_tawar_form,
            width=90, height=30, fill=ACCENT, bg=CARD)
        self.auto_tawar_simpan_btn.pack(side="left", padx=(14, 0))

        self.auto_tawar_jenis_row = tk.Frame(panel, bg=CARD)
        jenis_row = self.auto_tawar_jenis_row
        jenis_row.grid(row=2, column=0, sticky="w", pady=(10, 0))

        tk.Label(jenis_row, text="Jenis:", bg=CARD, fg=MUTED,
                 font=(FONT, 8)).pack(side="left", padx=(0, 8))

        self.auto_tawar_jenis_vars = {}
        for jenis in self.JENIS_LIST:
            var = tk.BooleanVar(
                value=self.auto_tawar_jenis_settings.get(jenis, {}).get("enabled", False)
            )
            self.auto_tawar_jenis_vars[jenis] = var
            tk.Checkbutton(
                jenis_row, text=jenis, variable=var,
                command=self._refresh_auto_tawar_visibility,
                bg=CARD, fg=TEXT, selectcolor=FIELD, activebackground=CARD,
                activeforeground=TEXT, font=(FONT, 9), anchor="w",
            ).pack(side="left")
            InfoTip(jenis_row, tip_jenis("Auto Tawar", jenis)).pack(
                side="left", padx=(2, 12))

        self.auto_tawar_fields_frame = tk.Frame(panel, bg=CARD)
        self.auto_tawar_fields_frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.auto_tawar_fields_frame.grid_columnconfigure(0, weight=1)

        self.auto_tawar_entries = {}
        self.auto_tawar_jenis_panels = {}
        self.auto_tawar_fixfile_include_vars = {}
        self.auto_tawar_fixfile_exclude_vars = {}
        self.auto_tawar_use_pct_vars = {}
        self.auto_tawar_pct_entries = {}
        self.auto_tawar_pct_wraps = {}
        self.auto_tawar_use_range_vars = {}
        self.auto_tawar_range_min_entries = {}
        self.auto_tawar_range_max_entries = {}
        self.auto_tawar_range_wraps = {}
        self.auto_tawar_range_rows = {}
        self.auto_tawar_outside_vars = {}
        self.auto_tawar_outside_rows = {}
        self.auto_tawar_floor_vars = {}
        self.auto_tawar_floor_entries = {}
        self.auto_tawar_row_index = {}

        row_i = 0
        for jenis in self.JENIS_LIST:
            sub = tk.Frame(self.auto_tawar_fields_frame, bg=CARD,
                           highlightbackground=FIELD_EDGE, highlightthickness=1)
            sub.grid(row=row_i, column=0, sticky="ew", pady=(0, 10),
                     ipadx=8, ipady=8)
            sub.grid_columnconfigure(0, weight=1)
            self.auto_tawar_jenis_panels[jenis] = sub
            row_i += 1

            tk.Label(sub, text=jenis.upper(), bg=CARD, fg=AMBER,
                     font=(FONT, 8, "bold")).grid(
                row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 4))

            settings = self.auto_tawar_jenis_settings.get(jenis, {})
            self.auto_tawar_entries[jenis] = {}

            if jenis in ("Parafrase", "Humanizer"):
                # Target berbentuk RENTANG (beda dari Auto Terima yang
                # cuma "di atas X"), dan harga client disyaratkan DI
                # BAWAH persentase ini — fallback untuk job yang kurang
                # bagus buat Terima langsung tapi masih worth ditawar.
                # Sebaris ke kanan selama muat; otomatis melipat turun
                # per field kalau jendela terlalu sempit (lihat
                # _build_criteria_flow).
                field_defs = [
                    ("target_min", "Target min (%)", settings.get("target_min")),
                    ("target_max", "Target max (%)", settings.get("target_max")),
                    ("max_client_pct", "Client max (%)", settings.get("max_client_pct")),
                    ("deadline_min_h", "Deadline min (jam)", settings.get("deadline_min_h")),
                    ("deadline_max_h", "Deadline max (jam)", settings.get("deadline_max_h")),
                ]
                if jenis == "Humanizer":
                    field_defs.append(
                        ("min_word_count", "Kata min", settings.get("min_word_count")))
                    field_defs.append(
                        ("max_word_count", "Kata max", settings.get("max_word_count")))
                next_row = self._build_criteria_flow(
                    sub, field_defs, self.auto_tawar_entries[jenis], row=1,
                    tips=TIPS_TAWAR)

                self._build_target_tawar_row(sub, jenis, settings, row=next_row)

            else:  # Fix File
                field_defs = [
                    ("max_client_pct", "Client max (%)", settings.get("max_client_pct")),
                    ("deadline_min_h", "Deadline min (jam)", settings.get("deadline_min_h")),
                    ("deadline_max_h", "Deadline max (jam)", settings.get("deadline_max_h")),
                ]
                next_row = self._build_criteria_flow(
                    sub, field_defs, self.auto_tawar_entries[jenis], row=1,
                    tips=TIPS_TAWAR)

                next_row = self._build_target_tawar_row(
                    sub, jenis, settings, row=next_row)

                service_row = tk.Frame(sub, bg=CARD)
                service_row.grid(row=next_row, column=0, columnspan=2, sticky="ew",
                                 padx=8, pady=(10, 0))
                service_row.grid_columnconfigure(0, weight=1, uniform="svct")
                service_row.grid_columnconfigure(1, weight=1, uniform="svct")

                include_box = tk.Frame(service_row, bg=CARD)
                include_box.grid(row=0, column=0, sticky="new", padx=(0, 20))
                lbl_inc = tk.Label(include_box, text="SERVICE YANG DIPILIH",
                                   bg=CARD, fg=MUTED, font=(FONT, 7, "bold"),
                                   anchor="w")
                lbl_inc.pack(anchor="w", pady=(0, 2))
                HoverTip(lbl_inc, TIP_SERVICE_INCLUDE)
                tk.Label(include_box, text="(job boleh punya layanan lain juga)",
                         bg=CARD, fg=MUTED, font=(FONT, 7), anchor="w").pack(
                    anchor="w", pady=(0, 6))
                include_vals = set(settings.get("service_include") or [])
                for svc in self.FIXFILE_SERVICES:
                    var = tk.BooleanVar(value=svc in include_vals)
                    self.auto_tawar_fixfile_include_vars[svc] = var
                    tk.Checkbutton(
                        include_box, text=svc, variable=var,
                        bg=CARD, fg=TEXT, selectcolor=FIELD, activebackground=CARD,
                        activeforeground=TEXT, font=(FONT, 8), anchor="w",
                        wraplength=280, justify="left",
                    ).pack(anchor="w", fill="x", pady=1)

                exclude_box = tk.Frame(service_row, bg=CARD)
                exclude_box.grid(row=0, column=1, sticky="new")
                lbl_exc = tk.Label(exclude_box, text="SERVICE YANG DIHINDARI",
                                   bg=CARD, fg=MUTED, font=(FONT, 7, "bold"),
                                   anchor="w")
                lbl_exc.pack(anchor="w", pady=(0, 2))
                HoverTip(lbl_exc, TIP_SERVICE_EXCLUDE)
                tk.Label(exclude_box, text="(ada salah satu ini -> ditolak)",
                         bg=CARD, fg=MUTED, font=(FONT, 7), anchor="w").pack(
                    anchor="w", pady=(0, 6))
                exclude_vals = set(settings.get("service_exclude") or [])
                for svc in self.FIXFILE_SERVICES:
                    var = tk.BooleanVar(value=svc in exclude_vals)
                    self.auto_tawar_fixfile_exclude_vars[svc] = var
                    tk.Checkbutton(
                        exclude_box, text=svc, variable=var,
                        bg=CARD, fg=TEXT, selectcolor=FIELD, activebackground=CARD,
                        activeforeground=TEXT, font=(FONT, 8), anchor="w",
                        wraplength=280, justify="left",
                    ).pack(anchor="w", fill="x", pady=1)

        self._refresh_auto_tawar_visibility()

    def _refresh_auto_tawar_visibility(self):
        """Semua panel syarat, baris "Jenis:", dan field Delay tersembunyi
        kalau Auto Tawar dimatikan — sama seperti Auto Terima."""
        if not self.auto_tawar_enabled_var.get():
            self.auto_tawar_delay_box.pack_forget()
            self.auto_tawar_jenis_row.grid_remove()
            self.auto_tawar_fields_frame.grid_remove()
            return
        if not self.auto_tawar_delay_box.winfo_ismapped():
            self.auto_tawar_delay_box.pack(
                side="left", before=self.auto_tawar_simpan_btn)
        self.auto_tawar_jenis_row.grid()
        self.auto_tawar_fields_frame.grid()

        for jenis, sub in self.auto_tawar_jenis_panels.items():
            if self.auto_tawar_jenis_vars[jenis].get():
                sub.grid()
            else:
                sub.grid_remove()

    def save_auto_tawar_form(self):
        def read(entry, default=0.0, allow_blank=False):
            txt = entry.get().strip()
            if not txt:
                return None if allow_blank else default
            return float(txt)

        try:
            self.auto_tawar_enabled = bool(self.auto_tawar_enabled_var.get())
            self.auto_tawar_delay_min_ms = float(
                self.e_auto_tawar_delay_min.get().strip() or 0)
            self.auto_tawar_delay_max_ms = float(
                self.e_auto_tawar_delay_max.get().strip() or 0)

            new_settings = {}
            for jenis in self.JENIS_LIST:
                entries = self.auto_tawar_entries[jenis]
                enabled = bool(self.auto_tawar_jenis_vars[jenis].get())
                use_target_tawar = bool(self.auto_tawar_use_pct_vars[jenis].get())
                target_tawar_pct = read(self.auto_tawar_pct_entries[jenis],
                                        allow_blank=True)
                if use_target_tawar and target_tawar_pct is None:
                    raise ValueError("Target Tawar (%) dicentang tapi kosong")

                use_price_range = bool(self.auto_tawar_use_range_vars[jenis].get())
                price_range_min = read(self.auto_tawar_range_min_entries[jenis],
                                       allow_blank=True)
                price_range_max = read(self.auto_tawar_range_max_entries[jenis],
                                       allow_blank=True)
                outside_range_default = bool(
                    self.auto_tawar_outside_vars[jenis].get())
                use_default_floor = bool(self.auto_tawar_floor_vars[jenis].get())
                default_floor_price = read(self.auto_tawar_floor_entries[jenis],
                                           allow_blank=True)
                if use_default_floor and default_floor_price is None:
                    raise ValueError(
                        "Tawar default minimal dicentang tapi angkanya kosong")
                if use_price_range:
                    if price_range_min is None or price_range_max is None:
                        raise ValueError(
                            "Tawar Rentang Harga dicentang tapi Harga Min/Max kosong")
                    if price_range_min > price_range_max:
                        raise ValueError(
                            "Harga Min Rentang Harga tidak boleh lebih besar dari Max")

                if jenis in ("Parafrase", "Humanizer"):
                    s = {
                        "enabled": enabled,
                        "target_min": read(entries["target_min"], allow_blank=True),
                        "target_max": read(entries["target_max"], allow_blank=True),
                        "max_client_pct": read(entries["max_client_pct"]),
                        "deadline_min_h": read(entries["deadline_min_h"]),
                        "deadline_max_h": read(entries["deadline_max_h"]),
                        "use_target_tawar": use_target_tawar,
                        "target_tawar_pct": target_tawar_pct,
                        "use_price_range": use_price_range,
                        "price_range_min": price_range_min,
                        "price_range_max": price_range_max,
                        "outside_range_default": outside_range_default,
                        "use_default_floor": use_default_floor,
                        "default_floor_price": default_floor_price,
                    }
                    if jenis == "Humanizer":
                        s["min_word_count"] = read(entries["min_word_count"],
                                                   allow_blank=True)
                        s["max_word_count"] = read(entries["max_word_count"],
                                                   allow_blank=True)
                else:  # Fix File
                    s = {
                        "enabled": enabled,
                        "max_client_pct": read(entries["max_client_pct"]),
                        "deadline_min_h": read(entries["deadline_min_h"]),
                        "deadline_max_h": read(entries["deadline_max_h"]),
                        "use_target_tawar": use_target_tawar,
                        "target_tawar_pct": target_tawar_pct,
                        "use_price_range": use_price_range,
                        "price_range_min": price_range_min,
                        "price_range_max": price_range_max,
                        "outside_range_default": outside_range_default,
                        "use_default_floor": use_default_floor,
                        "default_floor_price": default_floor_price,
                        "service_include": [
                            svc for svc in self.FIXFILE_SERVICES
                            if self.auto_tawar_fixfile_include_vars[svc].get()
                        ],
                        "service_exclude": [
                            svc for svc in self.FIXFILE_SERVICES
                            if self.auto_tawar_fixfile_exclude_vars[svc].get()
                        ],
                    }
                new_settings[jenis] = s
            self.auto_tawar_jenis_settings = new_settings
        except ValueError:
            self.log("[SYS] Nilai syarat Auto Tawar tidak valid — pakai angka.",
                     "err")
            return

        self.save_auto_tawar_settings()
        aktif = [j for j in self.JENIS_LIST
                if self.auto_tawar_jenis_settings[j]["enabled"]]
        status = "AKTIF" if self.auto_tawar_enabled else "NONAKTIF"
        self.log(f"[SYS] Syarat Auto Tawar disimpan ({status}, jenis aktif: "
                 f"{', '.join(aktif) if aktif else '-'}).", "sys")

    # ------------------------------------------------------ catatan job proses
    def _build_notes_row(self):
        row = tk.Frame(self.body, bg=CARD)
        row.grid(row=8, column=0, sticky="ew", pady=(14, 0))
        self.btn_notes = RoundedButton(
            row, self._notes_button_text(), self.show_notes_popup,
            width=220, height=34, fill=ACCENT_SOFT, bg=CARD, subtle=True)
        self.btn_notes.pack(side="left")

    def _build_chrome_row(self):
        """Tombol pasang Chrome debug + shortcut Desktop, paling bawah
        supaya tidak menumpuk di area atas."""
        row = tk.Frame(self.body, bg=CARD)
        row.grid(row=9, column=0, sticky="ew", pady=(14, 0))
        lbl_chrome = tk.Label(row, text="CHROME DEBUG", bg=CARD, fg=MUTED,
                              font=(FONT, 7, "bold"))
        lbl_chrome.pack(anchor="w")
        HoverTip(lbl_chrome, TIP_CHROME_DEBUG)
        btns = tk.Frame(row, bg=CARD)
        btns.pack(anchor="w", pady=(3, 0))
        self.btn_chrome_setup = RoundedButton(
            btns, "Pasang / Buka", self.on_setup_chrome_debug,
            width=130, height=34, fill=GREEN, bg=CARD)
        self.btn_chrome_setup.pack(side="left")
        self.btn_chrome_shortcut = RoundedButton(
            btns, "Shortcut Desktop", self.on_make_chrome_shortcut,
            width=150, height=34, fill=ACCENT, bg=CARD)
        self.btn_chrome_shortcut.pack(side="left", padx=(8, 0))

    def _notes_button_text(self):
        return f"Catatan Hari Ini ({len(self.job_notes_list)})"

    def _refresh_notes_badge(self):
        if getattr(self, "btn_notes", None) and self.btn_notes.winfo_exists():
            self.btn_notes.set_text(self._notes_button_text())

    def show_notes_popup(self):
        """Popup terpisah yang bisa digeser (title bar bawaan OS), posisi
        terakhir diingat sama seperti popup job masuk. Dibangun ulang
        total tiap dipanggil (daftar catatan biasanya kecil) supaya tidak
        perlu logika update-di-tempat yang rumit."""
        if getattr(self, "notes_popup", None) and self.notes_popup.winfo_exists():
            self.notes_popup.destroy()

        win = tk.Toplevel(self, bg=CARD)
        win.title("Catatan Hari Ini")
        win.configure(bg=CARD)
        self.notes_popup = win
        if self.notes_popup_position:
            win.geometry(f"+{self.notes_popup_position[0]}+{self.notes_popup_position[1]}")
        win.bind("<Configure>", self._on_notes_popup_move)
        win.bind("<Destroy>", self._on_notes_popup_destroyed)

        pad = tk.Frame(win, bg=CARD, padx=16, pady=14)
        pad.pack(fill="both", expand=True)

        tk.Label(pad, text="Catatan Hari Ini", bg=CARD, fg=TEXT,
                 font=(FONT, 12, "bold")).pack(anchor="w", pady=(0, 2))
        tk.Label(pad, text="Job yang sudah diterima lalu status-nya berubah "
                           "(mis. selesai menunggu pembayaran).", bg=CARD,
                 fg=MUTED, font=(FONT, 8), wraplength=320, justify="left"
                 ).pack(anchor="w", pady=(0, 10))

        if not self.job_notes_list:
            tk.Label(pad, text="Belum ada catatan hari ini.", bg=CARD,
                     fg=MUTED, font=(FONT, 9)).pack(anchor="w")
        else:
            for i, note in enumerate(self.job_notes_list):
                item = tk.Frame(pad, bg=FIELD, padx=10, pady=7)
                item.pack(fill="x", pady=3)
                info_col = tk.Frame(item, bg=FIELD)
                info_col.pack(side="left", fill="x", expand=True)
                tk.Label(info_col,
                         text=(f"{note.get('jenis', '-')} — {note.get('harga_client', '-')}"
                               + (f"  ·  tawar {note['harga_tawar']}"
                                  if note.get('harga_tawar') else "")),
                         bg=FIELD, fg=TEXT, font=(FONT, 10, "bold"), anchor="w"
                         ).pack(anchor="w")
                tk.Label(info_col,
                         text=f"{note.get('status', '-')}  ·  "
                              f"{note.get('time', '-')}  ·  {note.get('ref', '-')}",
                         bg=FIELD, fg=MUTED, font=(FONT, 8), anchor="w"
                         ).pack(anchor="w")
                tk.Label(info_col,
                         text=f"Deadline: {note.get('deadline', '-')}",
                         bg=FIELD, fg=MUTED, font=(FONT, 8), anchor="w"
                         ).pack(anchor="w")

                btn_col = tk.Frame(item, bg=FIELD)
                btn_col.pack(side="right")
                RoundedButton(
                    btn_col, "Hapus", lambda idx=i: self.remove_job_note(idx),
                    width=60, height=26, fill=RED, bg=FIELD,
                ).pack(side="right")
                url = note.get("url")
                if url:
                    RoundedButton(
                        btn_col, "Buka Detail",
                        lambda u=url: webbrowser.open(u),
                        width=90, height=26, fill=ACCENT, bg=FIELD,
                    ).pack(side="right", padx=(0, 6))

            RoundedButton(
                pad, "Hapus Semua", self.clear_job_notes,
                width=130, height=30, fill=FIELD_EDGE, bg=CARD, subtle=True,
            ).pack(anchor="e", pady=(10, 0))

    def _on_notes_popup_move(self, event):
        win = getattr(self, "notes_popup", None)
        if win and win.winfo_exists():
            self.notes_popup_position = (win.winfo_x(), win.winfo_y())

    def _on_notes_popup_destroyed(self, event):
        if self.notes_popup_position:
            self.save_notes_popup_position(*self.notes_popup_position)

    def remove_job_note(self, index):
        if 0 <= index < len(self.job_notes_list):
            self.job_notes_list.pop(index)
            self.save_job_notes()
            self._refresh_notes_badge()
            self.show_notes_popup()

    def clear_job_notes(self):
        self.job_notes_list = []
        self.save_job_notes()
        self._refresh_notes_badge()
        self.show_notes_popup()

    def _add_job_note(self, job, status_baru):
        from datetime import datetime as _dt
        ref = job.get("ref") or "-"
        # Sudah masuk proses (dicatat) — berhenti dipantau, baik lewat
        # polling (_watch_job_status) maupun lewat notif *PAID* grup.
        self.watched_jobs.pop(ref, None)
        entry = {
            "ref": ref,
            "jenis": job.get("jenis") or "-",
            "harga_client": job.get("harga_client") or "-",
            "harga_tawar": job.get("harga_tawar_fmt") or "",
            "deadline": job.get("deadline") or "-",
            "url": job.get("url") or "",
            "status": status_baru,
            "time": _dt.now().strftime("%H:%M"),
        }
        self.job_notes_list.append(entry)
        self.save_job_notes()
        self._refresh_notes_badge()
        self.show_notes_popup()
        self.log(f"[CATATAN] {entry['jenis']} ({entry['ref']}) -> {status_baru}",
                 "sys")

    def _mark_job_note_done(self, ref):
        """Dipanggil saat pesan *DONE* grup cocok dengan ref job kita yang
        ada di Catatan Job Proses — status entry-nya diganti "done" (bukan
        dihapus, supaya masih kelihatan sempat tercatat hari itu; user
        tetap bisa hapus manual lewat tombol Hapus kalau mau)."""
        changed = False
        for entry in self.job_notes_list:
            if entry.get("ref") == ref:
                entry["status"] = "done"
                changed = True
        if not changed:
            return
        self.save_job_notes()
        self._refresh_notes_badge()
        self.show_notes_popup()
        self.log(f"[CATATAN] {ref} -> done (lewat notif *DONE* grup)", "sys")

    def _handle_status_broadcast(self, text, is_paid):
        """Dipanggil tiap pesan berlabel *PAID*/*DONE* masuk di grup yang
        sama dengan *MASUK* — dicocokkan ke job yang SUDAH KITA TERIMA
        lewat Ref (job orang lain otomatis diabaikan karena ref-nya tidak
        ada di self.watched_jobs). Ini pelengkap _watch_job_status
        (polling tiap 45 detik): kalau status susah/lama ke-refresh di
        server, notif grup ini biasanya lebih cepat kasih tahu."""
        ref_match = REF_PATTERN.search(text)
        ref = ref_match.group(1) if ref_match else None
        if not ref:
            return
        if is_paid:
            job = self.watched_jobs.get(ref)
            if not job:
                return  # bukan job kita — abaikan
            self._add_job_note(job, "paid")
        else:
            # *DONE* bisa datang kapan saja — kalau job ini masih di
            # watched_jobs (polling belum sempat mendeteksi "proses"
            # duluan), hentikan juga pemantauannya.
            self.watched_jobs.pop(ref, None)
            self._mark_job_note_done(ref)

    def _watch_job_status(self, job):
        """Pantau status order yang baru diterima sampai benar-benar masuk
        proses (bukan sekadar "waiting-payment", karena order memang
        lazim berstatus waiting-payment dulu sebelum client bayar).
        Baru dicatat sekali kalau status berubah jadi sesuatu SELAIN
        status awal & SELAIN waiting-payment. Kalau status waiting-payment
        itu kedaluwarsa jadi "expired", berhenti memantau job ini tanpa
        dicatat (client tidak jadi bayar). Dibatasi 3 jam (jauh melebihi
        1 jam) supaya thread tidak hidup selamanya kalau client tidak
        kunjung bayar/status tidak pernah berubah. Dipakai untuk job hasil
        Terima maupun Tawar (watch_mode="tawar").

        Jalan PARALEL dengan _handle_status_broadcast (notif *PAID*/*DONE*
        grup) — dua-duanya sama-sama bisa memicu catatan; siapa pun yang
        lebih dulu "menang" (job langsung dicoret dari self.watched_jobs
        lewat _add_job_note, jadi yang kedua tidak dobel mencatat)."""
        ref = job.get("ref")
        job_id, collection, url = job.get("id"), job.get("collection"), job.get("url")
        if ref:
            self.watched_jobs[ref] = job
        if not (job_id and collection):
            return

        status_awal = job.get("status")
        if not status_awal:
            info0, _, hard_error0 = self.browser.fetch_order_detail(
                job_id, collection, url)
            if hard_error0:
                return
            status_awal = info0.get("status")

        def _norm(s):
            return (s or "").strip().lower().replace("_", "-")

        deadline = time.time() + 3 * 3600
        while time.time() < deadline:
            time.sleep(45)
            # Sudah ditangani lewat notif *PAID*/*DONE* grup selagi tidur —
            # tidak perlu polling lagi untuk job ini.
            if ref and ref not in self.watched_jobs:
                return
            info, _, hard_error = self.browser.fetch_order_detail(
                job_id, collection, url)
            if hard_error:
                continue
            status_baru = info.get("status")
            norm_baru = _norm(status_baru)
            if not status_baru or norm_baru == "-":
                continue
            if "expired" in norm_baru:
                # Client tidak jadi bayar — berhenti memantau, tidak dicatat.
                if ref:
                    self.watched_jobs.pop(ref, None)
                return
            if norm_baru == "waiting-payment":
                # Masih menunggu client bayar — belum masuk proses, lanjut pantau.
                continue
            if job.get("watch_mode") == "tawar":
                # Job hasil TAWAR: selama masih proses negosiasi (status
                # "waiting-...", "nego...", atau balik ke status awal
                # karena tawaran ditolak) belum dianggap masuk proses.
                if (norm_baru.startswith("waiting") or "nego" in norm_baru
                        or norm_baru == _norm(status_awal)):
                    continue
                if any(w in norm_baru for w in
                       ("cancel", "reject", "refund", "fail")):
                    if ref:
                        self.watched_jobs.pop(ref, None)
                    return
            if norm_baru != _norm(status_awal):
                # Berubah jadi status lain (bukan waiting-payment/expired) —
                # ini yang dianggap "masuk proses".
                self.ui(self._add_job_note, job, status_baru)
                return

    def refresh_buttons(self):
        """Perbarui tampilan pill status & tombol Start/Pause.

        Kalau koneksi sedang terputus, status koneksi didahulukan (merah)
        di atas status pause/berjalan, supaya kelihatan jelas ada masalah
        jaringan tanpa perlu buka log.
        """
        self.btn_start.set_enabled(self.is_paused)
        self.btn_pause.set_enabled(not self.is_paused)

        if not self.online:
            self.dot.itemconfig(self.dot_id, fill=RED)
            self.state_label.config(text="Terputus", fg=RED)
        elif self.is_paused:
            self.dot.itemconfig(self.dot_id, fill=AMBER)
            self.state_label.config(text="Berhenti", fg=AMBER)
        else:
            self.dot.itemconfig(self.dot_id, fill=GREEN)
            self.state_label.config(text="Berjalan", fg=GREEN)

    def log(self, text, tag="sys"):
        if not getattr(self, "log_box", None) or not self.log_box.winfo_exists():
            return
        self.log_box.config(state="normal")
        self.log_box.insert("end", text + "\n", tag)
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def on_start(self):
        self.is_paused = False
        self.watchdog_active = True
        self.refresh_buttons()
        self.log("--- Listener berjalan ---", "sys")
        self.log("[SYS] Pemantauan koneksi aktif.", "sys")
        # Siapkan session & tab Chrome debug sekarang, bukan saat job
        # pertama masuk, supaya popup pertama pun tidak menunggu.
        threading.Thread(target=self.browser.warm_up, daemon=True).start()

    def on_pause(self):
        self.is_paused = True
        self.watchdog_active = False
        self.online = True          # reset indikator, tidak dipantau saat pause
        self.refresh_buttons()
        self.log("--- Listener dijeda ---", "sys")

    # ------------------------------------------------------ config
    def _read_raw_config(self):
        if not os.path.exists(CONFIG_FILE):
            return {}
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:                          # noqa: BLE001
            return {}

    def _write_raw_config(self, data):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def load_config(self):
        data = self._read_raw_config()
        if "api_id" not in data or "api_hash" not in data:
            return None
        try:
            return {
                "api_id": int(data["api_id"]),
                "api_hash": data["api_hash"],
                "hotkey_terima": data.get("hotkey_terima", "f2"),
                "hotkey_tawar": data.get("hotkey_tawar", "f3"),
            }
        except Exception:                          # noqa: BLE001
            return None

    def save_config(self, api_id, api_hash, hotkey_terima=None, hotkey_tawar=None):
        # Merge, bukan timpa total -> pengaturan lain (auto terima, posisi
        # popup) yang sudah tersimpan tidak ikut hilang.
        data = self._read_raw_config()
        data["api_id"] = api_id
        data["api_hash"] = api_hash
        data["hotkey_terima"] = hotkey_terima or self.hotkey_terima
        data["hotkey_tawar"] = hotkey_tawar or self.hotkey_tawar
        self._write_raw_config(data)

    def load_auto_settings(self):
        """Syarat sekarang disimpan per-jenis (auto_jenis_settings), supaya
        beberapa jenis bisa aktif bersamaan dengan syarat masing-masing.
        Config lama (satu jenis saja, field flat auto_min_target dkk.)
        dimigrasikan otomatis supaya tidak hilang begitu saja saat update."""
        data = self._read_raw_config()
        jenis_data = data.get("auto_jenis_settings")
        if not isinstance(jenis_data, dict):
            jenis_data = {}

        def jset(jenis, defaults):
            saved = jenis_data.get(jenis)
            out = dict(defaults)
            if isinstance(saved, dict):
                out.update({k: saved[k] for k in defaults if k in saved})
            return out

        legacy_jenis = data.get("auto_jenis")
        legacy_enabled = bool(data.get("auto_enabled", False))

        parafrase_defaults = {
            "enabled": legacy_enabled and legacy_jenis == "Parafrase",
            "min_target": data.get("auto_min_target", 20),
            "min_client_pct": data.get("auto_min_client_pct", 50),
            # Syarat harga ABSOLUT (Rp), terpisah dari perbandingan % di
            # atas — mis. client minimal Rp100.000 berapa pun % nya
            # terhadap estimasi. Kosongkan (None) untuk tidak dipakai.
            "min_client_price": data.get("auto_min_client_price", 100000),
            "deadline_min_h": data.get("auto_deadline_min_h", 2),
            "deadline_max_h": data.get("auto_deadline_max_h", 48),
        }
        humanizer_defaults = {
            "enabled": legacy_enabled and legacy_jenis == "Humanizer",
            "min_target": data.get("auto_min_target", 20),
            "min_client_pct": data.get("auto_min_client_pct", 50),
            "min_client_price": data.get("auto_min_client_price", 100000),
            "deadline_min_h": data.get("auto_deadline_min_h", 2),
            "deadline_max_h": data.get("auto_deadline_max_h", 48),
            "min_word_count": data.get("auto_min_word_count"),
            "max_word_count": data.get("auto_max_word_count", 3000),
        }
        fixfile_defaults = {
            "enabled": False,
            "min_client_pct": 50,
            "min_client_price": 100000,
            "deadline_min_h": 2,
            "deadline_max_h": 48,
            "service_include": [],
            "service_exclude": [],
        }

        return {
            "enabled": bool(data.get("auto_enabled", False)),
            # Dalam milidetik, diisi langsung oleh user (0ms = tanpa jeda
            # sama sekali). Default kecil — jeda lama bikin status order
            # keburu berubah sebelum sempat konfirmasi.
            "delay_min_ms": data.get("auto_delay_min_ms", 300),
            "delay_max_ms": data.get("auto_delay_max_ms", 800),
            "jenis_settings": {
                "Parafrase": jset("Parafrase", parafrase_defaults),
                "Humanizer": jset("Humanizer", humanizer_defaults),
                "Fix File": jset("Fix File", fixfile_defaults),
            },
        }

    def save_auto_settings(self):
        data = self._read_raw_config()
        data["auto_enabled"] = self.auto_enabled
        data["auto_delay_min_ms"] = self.auto_delay_min_ms
        data["auto_delay_max_ms"] = self.auto_delay_max_ms
        data["auto_jenis_settings"] = self.auto_jenis_settings
        # Kunci format lama (single-jenis) dibuang supaya tidak membingungkan
        # kalau config.json dibuka manual — semua sudah pindah ke
        # auto_jenis_settings di atas.
        for old_key in ("auto_jenis", "auto_min_target", "auto_min_client_pct",
                        "auto_deadline_min_h", "auto_deadline_max_h",
                        "auto_max_word_count", "auto_min_word_count"):
            data.pop(old_key, None)
        self._write_raw_config(data)

    def load_auto_tawar_settings(self):
        """Sama strukturnya dengan load_auto_settings (Auto Terima), tapi
        kunci config.json beda ("auto_tawar_...") dan disimpan terpisah
        total — jadi syarat Auto Tawar bisa diisi beda dari Auto Terima
        per jenis (mis. Auto Terima minimal 70% harga, Auto Tawar aktif
        untuk yang di bawah itu sebagai fallback)."""
        data = self._read_raw_config()
        jenis_data = data.get("auto_tawar_jenis_settings")
        if not isinstance(jenis_data, dict):
            jenis_data = {}

        def jset(jenis, defaults):
            saved = jenis_data.get(jenis)
            out = dict(defaults)
            if isinstance(saved, dict):
                out.update({k: saved[k] for k in defaults if k in saved})
            return out

        # Beda arah dari Auto Terima: target berbentuk RENTANG (bukan cuma
        # "di atas X"), dan harga client disyaratkan DI BAWAH persentase
        # tertentu dari estimasi — ini memang fallback untuk job yang
        # harganya kurang bagus buat Terima langsung tapi masih worth
        # ditawar (lihat _evaluate_auto_tawar_conditions).
        # use_target_tawar/target_tawar_pct: kalau dicentang, harga yang
        # ditawar dihitung sebagai persentase dari Harga ESTIMASI (lihat
        # compute_tawar_price_from_client) bukan potongan bracket dari
        # Harga Estimasi — diatur per jenis lewat checkbox di panel.
        # use_price_range/price_range_min/price_range_max: kalau
        # "Target Tawar (%)" di atas AKTIF dan ini juga dicentang, dasar
        # perhitungan persentasenya dinaikkan jadi price_range_max kalau
        # Harga Estimasi job jatuh di antara price_range_min-max — lihat
        # _build_target_tawar_row dan _trigger_tawar.
        # outside_range_default: kalau rentang harga aktif dan dicentang,
        # job yang Harga Estimasi-nya DI LUAR rentang memakai logika tawar
        # default (bracket di bawah Harga Estimasi), BUKAN persentase —
        # jadi persentase >100% cuma berlaku untuk job dalam rentang.
        parafrase_defaults = {
            "enabled": False, "target_min": 0, "target_max": 20,
            "max_client_pct": 70, "deadline_min_h": 2, "deadline_max_h": 48,
            "use_target_tawar": False, "target_tawar_pct": None,
            "use_price_range": False, "price_range_min": None,
            "price_range_max": None, "outside_range_default": False,
            "use_default_floor": False, "default_floor_price": 100000,
        }
        humanizer_defaults = {
            "enabled": False, "target_min": 0, "target_max": 20,
            "max_client_pct": 70, "deadline_min_h": 2, "deadline_max_h": 48,
            "min_word_count": None, "max_word_count": 3000,
            "use_target_tawar": False, "target_tawar_pct": None,
            "use_price_range": False, "price_range_min": None,
            "price_range_max": None, "outside_range_default": False,
            "use_default_floor": False, "default_floor_price": 100000,
        }
        fixfile_defaults = {
            "enabled": False, "max_client_pct": 70,
            "deadline_min_h": 2, "deadline_max_h": 48,
            "service_include": [], "service_exclude": [],
            "use_target_tawar": False, "target_tawar_pct": None,
            "use_price_range": False, "price_range_min": None,
            "price_range_max": None, "outside_range_default": False,
            "use_default_floor": False, "default_floor_price": 100000,
        }

        return {
            "enabled": bool(data.get("auto_tawar_enabled", False)),
            "delay_min_ms": data.get("auto_tawar_delay_min_ms", 300),
            "delay_max_ms": data.get("auto_tawar_delay_max_ms", 800),
            "jenis_settings": {
                "Parafrase": jset("Parafrase", parafrase_defaults),
                "Humanizer": jset("Humanizer", humanizer_defaults),
                "Fix File": jset("Fix File", fixfile_defaults),
            },
        }

    def save_auto_tawar_settings(self):
        data = self._read_raw_config()
        data["auto_tawar_enabled"] = self.auto_tawar_enabled
        data["auto_tawar_delay_min_ms"] = self.auto_tawar_delay_min_ms
        data["auto_tawar_delay_max_ms"] = self.auto_tawar_delay_max_ms
        data["auto_tawar_jenis_settings"] = self.auto_tawar_jenis_settings
        self._write_raw_config(data)

    def load_popup_position(self):
        data = self._read_raw_config()
        pos = data.get("popup_position")
        if isinstance(pos, list) and len(pos) == 2:
            return tuple(pos)
        return None

    def save_popup_position(self, x, y):
        data = self._read_raw_config()
        data["popup_position"] = [x, y]
        self._write_raw_config(data)

    def load_log_height(self):
        data = self._read_raw_config()
        try:
            return max(100, min(1000, int(data.get("log_height", 240))))
        except (TypeError, ValueError):
            return 240

    def save_log_height(self):
        data = self._read_raw_config()
        data["log_height"] = int(self.log_height)
        self._write_raw_config(data)

    # ------------------------------------------------------ catatan job proses
    def load_job_notes(self):
        """Catatan cuma berlaku untuk HARI INI — kalau tanggal tersimpan
        beda dari hari ini (aplikasi baru dibuka besoknya), daftar lama
        dianggap kosong (otomatis "reset harian")."""
        from datetime import datetime as _dt
        data = self._read_raw_config()
        today = _dt.now().strftime("%Y-%m-%d")
        if data.get("job_notes_date") != today:
            return []
        notes = data.get("job_notes")
        return notes if isinstance(notes, list) else []

    def save_job_notes(self):
        from datetime import datetime as _dt
        data = self._read_raw_config()
        data["job_notes_date"] = _dt.now().strftime("%Y-%m-%d")
        data["job_notes"] = self.job_notes_list
        self._write_raw_config(data)

    def load_notes_popup_position(self):
        data = self._read_raw_config()
        pos = data.get("notes_popup_position")
        if isinstance(pos, list) and len(pos) == 2:
            return tuple(pos)
        return None

    def save_notes_popup_position(self, x, y):
        data = self._read_raw_config()
        data["notes_popup_position"] = [x, y]
        self._write_raw_config(data)

    # ------------------------------------------------------ resize kotak log
    def _on_log_resize_start(self, event):
        try:
            self._log_resize_start_y = event.y_root
            self._log_resize_start_h = self.log_frame.winfo_height()
            self._log_resize_dragged = False
        except Exception:                             # noqa: BLE001
            self._log_resize_start_y = None

    def _on_log_resize_drag(self, event):
        if self._log_resize_start_y is None or self._log_resize_start_h is None:
            return
        try:
            self._log_resize_dragged = True
            delta = event.y_root - self._log_resize_start_y
            new_h = int(max(100, min(1000, self._log_resize_start_h + delta)))
            self.log_height = new_h
            # minsize baris 1 di grid INDUK (self.body) adalah sumber
            # kebenaran tinggi kotak log — ini yang bikin grid benar2
            # menghitung ulang ukuran barisnya. height= pada frame anak
            # saja (percobaan sebelumnya) tidak cukup memicu ulang-hitung
            # baris induk, makanya log berubah tapi kotak di layar tetap.
            self.body.grid_rowconfigure(1, minsize=new_h)
            self.log_frame.configure(height=new_h)
            self.log_frame.update_idletasks()
            self.body.update_idletasks()
            self.body_canvas.configure(scrollregion=self.body_canvas.bbox("all"))
        except Exception:                             # noqa: BLE001
            pass

    def _on_log_resize_end(self, event):
        if self._log_resize_start_y is None:
            return
        dragged = getattr(self, "_log_resize_dragged", False)
        self._log_resize_start_y = None
        self._log_resize_start_h = None
        if dragged:
            self.save_log_height()

    # ------------------------------------------------------ alur telethon
    def try_existing_session(self):
        saved = self.load_config()
        if not saved or not os.path.exists(SESSION_NAME + ".session"):
            return

        self.set_status("Mencoba sesi tersimpan...")
        self.api_id, self.api_hash = saved["api_id"], saved["api_hash"]
        self.hotkey_terima = saved.get("hotkey_terima", "f2")
        self.hotkey_tawar = saved.get("hotkey_tawar", "f3")
        client = build_client(self.api_id, self.api_hash)
        self.worker.client = client

        async def connect_check():
            await client.connect()
            if not await client.is_user_authorized():
                return None
            return await client.get_me()

        def done(me, err):
            if err or me is None:
                self.ui(self.set_status, "")
                return
            self.ui(self.finish_login, me)

        self.worker.submit(connect_check(), done)

    def submit_credentials(self):
        api_id = self.f_api_id.get().strip()
        api_hash = self.f_api_hash.get().strip()

        if not api_id.isdigit() or not api_hash:
            self.set_status("API ID harus angka dan API Hash tidak boleh kosong.",
                            RED)
            return

        self.api_id = int(api_id)
        self.api_hash = api_hash
        self.save_config(self.api_id, self.api_hash)

        self.btn_login.set_enabled(False)
        self.set_status("Menghubungkan...")

        client = build_client(self.api_id, self.api_hash)
        self.worker.client = client

        async def connect():
            await client.connect()
            if await client.is_user_authorized():
                return await client.get_me()
            return None

        def done(me, err):
            if err:
                self.ui(self.on_error, err)
            elif me is not None:
                self.ui(self.finish_login, me)
            else:
                self.ui(self.show_phone_step)

        self.worker.submit(connect(), done)

    def submit_phone(self):
        phone = self.f_phone.get().strip()
        if not phone:
            self.set_status("Nomor telepon tidak boleh kosong.", RED)
            return

        self.phone = phone
        self.btn_login.set_enabled(False)
        self.set_status("Mengirim kode...")

        client = self.worker.client

        async def send_code():
            if not client.is_connected():
                await client.connect()
            await client.send_code_request(phone)

        def done(_, err):
            if err:
                self.ui(self.on_error, err)
            else:
                self.ui(self.show_code_step)

        self.worker.submit(send_code(), done)

    def submit_code(self):
        code = self.f_code.get().strip()
        if not code:
            self.set_status("Kode tidak boleh kosong.", RED)
            return

        self.btn_login.set_enabled(False)
        self.set_status("Memverifikasi...")

        client = self.worker.client
        phone = self.phone

        async def sign_in():
            return await client.sign_in(phone=phone, code=code)

        def done(me, err):
            if isinstance(err, SessionPasswordNeededError):
                self.ui(self.show_password_step)
            elif err:
                self.ui(self.on_error, err)
            else:
                self.ui(self.finish_login, me)

        self.worker.submit(sign_in(), done)

    def submit_password(self):
        password = self.f_password.get()
        if not password:
            self.set_status("Password tidak boleh kosong.", RED)
            return

        self.btn_login.set_enabled(False)
        self.set_status("Memverifikasi...")

        client = self.worker.client

        async def sign_in_pw():
            return await client.sign_in(password=password)

        def done(me, err):
            if err:
                self.ui(self.on_error, err)
            else:
                self.ui(self.finish_login, me)

        self.worker.submit(sign_in_pw(), done)

    def on_error(self, err):
        if isinstance(err, PhoneCodeInvalidError):
            msg = "Kode verifikasi salah. Coba lagi."
        elif isinstance(err, PasswordHashInvalidError):
            msg = "Password 2FA salah. Coba lagi."
        elif isinstance(err, FloodWaitError):
            menit = max(1, err.seconds // 60)
            msg = f"Telegram membatasi percobaan. Tunggu sekitar {menit} menit."
        else:
            msg = f"Gagal: {err}"

        self.set_status(msg, RED)
        if getattr(self, "btn_login", None) and self.btn_login.winfo_exists():
            self.btn_login.set_enabled(True)

    def finish_login(self, me):
        name = getattr(me, "first_name", "") or getattr(me, "username", "") or ""
        self.register_handler()
        self.start_watchdog()
        self._warm_browser()
        self.register_hotkeys()
        self.show_log_screen(name)
        threading.Thread(target=self._check_for_update, daemon=True).start()

    # ------------------------------------------------------ auto updater
    def _version_tuple(self, text):
        out = []
        for part in str(text).strip().split("."):
            digits = "".join(c for c in part if c.isdigit())
            out.append(int(digits) if digits else 0)
        return tuple(out)

    def _check_for_update(self):
        """Cek versi terbaru dari UPDATE_CHECK_URL (lihat komentar di
        konstanta-nya di atas file). Kosong -> tidak dicek sama sekali.
        Hanya memberi tahu, tidak mengunduh/menginstal apa pun sendiri."""
        if not UPDATE_CHECK_URL:
            return
        try:
            import urllib.request
            with urllib.request.urlopen(UPDATE_CHECK_URL, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            remote_version = str(data.get("version") or "").strip()
            if not remote_version:
                return
            if self._version_tuple(remote_version) > self._version_tuple(APP_VERSION):
                url = data.get("url") or ""
                exe_url = data.get("exe_url") or ""
                notes = data.get("notes") or ""
                msg = (f"[UPDATE] Versi baru tersedia: {remote_version} "
                       f"(sekarang: {APP_VERSION}).")
                if notes:
                    msg += f" Catatan: {notes}"
                self.ui(self.log, msg, "sys")
                if url or exe_url:
                    self.ui(self._show_update_notice, remote_version, url, exe_url)
        except Exception as exc:                  # noqa: BLE001
            # Gagal cek update tidak boleh mengganggu jalannya bot —
            # cukup diam, bukan dianggap error penting.
            self.ui(self.log, f"[UPDATE] Gagal cek versi terbaru: {exc}", "skip")

    def _show_update_notice(self, version, url, exe_url=""):
        """Kalau version.json punya "exe_url" (link langsung ke file .exe,
        bukan halaman rilis) DAN aplikasi ini sedang jalan sebagai .exe
        hasil build (bukan dijalankan lewat `py listener_gui.py`), tawarkan
        update otomatis: download .exe baru lalu restart sendiri. Kalau
        tidak memenuhi itu (dijalankan dari source, atau version.json cuma
        punya "url" halaman rilis), fallback ke cara lama: buka link di
        browser untuk diunduh manual."""
        can_auto = bool(exe_url) and bool(getattr(sys, "frozen", False))
        if can_auto:
            tanya = (f"Versi {version} sudah tersedia (sekarang {APP_VERSION}).\n\n"
                      "Unduh & update otomatis sekarang? Aplikasi akan "
                      "tertutup sebentar lalu terbuka ulang sendiri.")
        else:
            tanya = (f"Versi {version} sudah tersedia (sekarang {APP_VERSION}).\n\n"
                      "Buka halaman unduhannya sekarang?")
        if not messagebox.askyesno("Update tersedia", tanya):
            return
        if can_auto:
            threading.Thread(target=self._download_and_apply_update,
                             args=(exe_url, version), daemon=True).start()
            return
        target = url or exe_url
        try:
            webbrowser.open(target)
        except Exception as exc:              # noqa: BLE001
            self.log(f"[UPDATE] Gagal membuka link: {exc}", "err")

    def _fetch_expected_sha256(self, exe_url):
        """Ambil hash SHA-256 yang seharusnya (file kecil berisi hash saja,
        di-upload CI di sebelah .exe-nya, nama sama + ".sha256") — dipakai
        _download_and_apply_update untuk memastikan .exe yang diunduh utuh,
        bukan file rusak/kepotong (mis. kebetulan ke-download pas asset-nya
        lagi ditimpa build baru di GitHub). None kalau tidak ada/gagal —
        rilis lama belum tentu punya file ini, biar tetap bisa update,
        cuma lewat jalur verifikasi cadangan (sanity check ukuran/isi)."""
        import urllib.request
        try:
            with urllib.request.urlopen(exe_url + ".sha256", timeout=10) as resp:
                text = resp.read().decode("utf-8", errors="ignore").strip()
            hash_hex = text.split()[0].lower()
            if len(hash_hex) == 64 and all(c in "0123456789abcdef" for c in hash_hex):
                return hash_hex
        except Exception:                      # noqa: BLE001
            pass
        return None

    def _download_and_apply_update(self, exe_url, version):
        """Unduh .exe versi baru di sebelah .exe yang sedang jalan, lalu
        jadwalkan penggantiannya lewat skrip .bat kecil (exe yang sedang
        jalan tidak bisa menimpa dirinya sendiri selagi masih berjalan).
        Urutannya: unduh (sambil hitung SHA-256) -> cocokkan ke hash yang
        seharusnya (_fetch_expected_sha256) -> kalau cocok, tulis update.bat
        -> jalankan .bat (nunggu proses ini keluar dulu baru menimpa &
        membuka ulang) -> tutup aplikasi ini sendiri supaya file-nya tidak
        terkunci lagi.

        Verifikasi hash ini penting: download lewat HTTP bisa saja dapat
        file rusak/kepotong (koneksi putus di tengah) atau bahkan halaman
        error GitHub (kalau asset-nya pas lagi ditimpa build baru) —
        TANPA verifikasi, file rusak itu bisa kepasang menimpa .exe yang
        masih berfungsi, bikin aplikasi tidak bisa dibuka sama sekali
        ("Failed to load Python DLL" dkk). Kalau verifikasi gagal, .exe
        yang sedang berjalan TIDAK disentuh — aman, tinggal coba lagi
        nanti (dicek ulang tiap aplikasi dibuka)."""
        import hashlib
        import urllib.request

        current_exe = os.path.abspath(sys.executable)
        folder = os.path.dirname(current_exe)
        new_exe = os.path.join(folder, "_update_baru.exe")
        bat_path = os.path.join(folder, "_update.bat")

        expected_hash = self._fetch_expected_sha256(exe_url)

        MAX_ATTEMPTS = 2
        ok = False
        last_err = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                self.ui(self.log,
                        f"[UPDATE] Mengunduh versi {version} "
                        f"(percobaan {attempt}/{MAX_ATTEMPTS})...", "sys")
                hasher = hashlib.sha256()
                with urllib.request.urlopen(exe_url, timeout=30) as resp:
                    if getattr(resp, "status", 200) != 200:
                        raise RuntimeError(f"HTTP {resp.status} dari server")
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    if "text/html" in ctype:
                        raise RuntimeError("server mengembalikan halaman, "
                                           "bukan file .exe (link mungkin "
                                           "lagi diperbarui — coba lagi nanti)")
                    with open(new_exe, "wb") as f:
                        while True:
                            chunk = resp.read(1024 * 256)
                            if not chunk:
                                break
                            f.write(chunk)
                            hasher.update(chunk)

                size = os.path.getsize(new_exe) if os.path.exists(new_exe) else 0
                if size < 1_000_000:  # .exe asli ukurannya puluhan MB
                    raise RuntimeError(f"file hasil unduhan cuma {size} byte "
                                       "— kemungkinan rusak/kepotong")

                digest = hasher.hexdigest()
                if expected_hash and digest != expected_hash:
                    raise RuntimeError("hash file tidak cocok (unduhan "
                                       "kemungkinan rusak di tengah jalan)")

                ok = True
                break
            except Exception as exc:          # noqa: BLE001
                last_err = exc
                try:
                    if os.path.exists(new_exe):
                        os.remove(new_exe)
                except OSError:
                    pass

        if not ok:
            self.ui(self.log, f"[UPDATE] Gagal mengunduh update dengan aman "
                              f"(dicoba {MAX_ATTEMPTS}x): {last_err} — "
                              ".exe yang sedang jalan tidak diubah, akan "
                              "dicoba lagi lain kali.", "err")
            return

        try:
            # Retry "move" beberapa kali (bukan cuma sekali) — exe lama
            # kadang belum sempat benar-benar lepas file handle-nya tepat
            # 2 detik setelah proses ini exit. Sebelum ditimpa, exe LAMA
            # disimpan dulu sebagai ".bak" (bukan langsung hilang) — jaga-
            # jaga kalau walau sudah lolos verifikasi hash di Python tadi,
            # exe baru ternyata tetap bermasalah: tinggal rename manual
            # "BOT_by_Zierra.exe.bak" -> "BOT_by_Zierra.exe" untuk balik ke
            # versi sebelumnya, tidak perlu download ulang dari nol.
            # Catatan: sengaja pakai "ping -n X 127.0.0.1 >nul" untuk jeda,
            # BUKAN "timeout /t ..." — timeout.exe butuh console untuk baca
            # input (meski /nobreak), jadi kalau tidak ada console (lihat
            # CREATE_NO_WINDOW di bawah) dia malah mengalokasikan console
            # BARU sendiri yang kelihatan sekilas (muncul jendela hitam
            # "timeout /t 2 /nobreak" pas update). ping tidak butuh itu
            # sama sekali, jadi tetap bener2 tersembunyi. (ping -n 3 ~= 2
            # detik jeda, ping -n 2 ~= 1 detik, karena ping pertama instan.)
            bat_content = (
                "@echo off\r\n"
                "ping -n 3 127.0.0.1 >nul\r\n"
                f'if exist "{current_exe}.bak" del /f /q "{current_exe}.bak"\r\n'
                f'if exist "{current_exe}" move /y "{current_exe}" "{current_exe}.bak" >nul 2>nul\r\n'
                "set tries=0\r\n"
                ":retry\r\n"
                f'move /y "{new_exe}" "{current_exe}" >nul 2>nul\r\n'
                "if exist \"" + new_exe + "\" (\r\n"
                "  set /a tries+=1\r\n"
                "  if %tries% lss 10 (\r\n"
                "    ping -n 2 127.0.0.1 >nul\r\n"
                "    goto retry\r\n"
                "  )\r\n"
                ")\r\n"
                f'start "" "{current_exe}"\r\n'
                'del "%~f0"\r\n'
            )
            with open(bat_path, "w", encoding="utf-8") as f:
                f.write(bat_content)

            import subprocess
            # CREATE_NO_WINDOW saja (TANPA DETACHED_PROCESS) — kombinasi
            # keduanya justru yang bikin anak proses (cmd/timeout) merasa
            # tidak punya console sama sekali dan mengalokasikan console
            # barunya sendiri yang kelihatan. CREATE_NO_WINDOW saja sudah
            # cukup: cmd.exe tetap dapat console (tersembunyi), jadi apa pun
            # yang dijalankan di dalamnya ikut tersembunyi juga.
            subprocess.Popen(
                ["cmd", "/c", bat_path],
                creationflags=subprocess.CREATE_NO_WINDOW,
                close_fds=True,
            )
            self.ui(self.log, "[UPDATE] Update diunduh — aplikasi akan "
                              "tertutup & terbuka ulang otomatis...", "sys")
            self.after(500, self._quit_for_update)
        except Exception as exc:              # noqa: BLE001
            self.ui(self.log, f"[UPDATE] Gagal menjadwalkan update: {exc}", "err")

    def _quit_for_update(self):
        """Keluar sebersih mungkin supaya exe lama tidak terkunci, lalu
        biarkan _update.bat (sudah berjalan di proses terpisah) menimpa
        file-nya dan membuka ulang aplikasi."""
        try:
            self.destroy()
        finally:
            os._exit(0)

    def _warm_browser(self):
        """Resolve controller browser default lebih awal, supaya saat link
        pertama masuk nanti Python tidak perlu mencari ulang browser mana
        yang harus dipakai (pencarian ini kecil, tapi tetap memakan waktu)."""
        try:
            webbrowser.get()
        except Exception:                          # noqa: BLE001
            pass

    # ------------------------------------------------------ shortcut Tawar/Terima
    def register_hotkeys(self, announce=True):
        if keyboard is None:
            self.log("[SYS] Modul 'keyboard' tidak terpasang — shortcut "
                     "Tawar/Terima tidak aktif. Jalankan: pip install keyboard",
                     "err")
            return
        try:
            if self._hotkeys_registered:
                keyboard.unhook_all_hotkeys()
            keyboard.add_hotkey(self.hotkey_terima, self._trigger_terima)
            keyboard.add_hotkey(self.hotkey_tawar, self._trigger_tawar)
            self._hotkeys_registered = True
            if announce:
                self.log(f"[SYS] Shortcut aktif — Terima: "
                         f"{self.hotkey_terima.upper()}, Tawar: "
                         f"{self.hotkey_tawar.upper()}", "sys")
        except Exception as exc:                  # noqa: BLE001
            self.log(f"[SYS] Gagal mendaftarkan shortcut: {exc} (coba jalankan "
                     "sebagai Administrator)", "err")

    def _trigger_terima(self):
        """Prioritas jalur API (instan, dari ref di pesan Telegram, tanpa
        tab sama sekali); fallback ke klik DOM (buka tab dulu) kalau
        ref/collection tidak terbaca. Dipanggil baik dari shortcut maupun
        tombol di popup, jadi popup ditutup dan hasilnya dialertkan di
        sini supaya konsisten dari dua jalur itu."""
        self._close_popup()
        job = dict(self.current_job)  # snapshot — dipakai _watch_job_status
        if job.get("ref") and job.get("collection"):
            threading.Thread(
                target=self._run_and_alert,
                args=(self.browser.confirm_via_api,
                      (job["ref"], job["collection"], job.get("url")), job),
                daemon=True,
            ).start()
        else:
            threading.Thread(
                target=self._run_and_alert,
                args=(self.browser.do_terima, (job.get("url"),), job),
                daemon=True,
            ).start()

    def _trigger_tawar(self):
        # Belum ada endpoint API untuk Tawar yang terkonfirmasi -> tetap
        # lewat klik DOM. Tab dibuka lazy di sini (bukan otomatis tiap
        # job masuk) lewat parameter url pada do_tawar. Harga estimasi
        # dipakai dari data popup (kalau sudah ada) supaya tidak scrape
        # ulang dari halaman.
        self._close_popup()
        job = self.current_job

        # Kalau jenis job ini punya "Target Tawar (%)" aktif (dicentang di
        # panel Auto Tawar), harga ditawar dihitung dari % Harga ESTIMASI
        # (BUKAN Harga Client — sempat salah pakai Harga Client, yang
        # bikin hasil tawarnya kekecilan untuk job dengan estimasi besar
        # tapi harga client relatif kecil) — bukan potongan bracket
        # Harga Estimasi seperti biasa (compute_tawar_price). Berlaku
        # juga untuk Tawar manual (F3/tombol popup), bukan cuma Auto
        # Tawar. Nama fungsinya (compute_tawar_price_from_client) tetap
        # di browser_actions.py karena rumusnya generik (harga x pct%,
        # dibulatkan ke ribuan) — cuma angka yang dioper ke sana yang
        # berubah jadi Harga Estimasi.
        harga_tawar = None
        jenis_settings = self.auto_tawar_jenis_settings.get(job.get("jenis"), {})
        if jenis_settings.get("use_target_tawar"):
            pct = jenis_settings.get("target_tawar_pct")
            estimasi_raw = job.get("harga_estimasi_raw")
            if pct not in (None, "") and estimasi_raw not in (None, ""):
                try:
                    estimasi = float(estimasi_raw)
                    # "Tawar Rentang Harga" — dicek terhadap Harga
                    # ESTIMASI. Di DALAM rentang: dasar persentase
                    # dinaikkan jadi Harga Max (jadi persentase >100%
                    # menjamin tawar di atas Harga Max). Di LUAR rentang
                    # dan "Di luar rentang: tawar default" dicentang:
                    # harga_tawar dibiarkan None -> do_tawar memakai
                    # logika default (bracket di bawah Harga Estimasi),
                    # sehingga persentase >100% tidak ikut berlaku.
                    rmin = jenis_settings.get("price_range_min")
                    rmax = jenis_settings.get("price_range_max")
                    use_range = (jenis_settings.get("use_price_range")
                                 and rmin not in (None, "")
                                 and rmax not in (None, ""))
                    if use_range and float(rmin) <= estimasi <= float(rmax):
                        harga_tawar = compute_tawar_price_from_client(
                            float(rmax), pct)
                        self._safe_log(
                            f"[TAWAR] Estimasi Rp{int(estimasi)} di dalam "
                            f"rentang — dasar Rp{int(float(rmax))} x {pct}%",
                            "sys")
                    elif use_range and jenis_settings.get("outside_range_default"):
                        harga_tawar = None
                        self._safe_log(
                            f"[TAWAR] Estimasi Rp{int(estimasi)} di luar "
                            "rentang — pakai tawar default (di bawah "
                            "Harga Estimasi)", "sys")
                    else:
                        harga_tawar = compute_tawar_price_from_client(
                            estimasi, pct)
                except (TypeError, ValueError):
                    harga_tawar = None

        # LANTAI: harga tawar TIDAK boleh lebih rendah dari Harga Client.
        # Logika tawar default (bracket di bawah Harga Estimasi) dihitung
        # di sini (bukan di do_tawar) supaya ikut dicek — sebelumnya job
        # dengan estimasi Rp80.924 dan client Rp70.000 ditawar Rp65.000,
        # lebih rendah dari harga client. Berlaku untuk semua jalur tawar
        # (persentase, rentang harga, default, juga Tawar manual F3).
        try:
            client_price = float(job.get("harga_client_raw"))
        except (TypeError, ValueError):
            client_price = None
        if harga_tawar is None and job.get("harga_estimasi_raw") not in (None, ""):
            try:
                estimasi_val = float(job.get("harga_estimasi_raw"))
                floor_price = jenis_settings.get("default_floor_price")
                # Pengaturan "Tawar default minimal": kalau Harga Estimasi
                # DAN Harga Client sama-sama di bawah angka ini, tawar
                # default jadi angka itu (mis. 100rb).
                if (jenis_settings.get("use_default_floor")
                        and floor_price not in (None, "")
                        and client_price is not None
                        and estimasi_val < float(floor_price)
                        and client_price < float(floor_price)):
                    harga_tawar = int(float(floor_price))
                    self._safe_log(
                        f"[TAWAR] Estimasi Rp{int(estimasi_val)} & Client "
                        f"Rp{int(client_price)} di bawah Rp{harga_tawar} — "
                        f"tawar default jadi Rp{harga_tawar}", "sys")
                else:
                    harga_tawar = compute_tawar_price(estimasi_val)
            except (TypeError, ValueError):
                harga_tawar = None
        if (client_price is not None and harga_tawar is not None
                and harga_tawar < client_price):
            harga_lantai = int(round(client_price))
            self._safe_log(
                f"[TAWAR] Harga tawar Rp{int(harga_tawar)} lebih rendah dari "
                f"Harga Client Rp{harga_lantai} — dinaikkan jadi "
                f"Rp{harga_lantai}", "sys")
            harga_tawar = harga_lantai

        # Snapshot job supaya tawar yang BERHASIL ikut dipantau
        # (_watch_job_status -> Catatan Hari Ini) begitu client setuju &
        # bayar sampai status masuk proses — sebelumnya cuma Terima yang
        # dipantau, jadi hasil tawar tidak pernah tercatat.
        snapshot = dict(job)
        snapshot["watch_mode"] = "tawar"
        snapshot["harga_tawar_fmt"] = (
            "Rp" + format(int(harga_tawar), ",").replace(",", ".")
            if harga_tawar else None)
        threading.Thread(
            target=self._run_and_alert,
            args=(self.browser.do_tawar,
                  (job.get("url"), job.get("harga_estimasi_raw"), harga_tawar),
                  snapshot),
            daemon=True,
        ).start()

    def _run_and_alert(self, fn, args, job_snapshot=None):
        """Jalankan aksi browser (Terima/Tawar) lalu tampilkan hasilnya
        sebagai alert — sama isinya dengan yang tertulis di log, supaya
        tidak perlu lihat log untuk tahu berhasil atau tidak. Kalau
        job_snapshot diisi (dipakai Terima) dan berhasil, mulai pantau
        status job itu untuk fitur Catatan Job Proses."""
        success, message = fn(*args)
        self.ui(self._show_result_alert, success, message)
        if success and job_snapshot:
            threading.Thread(target=self._watch_job_status,
                             args=(job_snapshot,), daemon=True).start()

    def _show_result_alert(self, success, message):
        if success:
            messagebox.showinfo("Berhasil", message)
        else:
            messagebox.showerror("Gagal", message)

    def _close_popup(self):
        if getattr(self, "job_popup", None) and self.job_popup.winfo_exists():
            self.job_popup.destroy()

    def save_hotkeys(self):
        new_terima = (self._pending_hotkey_terima or "f2").strip().lower()
        new_tawar = (self._pending_hotkey_tawar or "f3").strip().lower()
        self.hotkey_terima = new_terima
        self.hotkey_tawar = new_tawar
        self.save_config(self.api_id, self.api_hash, new_terima, new_tawar)
        self.register_hotkeys()

    # ------------------------------------------------------ handler pesan
    def register_handler(self):
        client = self.worker.client

        @client.on(events.NewMessage(chats=TARGET_CHAT))
        async def handler(event):                  # noqa: ANN001
            text = event.raw_text

            # Pesan *PAID*/*DONE* dicocokkan ke job yang sudah KITA terima
            # atau tawar (lewat Ref) untuk fitur Catatan Job Proses — lebih
            # cepat dari polling 45 detik, dan tidak tersangkut kalau
            # statusnya lama berubah di server. Job orang lain (ref tidak
            # kita kenal) diabaikan. Dicek PALING DULU, SEBELUM cek pause:
            # pencatatan harus tetap jalan walau listener sedang di-Pause
            # (Pause hanya menghentikan job baru & aksi otomatis, bukan
            # pemantauan job yang sudah diterima/ditawar).
            if LABEL_PAID in text or LABEL_DONE in text:
                self.ui(self._handle_status_broadcast, text, LABEL_PAID in text)
                return

            if self.is_paused:
                return

            # Tawaran kita ditolak client — lihat _handle_reject_nego.
            if LABEL_REJECT in text.upper():
                self.ui(self._handle_reject_nego, text)
                return

            if LABEL not in text:
                return

            match = URL_PATTERN.search(text)
            if not match:
                return

            url = match.group(0)
            if url in self.opened_urls:
                self.ui(self.log, f"[SKIP] sudah pernah dibuka: {url}", "skip")
                return

            self.opened_urls.add(url)

            ref_match = REF_PATTERN.search(text)
            ref = ref_match.group(1) if ref_match else None
            collection = ref_to_collection(ref)
            id_match = ID_PATTERN.search(url)
            job_id = id_match.group(1) if id_match else None

            if ref and job_id:
                self.known_jobs[ref] = {
                    "id": job_id, "collection": collection, "url": url,
                }

            self.current_job = {
                "ref": ref, "id": job_id, "collection": collection, "url": url,
            }
            self.poll_token += 1
            token = self.poll_token

            # Tidak lagi membuka tab visual di sini — Terima sepenuhnya
            # lewat API (tidak butuh tab sama sekali). Tab hanya dibuka
            # lazy saat Tawar benar-benar ditekan (lihat _trigger_tawar),
            # supaya tidak ada dua operasi Selenium rebutan di saat
            # bersamaan.
            threading.Thread(
                target=self._load_job_summary,
                args=(job_id, collection, url, token),
                daemon=True,
            ).start()

            # Jenis bisa langsung ditebak dari prefix Ref (tidak perlu
            # menunggu API) supaya pola bunyi notifikasinya secepat
            # mungkin — lihat REF_JENIS_LABEL & play_sound().
            jenis_cepat = REF_JENIS_LABEL.get(
                ref.split("-")[0].upper()) if ref else None
            # Thread terpisah — winsound.Beep() memblokir thread pemanggil
            # selama pola bunyinya, dan handler ini jalan di event loop
            # asyncio Telethon sendiri (jangan sampai menunda pesan lain).
            threading.Thread(target=self.play_sound, args=(jenis_cepat,),
                             daemon=True).start()
            self.ui(
                self.log,
                f"[MASUK] {url}  ({self.hotkey_terima.upper()}=Terima  "
                f"{self.hotkey_tawar.upper()}=Tawar)",
                "hit",
            )

    def _load_job_summary(self, job_id, collection, url=None, token=None):
        """Ambil ringkasan lewat API dan tampilkan popup secepat mungkin,
        lalu terus ulangi kalau ada field yang belum siap di server (mis.
        Harga Estimasi yang dihitung sesaat setelah order dibuat) —
        popup ter-update sendiri sampai lengkap. Berhenti kalau: lengkap,
        error keras (token/koneksi), job baru sudah masuk (token basi),
        atau lewat batas waktu aman.

        Tab visual dibuka di THREAD TERPISAH sejak awal (paralel, bukan
        menunggu loop ini selesai) — dua channel Selenium (api/view)
        memang independen, jadi tidak ada alasan menunggu satu sama lain."""
        if url:
            threading.Thread(target=self._open_job, args=(url,), daemon=True).start()

        if not (job_id and collection):
            self.ui(self.log, "[API] Ref/ID job tidak terbaca dari pesan — "
                              "ringkasan otomatis dilewati.", "err")
            return

        deadline = time.time() + 25  # batas aman, bukan target normal
        while True:
            info, complete, hard_error = self.browser.fetch_order_detail(
                job_id, collection, url
            )
            if token != self.poll_token:
                return  # job lain sudah masuk, popup ini sudah tidak relevan

            self._update_current_job(info)
            self.ui(self.show_job_popup, info, token)
            self._maybe_auto_actions(info, token)

            if complete or hard_error or time.time() > deadline:
                break
            time.sleep(0.4)  # dulu 1 detik — dipercepat biar deteksi lebih cepat

    def _update_current_job(self, info):
        """Simpan nilai mentah terbaru ke current_job, dipakai _trigger_tawar
        (hitung harga tawar) dan _check_auto_terima (evaluasi syarat)."""
        if self.current_job.get("ref") and self.current_job["ref"] != info.get("ref"):
            return  # info dari job lain (kejar-kejaran token), abaikan
        self.current_job["harga_client_raw"] = info.get("harga_client_raw")
        self.current_job["harga_estimasi_raw"] = info.get("harga_estimasi_raw")
        self.current_job["target_raw"] = info.get("target_raw")
        self.current_job["word_count_raw"] = info.get("word_count_raw")
        self.current_job["deadline_dt"] = info.get("deadline_dt")
        self.current_job["jenis"] = info.get("jenis")
        # Dipakai fitur Catatan Job Proses (_watch_job_status) — status
        # & harga client berformat di saat Terima ditekan, dipakai sebagai
        # titik awal perbandingan status.
        self.current_job["status"] = info.get("status")
        self.current_job["harga_client"] = info.get("harga_client")
        # Dipakai fitur Catatan Job Proses (_add_job_note) — deadline
        # berformat teks, ditampilkan di popup Catatan Hari Ini.
        self.current_job["deadline"] = info.get("deadline")

    def _open_job(self, url):
        """Tampilkan halaman detail job di tab Chrome debug. Sengaja TIDAK
        ada fallback ke browser biasa: kalau gagal, penyebabnya sudah
        tercatat di log oleh open_job()."""
        self.browser.open_job(url)

    # ------------------------------------------------------ auto terima
    def _evaluate_auto_conditions(self, info, settings):
        """Evaluasi syarat (perbandingan harga/deadline/target/dll) —
        generik, dipakai baik oleh Auto Terima maupun Auto Tawar, cuma
        beda sumber `settings`-nya (lihat _check_auto_terima/_check_auto_tawar)."""
        jenis = info.get("jenis")
        try:
            client = float(info.get("harga_client_raw"))
            estimasi = float(info.get("harga_estimasi_raw"))
        except (TypeError, ValueError):
            return False
        if estimasi <= 0:
            return False

        # Syarat 1 (semua jenis): perbandingan harga client vs estimasi (%).
        # Pakai "<" (bukan "<=") supaya job yang persentasenya PAS SAMA
        # dengan nilai minimal tetap lolos, bukan malah ditolak.
        min_client_pct = settings.get("min_client_pct")
        if min_client_pct not in (None, "") and (client / estimasi) * 100 < float(min_client_pct):
            return False

        # Syarat 1b (semua jenis): harga client ABSOLUT minimal (Rp),
        # terpisah dari perbandingan % di atas — mis. client minimal
        # Rp100.000 berapa pun persentasenya terhadap estimasi.
        min_client_price = settings.get("min_client_price")
        if min_client_price not in (None, "") and client < float(min_client_price):
            return False

        # Syarat 2 (semua jenis): rentang deadline.
        deadline_dt = info.get("deadline_dt")
        if deadline_dt is None:
            return False
        from datetime import datetime as _dt
        hours_left = (deadline_dt - _dt.now(deadline_dt.tzinfo)).total_seconds() / 3600
        dmin, dmax = settings.get("deadline_min_h"), settings.get("deadline_max_h")
        if dmin not in (None, "") and dmax not in (None, ""):
            if not (float(dmin) <= hours_left <= float(dmax)):
                return False

        if jenis in ("Parafrase", "Humanizer"):
            try:
                target = float(info.get("target_raw"))
            except (TypeError, ValueError):
                return False
            min_target = settings.get("min_target")
            if min_target not in (None, "") and target < float(min_target):
                return False

            if jenis == "Humanizer":
                min_words = settings.get("min_word_count")
                max_words = settings.get("max_word_count")
                if min_words not in (None, "") or max_words not in (None, ""):
                    try:
                        words = float(info.get("word_count_raw"))
                    except (TypeError, ValueError):
                        return False
                    if min_words not in (None, "") and words < float(min_words):
                        return False
                    if max_words not in (None, "") and words >= float(max_words):
                        return False

        elif jenis == "Fix File":
            service_list = info.get("service_list") or []
            norm_services = {s.strip().lower() for s in service_list}

            # Syarat 3: service yang dipilih (include). Job dengan BANYAK
            # layanan tetap diperbolehkan selama SALAH SATU dari layanan
            # yang dicentang ada di job itu — bukan harus semuanya cocok.
            include = settings.get("service_include") or []
            if include:
                norm_include = {s.strip().lower() for s in include}
                if not (norm_services & norm_include):
                    return False

            # Syarat 4: service yang dihindari (exclude). Kalau job multi
            # layanan dan salah satunya ada di daftar ini, tolak.
            exclude = settings.get("service_exclude") or []
            if exclude:
                norm_exclude = {s.strip().lower() for s in exclude}
                if norm_services & norm_exclude:
                    return False

        return True

    def _check_auto_terima(self, info):
        """Evaluasi syarat Auto Terima untuk jenis job ini. Sekarang tiap
        jenis (Parafrase/Humanizer/Fix File) punya syarat sendiri-sendiri
        dan bisa aktif bersamaan — jenis job yang masuk cuma dicocokkan ke
        syarat miliknya sendiri, bukan satu syarat global lagi."""
        if not self.auto_enabled:
            return False
        jenis = info.get("jenis")
        settings = self.auto_jenis_settings.get(jenis)
        if not settings or not settings.get("enabled"):
            return False
        return self._evaluate_auto_conditions(info, settings)

    def _evaluate_auto_tawar_conditions(self, info, settings):
        """Syarat Auto Tawar beda ARAH dari Auto Terima (lihat
        _evaluate_auto_conditions): target berbentuk RENTANG (min & max,
        bukan cuma "di atas X"), dan harga client disyaratkan DI BAWAH
        persentase tertentu dari estimasi — ini memang fallback untuk job
        yang kurang bagus buat Terima langsung tapi masih worth ditawar.
        Deadline & syarat service Fix File tetap sama logikanya."""
        jenis = info.get("jenis")
        try:
            client = float(info.get("harga_client_raw"))
            estimasi = float(info.get("harga_estimasi_raw"))
        except (TypeError, ValueError):
            return False
        if estimasi <= 0:
            return False

        # Syarat harga (semua jenis): client harus DI BAWAH persentase ini.
        max_client_pct = settings.get("max_client_pct")
        if max_client_pct not in (None, "") and (client / estimasi) * 100 >= float(max_client_pct):
            return False

        # Syarat deadline (semua jenis) — sama seperti Auto Terima.
        deadline_dt = info.get("deadline_dt")
        if deadline_dt is None:
            return False
        from datetime import datetime as _dt
        hours_left = (deadline_dt - _dt.now(deadline_dt.tzinfo)).total_seconds() / 3600
        dmin, dmax = settings.get("deadline_min_h"), settings.get("deadline_max_h")
        if dmin not in (None, "") and dmax not in (None, ""):
            if not (float(dmin) <= hours_left <= float(dmax)):
                return False

        if jenis in ("Parafrase", "Humanizer"):
            try:
                target = float(info.get("target_raw"))
            except (TypeError, ValueError):
                return False
            tmin = settings.get("target_min")
            if tmin not in (None, "") and target < float(tmin):
                return False
            tmax = settings.get("target_max")
            if tmax not in (None, "") and target > float(tmax):
                return False

            if jenis == "Humanizer":
                min_words = settings.get("min_word_count")
                max_words = settings.get("max_word_count")
                if min_words not in (None, "") or max_words not in (None, ""):
                    try:
                        words = float(info.get("word_count_raw"))
                    except (TypeError, ValueError):
                        return False
                    if min_words not in (None, "") and words < float(min_words):
                        return False
                    if max_words not in (None, "") and words >= float(max_words):
                        return False

        elif jenis == "Fix File":
            service_list = info.get("service_list") or []
            norm_services = {s.strip().lower() for s in service_list}

            include = settings.get("service_include") or []
            if include:
                norm_include = {s.strip().lower() for s in include}
                if not (norm_services & norm_include):
                    return False

            exclude = settings.get("service_exclude") or []
            if exclude:
                norm_exclude = {s.strip().lower() for s in exclude}
                if norm_services & norm_exclude:
                    return False

        return True

    def _check_auto_tawar(self, info):
        """Sama seperti _check_auto_terima tapi pakai syarat Auto Tawar
        (self.auto_tawar_jenis_settings) yang terpisah total, dan evaluasi
        arah syaratnya (_evaluate_auto_tawar_conditions) beda dari Auto
        Terima — lihat komentar di fungsi itu."""
        if not self.auto_tawar_enabled:
            return False
        jenis = info.get("jenis")
        settings = self.auto_tawar_jenis_settings.get(jenis)
        if not settings or not settings.get("enabled"):
            return False
        return self._evaluate_auto_tawar_conditions(info, settings)

    def _maybe_auto_actions(self, info, token):
        """Auto Tawar dicek DULUAN; Auto Terima jadi fallback — hanya
        dicek kalau job ini tidak lolos syarat Auto Tawar. Satu token
        dedupe dipakai bersama supaya satu job cuma pernah memicu SALAH
        SATU aksi otomatis, tidak dua-duanya. (Kalau tawaran ditolak
        client, Auto Terima dicoba lagi lewat _handle_reject_nego.)"""
        if self.auto_action_fired_token == token:
            return
        if self._check_auto_tawar(info):
            self.auto_action_fired_token = token
            threading.Thread(
                target=self._auto_tawar_delayed, args=(token,), daemon=True
            ).start()
            return
        if self._check_auto_terima(info):
            self.auto_action_fired_token = token
            threading.Thread(
                target=self._auto_terima_delayed, args=(token,), daemon=True
            ).start()

    # ------------------------------------------------------ REJECT NEGO
    def _handle_reject_nego(self, text):
        """Pesan REJECT NEGO = client menolak tawaran kita, dan status
        order akan kembali ke waiting-confirmation. Kalau job itu
        memenuhi syarat Auto Terima, langsung di-Terima begitu statusnya
        sudah kembali waiting-confirmation (kalau di-Terima sebelum itu,
        server menolak: "Status orderan tidak sesuai")."""
        ref_match = REF_PATTERN.search(text) or REF_FALLBACK_PATTERN.search(text)
        ref = ref_match.group(1) if ref_match else None
        if not ref:
            self.log("[REJECT] Pesan REJECT NEGO masuk tapi Ref tidak "
                     "terbaca: " + text[:120].replace("\n", " | "), "err")
            return
        if not self.auto_enabled:
            self.log(f"[REJECT] Tawaran {ref} ditolak client (Auto Terima "
                     "nonaktif — dilewati).", "skip")
            return
        known = self.known_jobs.get(ref, {})
        url_match = URL_PATTERN.search(text)
        url = url_match.group(0) if url_match else known.get("url")
        id_match = ID_PATTERN.search(url) if url else None
        job_id = id_match.group(1) if id_match else known.get("id")
        collection = ref_to_collection(ref) or known.get("collection")
        if not (job_id and collection):
            self.log(f"[REJECT] Tawaran {ref} ditolak client, tapi ID job "
                     "tidak ditemukan (pesan tanpa link & job tidak dikenal "
                     "di sesi ini) — dilewati.", "err")
            return
        if ref in self._reject_inflight:
            return  # ref ini sedang diproses
        self._reject_inflight.add(ref)
        self.log(f"[REJECT] Tawaran {ref} ditolak client — menunggu status "
                 "kembali waiting-confirmation...", "sys")
        threading.Thread(
            target=self._reject_nego_worker,
            args=(ref, job_id, collection, url), daemon=True,
        ).start()

    def _reject_nego_worker(self, ref, job_id, collection, url):
        try:
            deadline = time.time() + 60
            info = None
            while time.time() < deadline:
                if self.is_paused:
                    return
                cur, complete, hard_error = self.browser.fetch_order_detail(
                    job_id, collection, url)
                if hard_error:
                    self.ui(self.log, f"[REJECT] {ref}: gagal ambil data "
                                      "job — dibatalkan.", "err")
                    return
                status = (cur.get("status") or "").strip().lower().replace("_", "-")
                if status == "waiting-confirmation" and complete:
                    info = cur
                    break
                time.sleep(1.2)
            if info is None:
                self.ui(self.log, f"[REJECT] {ref}: status tidak kembali "
                                  "waiting-confirmation dalam 60 detik — "
                                  "dilewati.", "skip")
                return
            if not self._check_auto_terima(info):
                self.ui(self.log, f"[REJECT] {ref} tidak memenuhi syarat "
                                  "Auto Terima — dilewati.", "skip")
                return

            lo, hi = self.auto_delay_min_ms / 1000, self.auto_delay_max_ms / 1000
            if hi > lo:
                time.sleep(random.uniform(lo, hi))
            elif hi > 0:
                time.sleep(hi)
            if self.is_paused:
                return

            self.ui(self.log, f"[AUTO] {ref} ditolak client tapi memenuhi "
                              "syarat Auto Terima — Terima otomatis...", "sys")
            snapshot = {
                "ref": ref, "id": job_id, "collection": collection,
                "url": url, "jenis": info.get("jenis"),
                "status": info.get("status"),
                "harga_client": info.get("harga_client"),
                "deadline": info.get("deadline"),
            }
            self._run_and_alert(self.browser.confirm_via_api,
                                (ref, collection, url), snapshot)
        finally:
            self._reject_inflight.discard(ref)

    def _auto_terima_delayed(self, token):
        # Jeda acak dalam ms (bisa diatur di panel). Isi 0-0 untuk tanpa
        # jeda sama sekali — angka lama (2-3 detik) ternyata cukup lama
        # untuk membuat status order sudah berubah duluan.
        lo, hi = self.auto_delay_min_ms / 1000, self.auto_delay_max_ms / 1000
        if hi > lo:
            time.sleep(random.uniform(lo, hi))
        elif hi > 0:
            time.sleep(hi)
        if token != self.poll_token:
            return  # job sudah berganti selama jeda, batalkan
        self.ui(self.log, "[AUTO] Syarat Auto Terima terpenuhi — Terima "
                          "otomatis...", "sys")
        self.ui(self._trigger_terima)

    def _auto_tawar_delayed(self, token):
        lo = self.auto_tawar_delay_min_ms / 1000
        hi = self.auto_tawar_delay_max_ms / 1000
        if hi > lo:
            time.sleep(random.uniform(lo, hi))
        elif hi > 0:
            time.sleep(hi)
        if token != self.poll_token:
            return  # job sudah berganti selama jeda, batalkan
        self.ui(self.log, "[AUTO] Syarat Auto Tawar terpenuhi — Tawar "
                          "otomatis...", "sys")
        self.ui(self._trigger_tawar)

    # ------------------------------------------------------ popup ringkasan job
    def _popup_row_spec(self, jenis):
        """Susunan baris popup beda per jenis job. kind "text" -> label
        biasa, kind "bullets" -> daftar poin (dipakai Fix File)."""
        if jenis == "Fix File":
            return [
                ("jenis", "Jenis", "text"),
                ("status", "Status", "text"),
                ("harga_client", "Harga Client", "text"),
                ("harga_estimasi", "Harga Estimasi", "text"),
                ("service", "Layanan", "bullets"),   # menggantikan Target
                ("deadline", "Deadline", "text"),
            ]
        if jenis == "Humanizer":
            return [
                ("jenis", "Jenis", "text"),
                ("status", "Status", "text"),
                ("harga_client", "Harga Client", "text"),
                ("harga_estimasi", "Harga Estimasi", "text"),
                ("target", "Target", "text"),
                ("deadline", "Deadline", "text"),
                ("word_count", "Jumlah Kata", "text"),
            ]
        return [
            ("jenis", "Jenis", "text"),
            ("status", "Status", "text"),
            ("harga_client", "Harga Client", "text"),
            ("harga_estimasi", "Harga Estimasi", "text"),
            ("target", "Target", "text"),
            ("deadline", "Deadline", "text"),
        ]

    def show_job_popup(self, info, token=None):
        """Tampilkan popup, atau kalau popup job ini sudah terbuka (token
        sama), cukup update isinya di tempat — dipanggil berulang selama
        polling _load_job_summary sampai data lengkap. Susunan baris
        ditentukan sekali di awal dari jenis job (tidak berubah lagi
        selama polling job yang sama)."""
        same_job = (
            getattr(self, "job_popup", None)
            and self.job_popup.winfo_exists()
            and getattr(self, "job_popup_token", None) == token
        )
        if same_job:
            self._update_job_popup(info)
            return

        if getattr(self, "job_popup", None) and self.job_popup.winfo_exists():
            self.job_popup.destroy()

        self.job_popup_token = token
        win = tk.Toplevel(self, bg=CARD)
        win.title("Job Baru")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.configure(bg=CARD)
        self.job_popup = win

        # Popup bisa digeser bebas (title bar bawaan OS) — posisi terakhir
        # dipakai lagi untuk popup berikutnya.
        if self.popup_position:
            win.geometry(f"+{self.popup_position[0]}+{self.popup_position[1]}")
        win.bind("<Configure>", self._on_popup_move)
        win.bind("<Destroy>", self._on_popup_destroyed)

        pad = tk.Frame(win, bg=CARD, padx=20, pady=18)
        pad.pack(fill="both", expand=True)

        tk.Label(pad, text="Job Baru Masuk", bg=CARD, fg=TEXT,
                 font=(FONT, 13, "bold")).pack(anchor="w", pady=(0, 12))

        rows_frame = tk.Frame(pad, bg=CARD)
        rows_frame.pack(fill="x")
        self.job_popup_value_labels = {}
        self.job_popup_bullet_frames = {}

        for key, label, kind in self._popup_row_spec(info.get("jenis")):
            row = tk.Frame(rows_frame, bg=CARD)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label.upper(), bg=CARD, fg=MUTED,
                     font=(FONT, 8, "bold"), width=13, anchor="nw").pack(side="left")

            if kind == "bullets":
                bframe = tk.Frame(row, bg=CARD)
                bframe.pack(side="left", fill="x", expand=True)
                self.job_popup_bullet_frames[key] = bframe
            else:
                value_lbl = tk.Label(row, text="-", bg=CARD, fg=TEXT,
                                     font=(FONT, 11, "bold"), anchor="w")
                value_lbl.pack(side="left")
                self.job_popup_value_labels[key] = value_lbl

        btn_row = tk.Frame(pad, bg=CARD)
        btn_row.pack(fill="x", pady=(18, 0))

        RoundedButton(
            btn_row, f"Terima ({self.hotkey_terima.upper()})",
            self._trigger_terima,
            width=155, fill=GREEN, bg=CARD,
        ).pack(side="left")

        RoundedButton(
            btn_row, f"Tawar ({self.hotkey_tawar.upper()})",
            self._trigger_tawar,
            width=155, fill=AMBER, bg=CARD,
        ).pack(side="right")

        self._update_job_popup(info)

    def _on_popup_move(self, event):
        win = getattr(self, "job_popup", None)
        if win and win.winfo_exists():
            self.popup_position = (win.winfo_x(), win.winfo_y())

    def _on_popup_destroyed(self, event):
        if self.popup_position:
            self.save_popup_position(*self.popup_position)

    def _relative_deadline(self, deadline_dt):
        """'3 jam lagi' / '5 hari lagi' / '2 jam lalu' kalau sudah lewat."""
        if deadline_dt is None:
            return ""
        from datetime import datetime as _dt
        delta_s = (deadline_dt - _dt.now(deadline_dt.tzinfo)).total_seconds()
        lewat = delta_s < 0
        delta_s = abs(delta_s)
        days = int(delta_s // 86400)
        hours = int((delta_s % 86400) // 3600)
        minutes = int((delta_s % 3600) // 60)
        if days >= 1:
            text = f"{days} hari"
        elif hours >= 1:
            text = f"{hours} jam"
        else:
            text = f"{minutes} menit"
        return f"{text} lalu" if lewat else f"{text} lagi"

    def _update_job_popup(self, info):
        if not (getattr(self, "job_popup", None) and self.job_popup.winfo_exists()):
            return

        deadline_text = info.get("deadline") or "-"
        relatif = self._relative_deadline(info.get("deadline_dt"))
        if relatif:
            deadline_text = f"{deadline_text}\n({relatif})"

        values = {
            "jenis": info.get("jenis") or "-",
            "status": info.get("status") or "-",
            "harga_client": info.get("harga_client") or "-",
            "harga_estimasi": info.get("harga_estimasi") or "-",
            "target": info.get("target") or "-",
            "deadline": deadline_text,
            "word_count": info.get("word_count") or "-",
        }
        for key, widget in self.job_popup_value_labels.items():
            widget.config(text=values.get(key, "-"), justify="left")

        for key, frame in self.job_popup_bullet_frames.items():
            for child in frame.winfo_children():
                child.destroy()
            items = info.get("service_list") or []
            if items:
                for item in items:
                    tk.Label(
                        frame, text=f"\u2022  {item}", bg=CARD, fg=TEXT,
                        font=(FONT, 10), anchor="w", justify="left",
                        wraplength=260,
                    ).pack(fill="x", anchor="w")
            else:
                tk.Label(frame, text="-", bg=CARD, fg=TEXT,
                         font=(FONT, 11, "bold"), anchor="w").pack(anchor="w")

    # Pola beep singkat SEBELUM notif.wav, beda per jenis — supaya bisa
    # langsung tahu jenis job tanpa baca popup dulu. Jumlah beep & nada
    # sengaja beda jauh biar gampang dibedakan sambil lalu. Tidak ada
    # file suara terpisah per jenis, jadi pakai winsound.Beep (nada
    # sistem) yang bisa diatur frekuensinya langsung dari kode.
    SOUND_PATTERN = {
        "Parafrase": (1, 1200),
        "Humanizer": (2, 1500),
        "Fix File": (3, 900),
    }

    def play_sound(self, jenis=None):
        if winsound is None:
            return
        try:
            self._set_wave_volume(self.sound_volume)
            pattern = self.SOUND_PATTERN.get(jenis)
            if pattern:
                count, freq = pattern
                for i in range(count):
                    winsound.Beep(freq, 70)
                    if i < count - 1:
                        time.sleep(0.05)
            winsound.PlaySound(SOUND_FILE,
                               winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as exc:                   # noqa: BLE001
            self.ui(self.log, f"[audio] {exc}", "err")

    # ------------------------------------------------------ watchdog koneksi
    def start_watchdog(self):
        """Pantau status koneksi terus-menerus dan sambung ulang bila putus.

        Telethon dikonfigurasi retry tanpa batas (connection_retries=None),
        tapi watchdog ini menambah lapisan kedua: kalau karena sesuatu hal
        client benar-benar disconnect (mis. laptop sleep, WiFi mati lama),
        watchdog yang memaksa connect() lagi begitu jaringan kembali,
        sekaligus memberi tahu kamu lewat status pill dan log.
        """
        if self.watchdog_running:
            return
        self.watchdog_running = True
        client = self.worker.client

        async def loop():
            while True:
                await asyncio.sleep(WATCHDOG_INTERVAL)

                if not self.watchdog_active:
                    # listener sedang dijeda -> jangan pantau/reconnect
                    continue

                connected = client.is_connected()

                if connected and not self.online:
                    # baru saja tersambung kembali
                    self.ui(self._set_online, True)
                    self.ui(self.log, "[SYS] Koneksi tersambung kembali.", "sys")

                elif not connected:
                    if self.online:
                        self.ui(self._set_online, False)
                        self.ui(self.log, "[SYS] Koneksi terputus, mencoba "
                                          "menyambung ulang...", "err")
                    try:
                        await client.connect()
                    except Exception as exc:        # noqa: BLE001
                        self.ui(self.log, f"[SYS] Gagal menyambung ulang: {exc}",
                                "err")

        self.worker.submit(loop())

    def _set_online(self, value):
        self.online = value
        self.refresh_buttons()

    # ------------------------------------------------------ tutup
    def on_close(self):
        if messagebox.askokcancel("Keluar", "Tutup listener?"):
            self.worker.shutdown()
            self.destroy()


if __name__ == "__main__":
    App().mainloop()
