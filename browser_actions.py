"""
browser_actions.py
Otomasi Chrome yang sudah login (dibuka dengan remote debugging) untuk
kawalu.id. Ada DUA jalur Selenium yang berdiri sendiri:

  api  -> popup ringkasan + Terima lewat request API (fetch di dalam
          browser). Tab-nya diam di satu tempat dan tidak pernah dipakai
          untuk hal lain, jadi nyaris tanpa navigasi/loading.
  view -> tab visual yang menampilkan halaman detail job, dan tempat klik
          DOM (Tawar). Boleh lambat karena tidak menghalangi jalur api.

Masing-masing punya session chromedriver, tab, dan lock sendiri, jadi
keduanya bisa jalan bersamaan tanpa rebutan "current window".

Selektor DOM (bi-check-lg, bi-tag, teks tombol, class overlay) disusun
dari screenshot DOM yang dikirim. Kalau situs berubah struktur HTML-nya,
klik bisa gagal menemukan elemen — kirim pesan error dari log.
"""

import re
import subprocess
import threading
import time
import urllib.request
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)

DEBUG_ADDRESS = "127.0.0.1:9222"
WAIT_TIMEOUT = 6
ADMIN_HOME = "https://admin.kawalu.id/admin/dashboard"

# Sesuaikan dua nilai ini kalau lokasi Chrome / folder profil kamu berbeda
# dari yang dipakai saat membuat shortcut "Chrome Debug".
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
USER_DATA_DIR = r"C:\ChromeAutomasi"
DEBUG_PORT = 9222


def _format_rupiah(n):
    try:
        return "Rp " + format(int(n), ",").replace(",", ".")
    except (TypeError, ValueError):
        return "-"


def _parse_deadline(iso):
    """Server kirim UTC ('...Z'); return datetime timezone-aware di jam
    lokal komputer, dipakai baik untuk ditampilkan maupun dihitung
    selisih jamnya (syarat rentang deadline auto terima)."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return dt.astimezone()
    except Exception:                              # noqa: BLE001
        return None


def _format_deadline(iso):
    dt = _parse_deadline(iso)
    return dt.strftime("%d %b %Y, %H.%M") if dt else (str(iso) if iso else "-")


def _debug_port_alive():
    """Cek langsung ke port debug — kalau ini merespons, Chrome debug
    pasti masih hidup, apa pun error lain yang terjadi di Selenium."""
    try:
        urllib.request.urlopen(
            f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=1.5
        )
        return True
    except Exception:                              # noqa: BLE001
        return False


def compute_tawar_price(estimasi):
    """Hitung harga tawar dari Harga Estimasi, dipotong sesuai bracket,
    lalu 3 digit terakhir dibulatkan ke bawah (dibuang) supaya angkanya
    tidak terlalu presisi/kelihatan bot. Contoh: estimasi 98654 (<100rb)
    -> dipotong 15rb -> 83654 -> dibulatkan -> 83000."""
    estimasi = int(estimasi)
    if estimasi < 100_000:
        potongan = 15_000
    elif estimasi < 150_000:
        potongan = 20_000
    elif estimasi < 200_000:
        potongan = 25_000
    elif estimasi < 270_000:
        potongan = 30_000
    elif estimasi < 400_000:
        potongan = 40_000
    else:
        potongan = 50_000
    mentah = max(estimasi - potongan, 0)
    return (mentah // 1000) * 1000


# Struktur JSON order-detail beda tiap koleksi. Path pakai titik untuk
# field bersarang (mis. "price.by_client"). Beberapa alternatif dicoba
# berurutan, dipakai yang pertama ada isinya.
#
# CATATAN ASUMSI: endpoint confirm Fix File ("orderfixfile") BELUM
# terkonfirmasi lewat Network tab — itu API tulis (POST) yang beda dari
# order-detail (GET) yang sudah dikonfirmasi. Kalau Terima Fix File
# gagal, ini yang perlu dicek duluan (klik Terima manual di web, lihat
# Network tab request POST-nya).
COLLECTION_FIELDS = {
    "parafrases": {
        "jenis": "Parafrase",
        "endpoint": "parafrase",
        "client": ("amount",),
        "estimasi": ("system_price",),
        "target": ("target",),
        "deadline": ("deadline",),
        "service": None,
        "word_count": None,
    },
    "humanizers": {
        "jenis": "Humanizer",
        "endpoint": "humanizer",   # ASUMSI (belum terkonfirmasi Network tab)
        "client": ("price.by_client", "amount"),
        "estimasi": ("price.by_system", "system_price"),
        # Tidak ada field "target" tersendiri untuk Humanizer — yang
        # ditampilkan situs sebagai "Target Persentase" ternyata
        # similarity.to (target skor AI-detection yang mau dicapai).
        "target": ("similarity.to",),
        "deadline": ("deadline_date", "deadline"),
        "service": None,
        # Jumlah kata ada di jobFile.words (dikonfirmasi lewat Network tab).
        "word_count": ("jobFile.words", "word_count", "words"),
    },
    "order-fix-files": {            # dikonfirmasi lewat Network tab
        "jenis": "Fix File",
        "endpoint": "orderfixfile",  # ASUMSI
        "client": ("amount",),       # dikonfirmasi lewat Network tab
        "estimasi": ("system_price",),
        "target": (),               # Fix File tidak punya Target Persentase
        "deadline": ("deadline",),
        "service": ("service",),    # dikonfirmasi; "A | B | C" -> poin-poin
        "word_count": None,
    },
}


class _Channel:
    """Satu jalur Selenium: session (driver) sendiri, tab sendiri, lock
    sendiri."""

    def __init__(self, name):
        self.name = name
        self.driver = None
        self.handle = None
        self.lock = threading.RLock()


class BrowserActions:
    def __init__(self, log=None):
        self.log = log or (lambda *a, **k: None)
        self.api = _Channel("api")
        self.view = _Channel("view")
        self._launch_lock = threading.Lock()

    # ------------------------------------------------------------ browser
    def _connect(self):
        options = webdriver.ChromeOptions()
        options.debugger_address = DEBUG_ADDRESS
        options.page_load_strategy = "eager"
        return webdriver.Chrome(options=options)

    def _launch_debug_chrome(self):
        """Jalankan ulang Chrome mode debug (bukan Chrome biasa) kalau
        prosesnya sudah mati total."""
        try:
            subprocess.Popen([
                CHROME_PATH,
                f"--remote-debugging-port={DEBUG_PORT}",
                f"--user-data-dir={USER_DATA_DIR}",
            ])
            self.log("[BROWSER] Chrome debug tertutup — membuka ulang...", "sys")
            time.sleep(2.5)  # beri waktu Chrome siap sebelum ditempeli
            return True
        except Exception as exc:                  # noqa: BLE001
            self.log(f"[BROWSER] Gagal membuka ulang Chrome debug: {exc}", "err")
            return False

    def _ensure_browser_up(self):
        if _debug_port_alive():
            return
        # Dikunci supaya dua jalur tidak menyalakan Chrome dua kali.
        with self._launch_lock:
            if _debug_port_alive():
                return
            if not self._launch_debug_chrome():
                raise RuntimeError("Chrome debug tidak bisa dinyalakan otomatis.")

    def _ensure_driver(self, ch):
        if ch.driver is not None:
            try:
                _ = ch.driver.window_handles  # cek session masih hidup
                return ch.driver
            except WebDriverException:
                ch.driver = None

        self._ensure_browser_up()
        try:
            ch.driver = self._connect()
        except WebDriverException as exc:
            # Port hidup tapi gagal ditempeli -> bukan "Chrome tertutup",
            # jangan buka Chrome baru. Coba sekali lagi, catat error aslinya.
            self.log(f"[BROWSER] Chrome debug hidup tapi gagal ditempeli, "
                     f"mencoba lagi... ({exc})", "err")
            time.sleep(1)
            ch.driver = self._connect()
        return ch.driver

    def _bind_tab(self, ch, drv):
        """Pastikan session ini menempel di tab MILIKNYA sendiri. Tidak
        melakukan switch kalau sudah di tab yang benar (supaya jalur api
        tidak mencuri tampilan dari tab job). Kalau tab ternyata sudah
        tertutup (mis. dibuang Memory Saver Chrome), buat tab baru saat itu
        juga — tidak pernah macet permanen."""
        handles = drv.window_handles
        if ch.handle and ch.handle in handles:
            try:
                try:
                    current = drv.current_window_handle
                except WebDriverException:
                    current = None
                if current != ch.handle:
                    drv.switch_to.window(ch.handle)
                return
            except WebDriverException:
                pass  # handle basi, lanjut buat tab baru

        if handles:
            # Session tanpa "current window" yang valid tidak bisa membuat
            # tab baru, jadi tempel dulu ke tab mana pun yang masih ada.
            try:
                drv.switch_to.window(handles[-1])
            except WebDriverException:
                pass
        drv.switch_to.new_window("tab")
        ch.handle = drv.current_window_handle

    def _reset_channel(self, ch):
        """Buang session+tab jalur ini — dipakai sebelum retry kalau error-
        nya kelihatan seperti tab/sesi bermasalah (tertutup, invalid, dsb).
        Panggilan berikutnya ke _ensure_driver/_bind_tab otomatis membuat
        session & tab baru lagi."""
        ch.driver = None
        ch.handle = None

    def _looks_recoverable(self, exc):
        """True kalau error ini kemungkinan karena tab/sesi Chrome
        bermasalah (tertutup, invalid session, dsb) — layak dicoba lagi
        setelah tab dibuka ulang. False untuk error lain (mis. elemen
        memang tidak ada karena situsnya berubah) supaya tidak retry
        percuma."""
        if isinstance(exc, WebDriverException):
            return True
        text = str(exc).lower()
        return any(s in text for s in (
            "invalid session", "no such window", "target window already closed",
            "chrome not reachable", "disconnected", "tab was closed",
        ))

    def _on_admin_origin(self, drv):
        try:
            return "admin.kawalu.id" in drv.current_url
        except WebDriverException:
            return False

    def warm_up(self):
        """Siapkan kedua jalur (session + tab) sebelum job pertama masuk,
        supaya popup pertama pun tidak menanggung biaya membuat session
        dan tab baru."""
        try:
            with self.api.lock:
                drv = self._ensure_driver(self.api)
                self._bind_tab(self.api, drv)
                if not self._on_admin_origin(drv):
                    drv.get(ADMIN_HOME)
            with self.view.lock:
                drv = self._ensure_driver(self.view)
                self._bind_tab(self.view, drv)
            self.log("[BROWSER] Chrome debug siap (jalur API & tab job).", "sys")
        except Exception as exc:                  # noqa: BLE001
            self.log(f"[BROWSER] Pemanasan gagal: {exc}", "err")

    # ------------------------------------------------------------ tab visual
    def open_job(self, url):
        """Tampilkan halaman detail job di tab visual (dipakai ulang) dan
        bawa ke depan. Berjalan di jalur 'view' sehingga tidak
        menghalangi popup/Terima yang ada di jalur 'api'."""
        ch = self.view
        with ch.lock:
            try:
                drv = self._ensure_driver(ch)
                self._bind_tab(ch, drv)

                try:
                    same = drv.current_url.split("#")[0] == url.split("#")[0]
                except WebDriverException:
                    same = False
                if not same:
                    drv.get(url)

                try:
                    drv.execute_cdp_cmd("Page.bringToFront", {})
                except Exception:                  # noqa: BLE001
                    pass

                self.log(f"[BROWSER] Tab dibuka: {url}", "sys")
                return True
            except Exception as exc:              # noqa: BLE001
                self.log(f"[BROWSER] Gagal membuka tab di Chrome debug: {exc}",
                         "err")
                return False

    def _focus_last_job(self):
        ch = self.view
        drv = self._ensure_driver(ch)
        if not ch.handle or ch.handle not in drv.window_handles:
            raise RuntimeError("Tidak ada tab job aktif yang bisa dikontrol.")
        self._bind_tab(ch, drv)
        return drv

    def _overlay(self, driver):
        return WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div.fixed.inset-0.z-50")
            )
        )

    # ------------------------------------------------------------ Terima (DOM)
    def do_terima(self, url=None, _retry=True):
        """Fallback klik DOM (dipakai hanya kalau ref/collection tidak
        terbaca dari pesan Telegram, sehingga jalur API tidak bisa
        dipakai). _retry=True -> kalau gagal karena tab/sesi bermasalah,
        tab dibuka ulang dan dicoba sekali lagi otomatis sebelum nyerah."""
        with self.view.lock:
            try:
                if url:
                    self.open_job(url)
                driver = self._focus_last_job()

                btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//button[.//i[contains(@class,'bi-check-lg')]]")
                    )
                )
                btn.click()

                overlay = self._overlay(driver)
                confirm = WebDriverWait(driver, WAIT_TIMEOUT).until(
                    lambda d: overlay.find_element(
                        By.XPATH, ".//button[normalize-space()='Terima']"
                    )
                )
                confirm.click()

                msg = "Terima dikirim."
                self.log(f"[BROWSER] {msg}", "hit")
                return True, msg
            except TimeoutException as exc:
                if _retry:
                    self.log("[BROWSER] Tombol Terima tidak ditemukan — "
                             "membuka ulang tab & mencoba sekali lagi...", "err")
                    self._reset_channel(self.view)
                    return self.do_terima(url, _retry=False)
                msg = ("Tombol Terima tidak ditemukan — pastikan tab job "
                       "masih terbuka di jendela Chrome debug.")
                self.log(f"[BROWSER] {msg}", "err")
                return False, msg
            except Exception as exc:              # noqa: BLE001
                if _retry and self._looks_recoverable(exc):
                    self.log(f"[BROWSER] Tab/sesi bermasalah ({exc}) — membuka "
                             "ulang tab & mencoba sekali lagi...", "err")
                    self._reset_channel(self.view)
                    return self.do_terima(url, _retry=False)
                self.log(f"[BROWSER] Gagal Terima: {exc}", "err")
                return False, str(exc)

    # ------------------------------------------------------------ Tawar (DOM)
    def do_tawar(self, url=None, estimasi=None, _retry=True):
        """Belum ada endpoint API untuk Tawar yang terkonfirmasi, jadi
        tetap lewat klik DOM di tab visual. Kalau `estimasi` (angka Harga
        Estimasi) sudah diketahui dari popup, dipakai langsung tanpa
        scrape ulang; kalau tidak, di-scrape dari halaman seperti
        sebelumnya. Harga yang diisi ke kolom "Tawar jadi" dihitung
        lewat compute_tawar_price(), bukan Harga Estimasi mentah.
        _retry=True -> kalau gagal karena tab/sesi bermasalah, tab dibuka
        ulang dan dicoba sekali lagi otomatis sebelum nyerah."""
        with self.view.lock:
            try:
                if url:
                    self.open_job(url)  # tidak reload kalau sudah di URL ini
                driver = self._focus_last_job()

                if estimasi is None:
                    # Ambil Harga Estimasi dari halaman SEBELUM modal
                    # dibuka, dipakai sebagai dasar hitungan tawaran.
                    try:
                        span = driver.find_element(
                            By.XPATH,
                            "//span[normalize-space()='Harga Estimasi']"
                            "/following-sibling::span[1]",
                        )
                        digits = re.sub(r"[^\d]", "", span.text)
                        estimasi = int(digits) if digits else None
                    except NoSuchElementException:
                        pass

                btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//button[.//i[contains(@class,'bi-tag')]]")
                    )
                )
                btn.click()

                overlay = self._overlay(driver)

                if not estimasi:
                    msg = "Harga Estimasi tidak terbaca — isi manual di jendela Chrome."
                    self.log(f"[BROWSER] {msg}", "err")
                    return False, msg

                harga_tawar = compute_tawar_price(estimasi)

                field = WebDriverWait(driver, WAIT_TIMEOUT).until(
                    lambda d: overlay.find_element(By.XPATH, ".//input")
                )
                field.clear()
                field.send_keys(str(harga_tawar))

                # Catatan sengaja tidak diisi (opsional)
                submit = overlay.find_element(
                    By.XPATH, ".//button[normalize-space()='Tawar']"
                )
                submit.click()

                msg = f"Tawar dikirim: Rp{harga_tawar} (estimasi Rp{estimasi})"
                self.log(f"[BROWSER] {msg}", "hit")
                return True, msg
            except TimeoutException:
                if _retry:
                    self.log("[BROWSER] Tombol Tawar tidak ditemukan — "
                             "membuka ulang tab & mencoba sekali lagi...", "err")
                    self._reset_channel(self.view)
                    return self.do_tawar(url, estimasi, _retry=False)
                msg = ("Tombol Tawar tidak ditemukan — pastikan tab job "
                       "masih terbuka di jendela Chrome debug.")
                self.log(f"[BROWSER] {msg}", "err")
                return False, msg
            except Exception as exc:              # noqa: BLE001
                if _retry and self._looks_recoverable(exc):
                    self.log(f"[BROWSER] Tab/sesi bermasalah ({exc}) — membuka "
                             "ulang tab & mencoba sekali lagi...", "err")
                    self._reset_channel(self.view)
                    return self.do_tawar(url, estimasi, _retry=False)
                self.log(f"[BROWSER] Gagal Tawar: {exc}", "err")
                return False, str(exc)

    # ------------------------------------------------------------ jalur API
    # Ref job diambil dari teks pesan Telegram ("Ref: ..."). Token dibaca
    # live dari localStorage tab yang sedang login setiap kali dipanggil,
    # jadi otomatis ikut siapa pun yang login di jendela Chrome debug.

    FETCH_DETAIL_JS = """
    var cb = arguments[arguments.length - 1];
    var id = arguments[0];
    var collection = arguments[1];
    var token = localStorage.getItem('adminToken');
    if (!token) { cb({error: 'no-token'}); return; }
    fetch('https://admin.kawalu.id/api/admin/order-detail?id=' + id +
          '&collection=' + collection, {
        headers: { 'Authorization': 'Bearer ' + token }
    }).then(function(r){
        return r.json().then(function(data){ return {status: r.status, data: data}; });
    }).then(function(result){ cb(result); })
      .catch(function(err){ cb({error: String(err)}); });
    """

    FETCH_CONFIRM_JS = """
    var cb = arguments[arguments.length - 1];
    var ref = arguments[0];
    var endpoint = arguments[1];
    var token = localStorage.getItem('adminToken');
    if (!token) { cb({error: 'no-token'}); return; }
    fetch('https://admin.kawalu.id/api/' + endpoint + '/confirm', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + token
        },
        body: JSON.stringify({ref: ref, confirm: 'accepted', reason: ''})
    }).then(function(r){
        return r.json().then(function(data){ return {status: r.status, data: data}; });
    }).then(function(result){ cb(result); })
      .catch(function(err){ cb({error: String(err)}); });
    """

    def _run_authed_fetch(self, script, url, *args):
        ch = self.api
        with ch.lock:
            drv = self._ensure_driver(ch)
            self._bind_tab(ch, drv)
            target = url or ADMIN_HOME
            if not self._on_admin_origin(drv):
                drv.get(target)

            drv.set_script_timeout(WAIT_TIMEOUT)
            result = drv.execute_async_script(script, *args)

            if isinstance(result, dict) and result.get("status") == 401:
                # Token di localStorage bisa basi walau sesi login masih
                # valid (situs biasanya memperbarui token sendiri saat
                # halamannya dimuat). Muat ulang sekali lalu coba lagi.
                # Kalau tetap 401, memang perlu login ulang.
                self.log("[API] Token ditolak — menyegarkan sesi lalu "
                         "mencoba lagi...", "sys")
                drv.get(target)
                time.sleep(1.5)
                result = drv.execute_async_script(script, *args)
            return result

    def _check_common_errors(self, result, action):
        """Return pesan error (string) kalau ada masalah umum, atau None
        kalau tidak ada — dipakai baik untuk log maupun untuk alert ke
        pengguna."""
        if result.get("error") == "no-token":
            msg = "Token tidak ditemukan — login ulang di jendela Chrome Debug."
            self.log(f"[API] {msg} ({action})", "err")
            return msg
        if result.get("error"):
            msg = f"Gagal {action}: {result['error']}"
            self.log(f"[API] {msg}", "err")
            return msg
        if result.get("status") == 401:
            msg = "Sesi login kedaluwarsa — login ulang di jendela Chrome Debug."
            self.log(f"[API] {msg} ({action})", "err")
            return msg
        return None

    def fetch_order_detail(self, id_, collection, url=None):
        """Satu kali ambil ringkasan job lewat API. Return (info, complete,
        hard_error). complete=True kalau semua field wajib koleksi ini
        sudah terisi. hard_error=True kalau gagal karena token/koneksi
        (retry percuma, perlu login ulang) — beda dari field yang cuma
        belum siap di server (retry ada gunanya)."""
        info = {
            "harga_client": None, "harga_estimasi": None,
            "target": None, "deadline": None, "jenis": None, "ref": None,
            "status": None, "service_list": None, "word_count": None,
            # nilai mentah (angka/datetime), dipakai hitungan Tawar &
            # syarat Auto Terima, bukan untuk ditampilkan langsung
            "harga_client_raw": None, "harga_estimasi_raw": None,
            "target_raw": None, "word_count_raw": None, "deadline_dt": None,
        }
        try:
            result = self._run_authed_fetch(
                self.FETCH_DETAIL_JS, url, id_, collection
            )
        except Exception as exc:                  # noqa: BLE001
            self.log(f"[API] Gagal ambil detail: {exc}", "err")
            return info, False, True

        if self._check_common_errors(result, "ambil detail"):
            return info, False, True

        data = result.get("data", {}) or {}
        order = data.get("order", data)

        def dig(path):
            cur = order
            for part in path.split("."):
                if not isinstance(cur, dict) or part not in cur:
                    return None
                cur = cur[part]
            return cur if cur not in (None, "") else None

        def pick(*paths):
            for p in paths:
                v = dig(p)
                if v is not None:
                    return v
            return None

        spec = COLLECTION_FIELDS.get(collection, COLLECTION_FIELDS["parafrases"])

        harga_client_raw = pick(*spec["client"])
        harga_estimasi_raw = pick(*spec["estimasi"])
        target_raw = pick(*spec["target"]) if spec["target"] else None
        deadline_raw = pick(*spec["deadline"])
        service_raw = pick(*spec["service"]) if spec.get("service") else None
        word_count_raw = pick(*spec["word_count"]) if spec.get("word_count") else None

        info["harga_client"] = (
            _format_rupiah(harga_client_raw) if harga_client_raw is not None else "-"
        )
        info["harga_estimasi"] = (
            _format_rupiah(harga_estimasi_raw)
            if harga_estimasi_raw is not None else "-"
        )
        info["target"] = str(target_raw) if target_raw is not None else "-"
        info["deadline"] = _format_deadline(deadline_raw) if deadline_raw else "-"
        info["ref"] = order.get("ref")
        info["status"] = str(pick("status") or "-")
        info["jenis"] = spec["jenis"]
        info["harga_client_raw"] = harga_client_raw
        info["harga_estimasi_raw"] = harga_estimasi_raw
        info["target_raw"] = target_raw
        info["word_count_raw"] = word_count_raw
        info["deadline_dt"] = _parse_deadline(deadline_raw)
        if service_raw:
            info["service_list"] = [s.strip() for s in str(service_raw).split("|")
                                    if s.strip()]
        if spec.get("word_count"):
            info["word_count"] = str(word_count_raw) if word_count_raw is not None else "-"

        required = [harga_client_raw, harga_estimasi_raw]
        if spec.get("service"):
            required.append(service_raw)
        complete = all(v is not None for v in required)

        if harga_client_raw is None and harga_estimasi_raw is None:
            keys = list(order.keys()) if isinstance(order, dict) else order
            self.log(f"[API] Field harga tidak dikenali untuk '{collection}'. "
                     f"Key yang tersedia: {keys}", "err")

        return info, complete, False

    def confirm_via_api(self, ref, collection, url=None, _retry=True):
        """Terima job langsung lewat API — satu request POST, tanpa klik
        dan tanpa menunggu render. Return (berhasil, pesan). _retry=True
        -> kalau gagal karena tab/sesi jalur api bermasalah, tab dibuka
        ulang dan dicoba sekali lagi otomatis sebelum nyerah."""
        spec = COLLECTION_FIELDS.get(collection, COLLECTION_FIELDS["parafrases"])
        endpoint = spec["endpoint"]
        try:
            result = self._run_authed_fetch(
                self.FETCH_CONFIRM_JS, url, ref, endpoint
            )
        except Exception as exc:                  # noqa: BLE001
            if _retry and self._looks_recoverable(exc):
                self.log(f"[API] Tab/sesi bermasalah ({exc}) — membuka ulang "
                         "tab & mencoba sekali lagi...", "err")
                self._reset_channel(self.api)
                return self.confirm_via_api(ref, collection, url, _retry=False)
            msg = f"Gagal Terima: {exc}"
            self.log(f"[API] {msg}", "err")
            return False, str(exc)

        err = self._check_common_errors(result, "Terima")
        if err:
            return False, err

        data = result.get("data", {}) or {}
        if data.get("success"):
            msg = f"Terima berhasil: {ref}"
            self.log(f"[API] {msg}", "hit")
            return True, msg
        else:
            msg = str(data.get("message", data))
            self.log(f"[API] Ditolak server: {msg}", "err")
            return False, msg
