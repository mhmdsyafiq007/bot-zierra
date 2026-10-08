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

from browser_actions import BrowserActions


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
URL_PATTERN = re.compile(r"https?://\S+")
REF_PATTERN = re.compile(r"Ref:\s*(\S+)")
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
APP_VERSION = "1.0.0"
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

    def __init__(self, parent, label, show=None):
        super().__init__(parent, bg=CARD)
        tk.Label(self, text=label.upper(), bg=CARD, fg=MUTED,
                 font=(FONT, 8, "bold"), anchor="w").pack(fill="x", pady=(0, 5))

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

        # Seluruh isi "body" dibungkus Canvas+Scrollbar supaya bisa
        # digulir kalau jendela terlalu kecil/pendek untuk menampung
        # semua panel sekaligus (mis. Auto Terima dengan banyak syarat).
        # Scrollbar sendiri baru dipasang kalau memang diperlukan (lihat
        # _on_body_scroll), supaya tidak selalu kelihatan kalau isinya pas.
        self.body_canvas = tk.Canvas(inner, bg=CARD, highlightthickness=0, bd=0)
        self.body_scroll = ttk.Scrollbar(inner, orient="vertical",
                                         command=self.body_canvas.yview,
                                         style="Dark.Vertical.TScrollbar")
        self.body_canvas.configure(yscrollcommand=self._on_body_scroll)
        self.body_canvas.pack(side="left", fill="both", expand=True)

        self.body = tk.Frame(self.body_canvas, bg=CARD, padx=22, pady=20)
        self.body_window = self.body_canvas.create_window(
            (0, 0), window=self.body, anchor="nw"
        )
        self.body.bind("<Configure>", self._on_body_content_resize)
        self.body_canvas.bind("<Configure>", self._on_body_canvas_resize)

        # Scroll roda mouse hanya aktif selagi kursor di atas body, supaya
        # tidak "mencuri" scroll widget lain (mis. dropdown) saat lewat.
        self.body_canvas.bind("<Enter>", lambda e: self._bind_mousewheel())
        self.body_canvas.bind("<Leave>", lambda e: self._unbind_mousewheel())

        self.footer = tk.Label(self, text="", bg=BG, fg=MUTED, font=(FONT, 8))
        self.footer.pack(pady=(0, 8))

    def _on_body_content_resize(self, event=None):
        self.body_canvas.configure(scrollregion=self.body_canvas.bbox("all"))

    def _on_body_canvas_resize(self, event):
        # Isi body dipaksa selebar canvas -> elemen ber-sticky "ew" di
        # dalamnya benar-benar bisa melebar penuh mengikuti jendela,
        # tidak lagi mepet/mentok di kiri saat jendela dilebarkan.
        self.body_canvas.itemconfig(self.body_window, width=event.width)

    def _on_body_scroll(self, first, last):
        """Dipanggil tiap scrollregion berubah — scrollbar cuma dipasang
        kalau isi body memang lebih panjang dari area yang kelihatan."""
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.body_scroll.pack_forget()
        else:
            self.body_scroll.pack(side="right", fill="y")
        self.body_scroll.set(first, last)

    def _bind_mousewheel(self):
        self.body_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self):
        self.body_canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event):
        self.body_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

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

        self.f_api_id = Field(self.body, "API ID")
        self.f_api_id.pack(fill="x", pady=(0, 14))
        self.f_api_hash = Field(self.body, "API Hash")
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

        self.f_phone = Field(self.body, "Nomor telepon")
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

        self.f_code = Field(self.body, "Kode verifikasi")
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

        self.f_password = Field(self.body, "Password 2FA", show="\u2022")
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

        tk.Label(settings_row, text="SHORTCUT TERIMA", bg=CARD, fg=MUTED,
                 font=(FONT, 7, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(settings_row, text="SHORTCUT TAWAR", bg=CARD, fg=MUTED,
                 font=(FONT, 7, "bold")).grid(row=0, column=1, sticky="w",
                                              padx=(14, 0))

        self.e_hotkey_terima = tk.Entry(settings_row, bg=FIELD, fg=TEXT,
                                        relief="flat", width=8, font=(FONT, 10),
                                        insertbackground=ACCENT)
        self.e_hotkey_terima.insert(0, self.hotkey_terima)
        self.e_hotkey_terima.grid(row=1, column=0, sticky="w", pady=(3, 0),
                                  ipady=3)

        self.e_hotkey_tawar = tk.Entry(settings_row, bg=FIELD, fg=TEXT,
                                       relief="flat", width=8, font=(FONT, 10),
                                       insertbackground=ACCENT)
        self.e_hotkey_tawar.insert(0, self.hotkey_tawar)
        self.e_hotkey_tawar.grid(row=1, column=1, sticky="w", padx=(14, 0),
                                 pady=(3, 0), ipady=3)

        RoundedButton(settings_row, "Simpan", self.save_hotkeys, width=90,
                     height=30, fill=ACCENT, bg=CARD).grid(
            row=1, column=2, sticky="w", padx=(14, 0))

        self._build_volume_panel()
        self._build_auto_terima_panel()
        self._build_auto_tawar_panel()
        self._build_notes_row()

        self.refresh_buttons()
        self.footer.config(text="Menunggu pesan berlabel *MASUK*")
        self.log("Login berhasil. Tekan Start untuk mulai memantau.", "sys")

    # ------------------------------------------------------ volume notifikasi
    def _build_volume_panel(self):
        """Slider volume notif.wav. Efeknya lewat winmm (lihat
        _set_wave_volume) — di Windows modern ini cuma mengatur volume
        sesi aplikasi ini sendiri di Volume Mixer, tidak mengubah volume
        sistem/aplikasi lain."""
        row = tk.Frame(self.body, bg=CARD)
        row.grid(row=5, column=0, sticky="ew", pady=(12, 0))

        tk.Label(row, text="VOLUME NOTIFIKASI", bg=CARD, fg=MUTED,
                 font=(FONT, 7, "bold")).pack(side="left", padx=(0, 10))

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

        tk.Label(toggle_row, text="Delay (ms)", bg=CARD, fg=MUTED,
                 font=(FONT, 8)).pack(side="left", padx=(14, 4))

        self.e_auto_delay_min = tk.Entry(
            toggle_row, bg=FIELD, fg=TEXT, relief="flat", width=5,
            font=(FONT, 10), insertbackground=ACCENT,
        )
        self.e_auto_delay_min.insert(0, str(int(self.auto_delay_min_ms)))
        self.e_auto_delay_min.pack(side="left", ipady=3)

        tk.Label(toggle_row, text="-", bg=CARD, fg=MUTED,
                 font=(FONT, 9)).pack(side="left", padx=3)

        self.e_auto_delay_max = tk.Entry(
            toggle_row, bg=FIELD, fg=TEXT, relief="flat", width=5,
            font=(FONT, 10), insertbackground=ACCENT,
        )
        self.e_auto_delay_max.insert(0, str(int(self.auto_delay_max_ms)))
        self.e_auto_delay_max.pack(side="left", ipady=3)

        RoundedButton(toggle_row, "Simpan", self.save_auto_terima_form,
                     width=90, height=30, fill=ACCENT, bg=CARD).pack(
            side="left", padx=(14, 0))

        # --- baris checkbox pemilihan jenis (multiselect) ---
        jenis_row = tk.Frame(panel, bg=CARD)
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
            ).pack(side="left", padx=(0, 12))

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
            self.auto_jenis_panels[jenis] = sub
            row_i += 1

            tk.Label(sub, text=jenis.upper(), bg=CARD, fg=ACCENT,
                     font=(FONT, 8, "bold")).grid(
                row=0, column=0, columnspan=6, sticky="w", padx=8, pady=(6, 4))

            settings = self.auto_jenis_settings.get(jenis, {})
            self.auto_entries[jenis] = {}

            if jenis in ("Parafrase", "Humanizer"):
                # Target min artinya target HARUS DI ATAS angka ini (bukan
                # minimal termasuk) — isi 15 berarti target >15 yang lolos.
                field_defs = [
                    ("min_target", "Target min (%)", settings.get("min_target")),
                    ("min_client_pct", "Client min (%)", settings.get("min_client_pct")),
                    ("deadline_min_h", "Deadline min (jam)", settings.get("deadline_min_h")),
                    ("deadline_max_h", "Deadline max (jam)", settings.get("deadline_max_h")),
                ]
                if jenis == "Humanizer":
                    field_defs.append(
                        ("max_word_count", "Kata max", settings.get("max_word_count"))
                    )
                for col, (key, label, value) in enumerate(field_defs):
                    wrapper = tk.Frame(sub, bg=CARD)
                    wrapper.grid(row=1, column=col, sticky="w", padx=8)
                    tk.Label(wrapper, text=label.upper(), bg=CARD, fg=MUTED,
                             font=(FONT, 7, "bold"), anchor="w").pack(anchor="w")
                    entry = tk.Entry(wrapper, bg=FIELD, fg=TEXT, relief="flat",
                                     width=8, font=(FONT, 10), insertbackground=ACCENT)
                    entry.insert(0, "" if value is None else str(value))
                    entry.pack(anchor="w", ipady=3, pady=(2, 0))
                    self.auto_entries[jenis][key] = entry

            else:  # Fix File — tidak ada Target Persentase, diganti syarat
                   # perbandingan harga + deadline + pilihan layanan.
                field_defs = [
                    ("min_client_pct", "Client min (%)", settings.get("min_client_pct")),
                    ("deadline_min_h", "Deadline min (jam)", settings.get("deadline_min_h")),
                    ("deadline_max_h", "Deadline max (jam)", settings.get("deadline_max_h")),
                ]
                for col, (key, label, value) in enumerate(field_defs):
                    wrapper = tk.Frame(sub, bg=CARD)
                    wrapper.grid(row=1, column=col, sticky="w", padx=8)
                    tk.Label(wrapper, text=label.upper(), bg=CARD, fg=MUTED,
                             font=(FONT, 7, "bold"), anchor="w").pack(anchor="w")
                    entry = tk.Entry(wrapper, bg=FIELD, fg=TEXT, relief="flat",
                                     width=8, font=(FONT, 10), insertbackground=ACCENT)
                    entry.insert(0, "" if value is None else str(value))
                    entry.pack(anchor="w", ipady=3, pady=(2, 0))
                    self.auto_entries[jenis][key] = entry

                # Grid (bukan pack side=left) supaya dua kotak ini benar2
                # berbagi lebar penuh "sub" secara merata dan tidak
                # mengumpul/mepet di kiri saat panel melebar.
                service_row = tk.Frame(sub, bg=CARD)
                service_row.grid(row=2, column=0, columnspan=6, sticky="ew",
                                 padx=8, pady=(10, 0))
                service_row.grid_columnconfigure(0, weight=1, uniform="svc")
                service_row.grid_columnconfigure(1, weight=1, uniform="svc")

                include_box = tk.Frame(service_row, bg=CARD)
                include_box.grid(row=0, column=0, sticky="new", padx=(0, 20))
                tk.Label(include_box, text="SERVICE YANG DIPILIH", bg=CARD,
                         fg=MUTED, font=(FONT, 7, "bold"), anchor="w").pack(
                    anchor="w", pady=(0, 2))
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
                tk.Label(exclude_box, text="SERVICE YANG DIHINDARI", bg=CARD,
                         fg=MUTED, font=(FONT, 7, "bold"), anchor="w").pack(
                    anchor="w", pady=(0, 2))
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
        """Semua panel syarat tersembunyi kalau Auto Terima dimatikan.
        Kalau aktif, hanya panel jenis yang dicentang yang ditampilkan —
        bisa lebih dari satu sekaligus (multiselect)."""
        if not self.auto_enabled_var.get():
            self.auto_fields_frame.grid_remove()
            return
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
                        "deadline_min_h": read(entries["deadline_min_h"]),
                        "deadline_max_h": read(entries["deadline_max_h"]),
                    }
                    if jenis == "Humanizer":
                        s["max_word_count"] = read(entries["max_word_count"],
                                                   allow_blank=True)
                else:  # Fix File
                    s = {
                        "enabled": enabled,
                        "min_client_pct": read(entries["min_client_pct"]),
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

        tk.Label(toggle_row, text="Delay (ms)", bg=CARD, fg=MUTED,
                 font=(FONT, 8)).pack(side="left", padx=(14, 4))

        self.e_auto_tawar_delay_min = tk.Entry(
            toggle_row, bg=FIELD, fg=TEXT, relief="flat", width=5,
            font=(FONT, 10), insertbackground=ACCENT,
        )
        self.e_auto_tawar_delay_min.insert(0, str(int(self.auto_tawar_delay_min_ms)))
        self.e_auto_tawar_delay_min.pack(side="left", ipady=3)

        tk.Label(toggle_row, text="-", bg=CARD, fg=MUTED,
                 font=(FONT, 9)).pack(side="left", padx=3)

        self.e_auto_tawar_delay_max = tk.Entry(
            toggle_row, bg=FIELD, fg=TEXT, relief="flat", width=5,
            font=(FONT, 10), insertbackground=ACCENT,
        )
        self.e_auto_tawar_delay_max.insert(0, str(int(self.auto_tawar_delay_max_ms)))
        self.e_auto_tawar_delay_max.pack(side="left", ipady=3)

        RoundedButton(toggle_row, "Simpan", self.save_auto_tawar_form,
                     width=90, height=30, fill=ACCENT, bg=CARD).pack(
            side="left", padx=(14, 0))

        jenis_row = tk.Frame(panel, bg=CARD)
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
            ).pack(side="left", padx=(0, 12))

        self.auto_tawar_fields_frame = tk.Frame(panel, bg=CARD)
        self.auto_tawar_fields_frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.auto_tawar_fields_frame.grid_columnconfigure(0, weight=1)

        self.auto_tawar_entries = {}
        self.auto_tawar_jenis_panels = {}
        self.auto_tawar_fixfile_include_vars = {}
        self.auto_tawar_fixfile_exclude_vars = {}

        row_i = 0
        for jenis in self.JENIS_LIST:
            sub = tk.Frame(self.auto_tawar_fields_frame, bg=CARD,
                           highlightbackground=FIELD_EDGE, highlightthickness=1)
            sub.grid(row=row_i, column=0, sticky="ew", pady=(0, 10),
                     ipadx=8, ipady=8)
            self.auto_tawar_jenis_panels[jenis] = sub
            row_i += 1

            tk.Label(sub, text=jenis.upper(), bg=CARD, fg=AMBER,
                     font=(FONT, 8, "bold")).grid(
                row=0, column=0, columnspan=6, sticky="w", padx=8, pady=(6, 4))

            settings = self.auto_tawar_jenis_settings.get(jenis, {})
            self.auto_tawar_entries[jenis] = {}

            if jenis in ("Parafrase", "Humanizer"):
                # Target berbentuk RENTANG (beda dari Auto Terima yang
                # cuma "di atas X"), dan harga client disyaratkan DI
                # BAWAH persentase ini — fallback untuk job yang kurang
                # bagus buat Terima langsung tapi masih worth ditawar.
                field_defs = [
                    ("target_min", "Target min (%)", settings.get("target_min")),
                    ("target_max", "Target max (%)", settings.get("target_max")),
                    ("max_client_pct", "Client max (%)", settings.get("max_client_pct")),
                    ("deadline_min_h", "Deadline min (jam)", settings.get("deadline_min_h")),
                    ("deadline_max_h", "Deadline max (jam)", settings.get("deadline_max_h")),
                ]
                if jenis == "Humanizer":
                    field_defs.append(
                        ("max_word_count", "Kata max", settings.get("max_word_count"))
                    )
                for col, (key, label, value) in enumerate(field_defs):
                    wrapper = tk.Frame(sub, bg=CARD)
                    wrapper.grid(row=1, column=col, sticky="w", padx=8)
                    tk.Label(wrapper, text=label.upper(), bg=CARD, fg=MUTED,
                             font=(FONT, 7, "bold"), anchor="w").pack(anchor="w")
                    entry = tk.Entry(wrapper, bg=FIELD, fg=TEXT, relief="flat",
                                     width=8, font=(FONT, 10), insertbackground=ACCENT)
                    entry.insert(0, "" if value is None else str(value))
                    entry.pack(anchor="w", ipady=3, pady=(2, 0))
                    self.auto_tawar_entries[jenis][key] = entry

            else:  # Fix File
                field_defs = [
                    ("max_client_pct", "Client max (%)", settings.get("max_client_pct")),
                    ("deadline_min_h", "Deadline min (jam)", settings.get("deadline_min_h")),
                    ("deadline_max_h", "Deadline max (jam)", settings.get("deadline_max_h")),
                ]
                for col, (key, label, value) in enumerate(field_defs):
                    wrapper = tk.Frame(sub, bg=CARD)
                    wrapper.grid(row=1, column=col, sticky="w", padx=8)
                    tk.Label(wrapper, text=label.upper(), bg=CARD, fg=MUTED,
                             font=(FONT, 7, "bold"), anchor="w").pack(anchor="w")
                    entry = tk.Entry(wrapper, bg=FIELD, fg=TEXT, relief="flat",
                                     width=8, font=(FONT, 10), insertbackground=ACCENT)
                    entry.insert(0, "" if value is None else str(value))
                    entry.pack(anchor="w", ipady=3, pady=(2, 0))
                    self.auto_tawar_entries[jenis][key] = entry

                service_row = tk.Frame(sub, bg=CARD)
                service_row.grid(row=2, column=0, columnspan=6, sticky="ew",
                                 padx=8, pady=(10, 0))
                service_row.grid_columnconfigure(0, weight=1, uniform="svct")
                service_row.grid_columnconfigure(1, weight=1, uniform="svct")

                include_box = tk.Frame(service_row, bg=CARD)
                include_box.grid(row=0, column=0, sticky="new", padx=(0, 20))
                tk.Label(include_box, text="SERVICE YANG DIPILIH", bg=CARD,
                         fg=MUTED, font=(FONT, 7, "bold"), anchor="w").pack(
                    anchor="w", pady=(0, 2))
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
                tk.Label(exclude_box, text="SERVICE YANG DIHINDARI", bg=CARD,
                         fg=MUTED, font=(FONT, 7, "bold"), anchor="w").pack(
                    anchor="w", pady=(0, 2))
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
        if not self.auto_tawar_enabled_var.get():
            self.auto_tawar_fields_frame.grid_remove()
            return
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

                if jenis in ("Parafrase", "Humanizer"):
                    s = {
                        "enabled": enabled,
                        "target_min": read(entries["target_min"], allow_blank=True),
                        "target_max": read(entries["target_max"], allow_blank=True),
                        "max_client_pct": read(entries["max_client_pct"]),
                        "deadline_min_h": read(entries["deadline_min_h"]),
                        "deadline_max_h": read(entries["deadline_max_h"]),
                    }
                    if jenis == "Humanizer":
                        s["max_word_count"] = read(entries["max_word_count"],
                                                   allow_blank=True)
                else:  # Fix File
                    s = {
                        "enabled": enabled,
                        "max_client_pct": read(entries["max_client_pct"]),
                        "deadline_min_h": read(entries["deadline_min_h"]),
                        "deadline_max_h": read(entries["deadline_max_h"]),
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
                         text=f"{note.get('jenis', '-')} — {note.get('harga_client', '-')}",
                         bg=FIELD, fg=TEXT, font=(FONT, 10, "bold"), anchor="w"
                         ).pack(anchor="w")
                tk.Label(info_col,
                         text=f"{note.get('status', '-')}  ·  "
                              f"{note.get('time', '-')}  ·  {note.get('ref', '-')}",
                         bg=FIELD, fg=MUTED, font=(FONT, 8), anchor="w"
                         ).pack(anchor="w")
                RoundedButton(
                    item, "Hapus", lambda idx=i: self.remove_job_note(idx),
                    width=60, height=26, fill=RED, bg=FIELD,
                ).pack(side="right")

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
        entry = {
            "ref": job.get("ref") or "-",
            "jenis": job.get("jenis") or "-",
            "harga_client": job.get("harga_client") or "-",
            "status": status_baru,
            "time": _dt.now().strftime("%H:%M"),
        }
        self.job_notes_list.append(entry)
        self.save_job_notes()
        self._refresh_notes_badge()
        self.show_notes_popup()
        self.log(f"[CATATAN] {entry['jenis']} ({entry['ref']}) -> {status_baru}",
                 "sys")

    def _watch_job_status(self, job):
        """Pantau status order yang baru diterima sampai berubah dari
        status awalnya (mis. dari "Menunggu Pembayaran" jadi status
        lain) lalu catat sekali dan berhenti memantau job ini. Dibatasi
        3 jam supaya thread tidak hidup selamanya kalau client tidak
        kunjung bayar/status tidak pernah berubah."""
        job_id, collection, url = job.get("id"), job.get("collection"), job.get("url")
        if not (job_id and collection):
            return

        status_awal = job.get("status")
        if not status_awal:
            info0, _, hard_error0 = self.browser.fetch_order_detail(
                job_id, collection, url)
            if hard_error0:
                return
            status_awal = info0.get("status")

        deadline = time.time() + 3 * 3600
        while time.time() < deadline:
            time.sleep(45)
            info, _, hard_error = self.browser.fetch_order_detail(
                job_id, collection, url)
            if hard_error:
                continue
            status_baru = info.get("status")
            if status_baru and status_baru != "-" and status_baru != status_awal:
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
            "deadline_min_h": data.get("auto_deadline_min_h", 2),
            "deadline_max_h": data.get("auto_deadline_max_h", 48),
        }
        humanizer_defaults = {
            "enabled": legacy_enabled and legacy_jenis == "Humanizer",
            "min_target": data.get("auto_min_target", 20),
            "min_client_pct": data.get("auto_min_client_pct", 50),
            "deadline_min_h": data.get("auto_deadline_min_h", 2),
            "deadline_max_h": data.get("auto_deadline_max_h", 48),
            "max_word_count": data.get("auto_max_word_count", 3000),
        }
        fixfile_defaults = {
            "enabled": False,
            "min_client_pct": 50,
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
                        "auto_max_word_count"):
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
        parafrase_defaults = {
            "enabled": False, "target_min": 0, "target_max": 20,
            "max_client_pct": 70, "deadline_min_h": 2, "deadline_max_h": 48,
        }
        humanizer_defaults = {
            "enabled": False, "target_min": 0, "target_max": 20,
            "max_client_pct": 70, "deadline_min_h": 2, "deadline_max_h": 48,
            "max_word_count": 3000,
        }
        fixfile_defaults = {
            "enabled": False, "max_client_pct": 70,
            "deadline_min_h": 2, "deadline_max_h": 48,
            "service_include": [], "service_exclude": [],
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
                notes = data.get("notes") or ""
                msg = (f"[UPDATE] Versi baru tersedia: {remote_version} "
                       f"(sekarang: {APP_VERSION}).")
                if notes:
                    msg += f" Catatan: {notes}"
                self.ui(self.log, msg, "sys")
                if url:
                    self.ui(self._show_update_notice, remote_version, url)
        except Exception as exc:                  # noqa: BLE001
            # Gagal cek update tidak boleh mengganggu jalannya bot —
            # cukup diam, bukan dianggap error penting.
            self.ui(self.log, f"[UPDATE] Gagal cek versi terbaru: {exc}", "skip")

    def _show_update_notice(self, version, url):
        if messagebox.askyesno(
            "Update tersedia",
            f"Versi {version} sudah tersedia (sekarang {APP_VERSION}).\n\n"
            "Buka halaman unduhannya sekarang?",
        ):
            try:
                webbrowser.open(url)
            except Exception as exc:              # noqa: BLE001
                self.log(f"[UPDATE] Gagal membuka link: {exc}", "err")

    def _warm_browser(self):
        """Resolve controller browser default lebih awal, supaya saat link
        pertama masuk nanti Python tidak perlu mencari ulang browser mana
        yang harus dipakai (pencarian ini kecil, tapi tetap memakan waktu)."""
        try:
            webbrowser.get()
        except Exception:                          # noqa: BLE001
            pass

    # ------------------------------------------------------ shortcut Tawar/Terima
    def register_hotkeys(self):
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
        threading.Thread(
            target=self._run_and_alert,
            args=(self.browser.do_tawar, (job.get("url"), job.get("harga_estimasi_raw"))),
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
        new_terima = self.e_hotkey_terima.get().strip().lower() or "f2"
        new_tawar = self.e_hotkey_tawar.get().strip().lower() or "f3"
        self.hotkey_terima = new_terima
        self.hotkey_tawar = new_tawar
        self.save_config(self.api_id, self.api_hash, new_terima, new_tawar)
        self.register_hotkeys()

    # ------------------------------------------------------ handler pesan
    def register_handler(self):
        client = self.worker.client

        @client.on(events.NewMessage(chats=TARGET_CHAT))
        async def handler(event):                  # noqa: ANN001
            if self.is_paused:
                return

            text = event.raw_text
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

        # Syarat 1 (semua jenis): perbandingan harga client vs estimasi.
        min_client_pct = settings.get("min_client_pct")
        if min_client_pct not in (None, "") and (client / estimasi) * 100 <= float(min_client_pct):
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
            if min_target not in (None, "") and target <= float(min_target):
                return False

            if jenis == "Humanizer":
                max_words = settings.get("max_word_count")
                if max_words not in (None, ""):
                    try:
                        words = float(info.get("word_count_raw"))
                    except (TypeError, ValueError):
                        return False
                    if words >= float(max_words):
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
                max_words = settings.get("max_word_count")
                if max_words not in (None, ""):
                    try:
                        words = float(info.get("word_count_raw"))
                    except (TypeError, ValueError):
                        return False
                    if words >= float(max_words):
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
        """Auto Terima dicek DULUAN; Auto Tawar jadi fallback — hanya
        dicek kalau job ini tidak lolos syarat Auto Terima. Satu token
        dedupe dipakai bersama supaya satu job cuma pernah memicu SALAH
        SATU aksi otomatis, tidak dua-duanya."""
        if self.auto_action_fired_token == token:
            return
        if self._check_auto_terima(info):
            self.auto_action_fired_token = token
            threading.Thread(
                target=self._auto_terima_delayed, args=(token,), daemon=True
            ).start()
            return
        if self._check_auto_tawar(info):
            self.auto_action_fired_token = token
            threading.Thread(
                target=self._auto_tawar_delayed, args=(token,), daemon=True
            ).start()

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
