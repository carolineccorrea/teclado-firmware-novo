import os
import sys
import subprocess
import threading
import shutil
import json
import string
import time
import ctypes
import datetime

# Auto-instala customtkinter se necessário
try:
    import customtkinter as ctk
except ImportError:
    print("Instalando customtkinter...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "customtkinter"])
    import customtkinter as ctk

from tkinter import messagebox

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

REPO = "carolineccorrea/teclado-firmware-novo"
BOOTLOADER_NAMES = {"NRF52BOOT", "NICENANO", "NICE!NANO", "NICENANOV2"}
FIRMWARE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "firmwares")

# ── Cores ──
C_BG = "#0f0f14"
C_CARD = "#1a1a24"
C_CARD_BORDER = "#2a2a3a"
C_ACCENT = "#6c5ce7"
C_ACCENT_HOVER = "#5a4bd6"
C_SUCCESS = "#00e676"
C_WARNING = "#ffc107"
C_ERROR = "#ff5252"
C_TEXT = "#e0e0e0"
C_TEXT_DIM = "#707090"
C_GREEN_BTN = "#2e7d49"
C_GREEN_BTN_HOVER = "#236038"
C_RED_BTN = "#c62828"
C_RED_BTN_HOVER = "#961f1f"


def _ensure_gh_in_path():
    import winreg
    try:
        machine_path = winreg.QueryValueEx(
            winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            "Path",
        )[0]
    except Exception:
        machine_path = ""
    try:
        user_path = winreg.QueryValueEx(
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment"), "Path"
        )[0]
    except Exception:
        user_path = ""
    os.environ["PATH"] = machine_path + ";" + user_path + ";" + os.environ.get("PATH", "")


_ensure_gh_in_path()


def get_volume_label(drive_letter):
    buf = ctypes.create_unicode_buffer(256)
    try:
        ctypes.windll.kernel32.GetVolumeInformationW(
            f"{drive_letter}:\\", buf, 256, None, None, None, None, 0
        )
        return buf.value.strip()
    except Exception:
        return ""


def get_removable_drives():
    drives = {}
    for letter in string.ascii_uppercase:
        if letter == "C":
            continue
        try:
            dtype = ctypes.windll.kernel32.GetDriveTypeW(f"{letter}:\\")
            if dtype == 2:
                drives[letter] = get_volume_label(letter)
        except Exception:
            pass
    return drives


# ═══════════════════════════════════════════════════════════════════
#  Componentes customizados
# ═══════════════════════════════════════════════════════════════════

class StepIndicator(ctk.CTkFrame):
    """Indicador visual de uma etapa do pipeline."""

    STATES = {
        "idle":     {"dot": C_TEXT_DIM, "text": C_TEXT_DIM, "label_prefix": ""},
        "active":   {"dot": C_WARNING,  "text": C_TEXT,     "label_prefix": ""},
        "success":  {"dot": C_SUCCESS,  "text": C_SUCCESS,  "label_prefix": "✓ "},
        "error":    {"dot": C_ERROR,    "text": C_ERROR,    "label_prefix": "✗ "},
    }

    def __init__(self, master, number, label, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self._number = number
        self._label_text = label
        self._state = "idle"
        self._pulse_on = False

        self.dot = ctk.CTkLabel(
            self, text=f" {number} ", width=28, height=28,
            corner_radius=14, font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=C_TEXT_DIM, text_color=C_BG,
        )
        self.dot.pack(side="top")

        self.label = ctk.CTkLabel(
            self, text=label, font=ctk.CTkFont(size=11),
            text_color=C_TEXT_DIM,
        )
        self.label.pack(side="top", pady=(4, 0))

        self._sub_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=9),
            text_color=C_TEXT_DIM,
        )
        self._sub_label.pack(side="top")

    def set_state(self, state, sub_text=""):
        self._state = state
        s = self.STATES.get(state, self.STATES["idle"])
        self.dot.configure(fg_color=s["dot"], text_color=C_BG if state != "idle" else C_TEXT)
        prefix = s["label_prefix"]
        self.label.configure(text=f"{prefix}{self._label_text}", text_color=s["text"])
        self._sub_label.configure(text=sub_text, text_color=s["text"])

        if state == "active":
            self._start_pulse()
        else:
            self._pulse_on = False

    def _start_pulse(self):
        self._pulse_on = True
        self._pulse_tick()

    def _pulse_tick(self):
        if not self._pulse_on:
            return
        cur = self.dot.cget("fg_color")
        nxt = C_CARD if cur == C_WARNING else C_WARNING
        self.dot.configure(fg_color=nxt)
        self.after(600, self._pulse_tick)


class LogConsole(ctk.CTkFrame):
    """Console de log com auto-scroll."""

    def __init__(self, master, **kw):
        super().__init__(master, fg_color=C_BG, corner_radius=8, **kw)
        self.textbox = ctk.CTkTextbox(
            self, font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=C_BG, text_color=C_TEXT_DIM,
            border_width=0, corner_radius=0,
            activate_scrollbars=True, wrap="word",
            height=140,
        )
        self.textbox.pack(fill="both", expand=True, padx=4, pady=4)
        self.textbox.configure(state="disabled")

    def log(self, msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.textbox.configure(state="normal")
        self.textbox.insert("end", f"[{ts}]  {msg}\n")
        self.textbox.configure(state="disabled")
        self.textbox.see("end")


class FirmwareCard(ctk.CTkFrame):
    """Card para exibir um firmware detectado."""

    def __init__(self, master, icon, name, side, flash_cb, **kw):
        super().__init__(master, fg_color=C_CARD, corner_radius=10, border_width=1,
                         border_color=C_CARD_BORDER, **kw)
        self.side = side

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(row, text=icon, font=ctk.CTkFont(size=22)).pack(side="left")
        ctk.CTkLabel(
            row, text=name, font=ctk.CTkFont(size=12),
            text_color=C_TEXT, anchor="w",
        ).pack(side="left", padx=(8, 0), fill="x", expand=True)

        is_reset = side == "reset"
        self.btn = ctk.CTkButton(
            row, text="Flash" if not is_reset else "Reset",
            width=70, height=28, corner_radius=6,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=C_RED_BTN if is_reset else C_GREEN_BTN,
            hover_color=C_RED_BTN_HOVER if is_reset else C_GREEN_BTN_HOVER,
            command=lambda: flash_cb(side),
        )
        self.btn.pack(side="right")

        self.progress = ctk.CTkProgressBar(
            self, height=3, corner_radius=2,
            fg_color=C_CARD_BORDER, progress_color=C_SUCCESS,
        )
        self.progress.pack(fill="x", padx=12, pady=(0, 6))
        self.progress.set(0)

    def start_flash_progress(self):
        self.progress.configure(mode="indeterminate", indeterminate_speed=1.5)
        self.progress.start()
        self.btn.configure(state="disabled")

    def finish_flash_progress(self, success=True):
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(1.0 if success else 0)
        self.btn.configure(state="normal")


# ═══════════════════════════════════════════════════════════════════
#  App Principal
# ═══════════════════════════════════════════════════════════════════

class FlasherApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Sofle ZMK Flasher")
        self.geometry("780x820")
        self.resizable(False, False)
        self.configure(fg_color=C_BG)

        self._monitoring = True
        self._known_drives = set()
        self._build_polling = False
        self._last_run_id = None
        self._detected_drive = None
        self._auto_flash_side = None

        self._build_ui()
        self._scan_firmware_files()
        threading.Thread(target=self._usb_watch_loop, daemon=True).start()
        threading.Thread(target=self._poll_build_status, daemon=True).start()

    # ────────────────────── UI Build ──────────────────────

    def _build_ui(self):
        # ── Header ──
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(18, 0))
        ctk.CTkLabel(
            header, text="⌨️  Sofle ZMK Flasher",
            font=ctk.CTkFont(size=26, weight="bold"), text_color=C_TEXT,
        ).pack(side="left")

        self.usb_badge = ctk.CTkLabel(
            header, text="  USB ●  ", font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=C_CARD, corner_radius=12, text_color=C_TEXT_DIM,
        )
        self.usb_badge.pack(side="right", padx=(0, 4))

        self.gh_badge = ctk.CTkLabel(
            header, text="  Actions ●  ", font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=C_CARD, corner_radius=12, text_color=C_TEXT_DIM,
        )
        self.gh_badge.pack(side="right", padx=(0, 8))

        # ── Pipeline Steps ──
        pipe_frame = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=12,
                                  border_width=1, border_color=C_CARD_BORDER)
        pipe_frame.pack(fill="x", padx=24, pady=(14, 0))

        ctk.CTkLabel(pipe_frame, text="Pipeline", font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=C_TEXT).pack(anchor="w", padx=16, pady=(10, 2))

        steps_row = ctk.CTkFrame(pipe_frame, fg_color="transparent")
        steps_row.pack(fill="x", padx=16, pady=(4, 12))

        self.step_build = StepIndicator(steps_row, "1", "Build")
        self.step_download = StepIndicator(steps_row, "2", "Download")
        self.step_flash_l = StepIndicator(steps_row, "3", "Flash L")
        self.step_flash_r = StepIndicator(steps_row, "4", "Flash R")

        for i, step in enumerate([self.step_build, self.step_download,
                                  self.step_flash_l, self.step_flash_r]):
            step.pack(side="left", expand=True)
            if i < 3:
                sep = ctk.CTkLabel(steps_row, text="━━━", text_color=C_CARD_BORDER,
                                   font=ctk.CTkFont(size=10))
                sep.pack(side="left", padx=2)

        # ── Build progress bar ──
        self.build_progress = ctk.CTkProgressBar(
            pipe_frame, height=4, corner_radius=2,
            fg_color=C_CARD_BORDER, progress_color=C_ACCENT,
        )
        self.build_progress.pack(fill="x", padx=16, pady=(0, 10))
        self.build_progress.set(0)

        # ── GitHub Actions Card ──
        gh_card = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=12,
                               border_width=1, border_color=C_CARD_BORDER)
        gh_card.pack(fill="x", padx=24, pady=(10, 0))

        gh_header = ctk.CTkFrame(gh_card, fg_color="transparent")
        gh_header.pack(fill="x", padx=16, pady=(10, 0))
        ctk.CTkLabel(gh_header, text="GitHub Actions",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=C_TEXT).pack(side="left")

        self.build_status_var = ctk.StringVar(value="Verificando...")
        self.build_status_label = ctk.CTkLabel(
            gh_header, textvariable=self.build_status_var,
            font=ctk.CTkFont(size=11), text_color=C_TEXT_DIM,
        )
        self.build_status_label.pack(side="right")

        btn_row = ctk.CTkFrame(gh_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(8, 12))

        self.btn_trigger = ctk.CTkButton(
            btn_row, text="  Trigger Build  ", command=self.trigger_build,
            fg_color=C_ACCENT, hover_color=C_ACCENT_HOVER,
            corner_radius=8, height=34, font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.btn_trigger.pack(side="left", padx=(0, 8))

        self.btn_download = ctk.CTkButton(
            btn_row, text="  Baixar Firmware  ", command=self.download_firmware,
            fg_color=C_GREEN_BTN, hover_color=C_GREEN_BTN_HOVER,
            corner_radius=8, height=34, font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.btn_download.pack(side="left")

        self.download_progress = ctk.CTkProgressBar(
            gh_card, height=4, corner_radius=2,
            fg_color=C_CARD_BORDER, progress_color=C_SUCCESS,
        )
        self.download_progress.pack(fill="x", padx=16, pady=(0, 10))
        self.download_progress.set(0)

        # ── USB Detection Card ──
        usb_card = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=12,
                                border_width=1, border_color=C_CARD_BORDER)
        usb_card.pack(fill="x", padx=24, pady=(10, 0))

        usb_header = ctk.CTkFrame(usb_card, fg_color="transparent")
        usb_header.pack(fill="x", padx=16, pady=(10, 0))
        ctk.CTkLabel(usb_header, text="Detecção USB",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=C_TEXT).pack(side="left")

        self.usb_status_var = ctk.StringVar(value="Monitorando...")
        ctk.CTkLabel(usb_header, textvariable=self.usb_status_var,
                     font=ctk.CTkFont(size=11), text_color=C_TEXT_DIM).pack(side="right")

        self.usb_detail_var = ctk.StringVar(value="Aguardando teclado em modo bootloader")
        self.usb_detail_label = ctk.CTkLabel(
            usb_card, textvariable=self.usb_detail_var,
            font=ctk.CTkFont(size=12, weight="bold"), text_color=C_TEXT_DIM,
        )
        self.usb_detail_label.pack(padx=16, pady=(6, 4))

        # Auto-flash toggle
        af_row = ctk.CTkFrame(usb_card, fg_color="transparent")
        af_row.pack(fill="x", padx=16, pady=(0, 10))

        ctk.CTkLabel(af_row, text="Auto-flash ao detectar:",
                     font=ctk.CTkFont(size=11), text_color=C_TEXT_DIM).pack(side="left")

        self.auto_flash_var = ctk.StringVar(value="off")
        self.af_menu = ctk.CTkSegmentedButton(
            af_row, values=["off", "left", "right"],
            variable=self.auto_flash_var,
            font=ctk.CTkFont(size=11), height=26,
            selected_color=C_ACCENT, selected_hover_color=C_ACCENT_HOVER,
            unselected_color=C_CARD_BORDER,
        )
        self.af_menu.pack(side="right")

        # ── Firmware Cards ──
        self.fw_container = ctk.CTkFrame(self, fg_color="transparent")
        self.fw_container.pack(fill="x", padx=24, pady=(10, 0))

        fw_label_row = ctk.CTkFrame(self.fw_container, fg_color="transparent")
        fw_label_row.pack(fill="x")
        ctk.CTkLabel(fw_label_row, text="Firmwares",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=C_TEXT).pack(side="left")
        self.fw_count_label = ctk.CTkLabel(
            fw_label_row, text="0 arquivos",
            font=ctk.CTkFont(size=11), text_color=C_TEXT_DIM,
        )
        self.fw_count_label.pack(side="right")

        self.fw_cards_frame = ctk.CTkFrame(self.fw_container, fg_color="transparent")
        self.fw_cards_frame.pack(fill="x", pady=(6, 0))
        self._fw_cards = {}

        # Placeholder se vazio
        self.fw_empty_label = ctk.CTkLabel(
            self.fw_cards_frame,
            text="Nenhum firmware encontrado — clique \"Baixar Firmware\"",
            font=ctk.CTkFont(size=11), text_color=C_TEXT_DIM,
        )
        self.fw_empty_label.pack(pady=8)

        # ── Log Console ──
        log_label = ctk.CTkFrame(self, fg_color="transparent")
        log_label.pack(fill="x", padx=24, pady=(12, 0))
        ctk.CTkLabel(log_label, text="Log",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=C_TEXT).pack(side="left")

        self.console = LogConsole(self)
        self.console.pack(fill="both", expand=True, padx=24, pady=(4, 16))

        self._log("Aplicação iniciada. Monitorando USB e GitHub Actions...")

    # ────────────────────── Helpers ──────────────────────

    def _log(self, msg):
        self.console.log(msg)

    def _gh_run(self, args):
        result = subprocess.run(
            ["gh"] + args, capture_output=True, text=True,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        return result.returncode, result.stdout, result.stderr

    # ────────────────────── USB Auto-Detection ──────────────────────

    def _usb_watch_loop(self):
        self._known_drives = set(get_removable_drives().keys())
        while self._monitoring:
            time.sleep(1)
            try:
                current = get_removable_drives()
                current_set = set(current.keys())
                new_drives = current_set - self._known_drives

                for letter in new_drives:
                    label = current.get(letter, "")
                    upper_label = label.upper()
                    is_bl = any(bl in upper_label for bl in BOOTLOADER_NAMES)
                    if is_bl:
                        self.after(0, self._on_bootloader_detected, letter, label)
                    else:
                        self.after(0, self._log, f"Nova unidade: {letter}:\\ ({label or '?'})")

                gone = self._known_drives - current_set
                if gone:
                    self.after(0, self._on_bootloader_gone)

                self._known_drives = current_set
            except Exception:
                pass

    def _on_bootloader_detected(self, letter, label):
        drive = f"{letter}:\\"
        self._detected_drive = drive
        self.usb_status_var.set("Conectado")
        self.usb_detail_var.set(f"{drive}  •  {label}")
        self.usb_detail_label.configure(text_color=C_SUCCESS)
        self.usb_badge.configure(text_color=C_SUCCESS, text="  USB ●  ")
        self._log(f"Bootloader detectado em {drive} ({label})")

        # Auto-flash
        af = self.auto_flash_var.get()
        if af in ("left", "right"):
            self._log(f"Auto-flash ativado → flasheando lado {af}...")
            self.flash(af)

    def _on_bootloader_gone(self):
        self._detected_drive = None
        self.usb_status_var.set("Monitorando...")
        self.usb_detail_var.set("Aguardando teclado em modo bootloader")
        self.usb_detail_label.configure(text_color=C_TEXT_DIM)
        self.usb_badge.configure(text_color=C_TEXT_DIM)

    # ────────────────────── GitHub Actions Polling ──────────────────────

    def _poll_build_status(self):
        first_check = True
        while self._monitoring:
            try:
                rc, out, _ = self._gh_run([
                    "run", "list", "--repo", REPO, "--limit", "1",
                    "--json", "databaseId,displayTitle,status,conclusion,createdAt",
                ])
                if rc == 0 and out.strip():
                    runs = json.loads(out)
                    if runs:
                        run = runs[0]
                        rid = run["databaseId"]
                        status = run["status"]
                        conclusion = run.get("conclusion") or ""
                        title = run.get("displayTitle", "")[:40]

                        if status == "completed":
                            if conclusion == "success":
                                self.after(0, self._set_build_success, rid, title)
                                if not first_check and self._last_run_id and rid != self._last_run_id:
                                    self.after(0, self._auto_download_firmware)
                            else:
                                self.after(0, self._set_build_fail, rid, title, conclusion)
                        elif status in ("in_progress", "queued", "waiting"):
                            self.after(0, self._set_build_running, rid, title, status)
                        else:
                            self.after(0, self.build_status_var.set, f"#{rid} {status}")

                        self._last_run_id = rid
                        first_check = False
            except Exception:
                pass
            time.sleep(10 if self._build_polling else 30)

    def _set_build_success(self, rid, title):
        self.build_status_var.set(f"✓ #{rid}  sucesso — {title}")
        self.build_status_label.configure(text_color=C_SUCCESS)
        self.gh_badge.configure(text_color=C_SUCCESS)
        self.step_build.set_state("success", "Concluído")
        self._stop_build_progress()
        self.build_progress.set(1.0)
        self._build_polling = False

    def _set_build_fail(self, rid, title, conclusion):
        self.build_status_var.set(f"✗ #{rid}  {conclusion} — {title}")
        self.build_status_label.configure(text_color=C_ERROR)
        self.gh_badge.configure(text_color=C_ERROR)
        self.step_build.set_state("error", conclusion)
        self._stop_build_progress()
        self._build_polling = False

    def _set_build_running(self, rid, title, status):
        self.build_status_var.set(f"#{rid}  {status} — {title}")
        self.build_status_label.configure(text_color=C_WARNING)
        self.gh_badge.configure(text_color=C_WARNING)
        self.step_build.set_state("active", status)
        self.build_progress.configure(mode="indeterminate", indeterminate_speed=0.8)
        self.build_progress.start()

    # ────────────────────── Trigger Build ──────────────────────

    def trigger_build(self):
        self.btn_trigger.configure(state="disabled")
        self._build_polling = True
        self.step_build.set_state("active", "Disparando...")
        self.build_progress.configure(mode="indeterminate", indeterminate_speed=0.8)
        self.build_progress.start()
        self._log("Disparando build no GitHub Actions...")

        # Reset pipeline steps 2-4
        self.step_download.set_state("idle")
        self.step_flash_l.set_state("idle")
        self.step_flash_r.set_state("idle")

        threading.Thread(target=self._trigger_thread, daemon=True).start()

    def _trigger_thread(self):
        try:
            rc, _, err = self._gh_run(["workflow", "run", "build.yml", "--repo", REPO])
            if rc == 0:
                self.after(0, self._log, "Build disparado! Aguardando início...")
                self.after(0, self.build_status_var.set, "Build disparado — aguardando...")
            else:
                self.after(0, self._log, f"Erro ao disparar build: {err[:120]}")
                self.after(0, self.step_build.set_state, "error", "Falha")
                self.after(0, self._stop_build_progress)
        except Exception as e:
            self.after(0, self._log, f"Erro: {e}")
        finally:
            self.after(0, lambda: self.btn_trigger.configure(state="normal"))

    def _stop_build_progress(self):
        self.build_progress.stop()
        self.build_progress.configure(mode="determinate")
        self.build_progress.set(0)

    # ────────────────────── Download Firmware ──────────────────────

    def download_firmware(self):
        self.btn_download.configure(state="disabled")
        self.step_download.set_state("active", "Baixando...")
        self.download_progress.configure(mode="indeterminate", indeterminate_speed=1.2)
        self.download_progress.start()
        self._log("Iniciando download do firmware...")
        threading.Thread(target=self._download_thread, daemon=True).start()

    def _auto_download_firmware(self):
        self._log("Novo build com sucesso detectado! Baixando automaticamente...")
        self.step_download.set_state("active", "Auto-download...")
        self.download_progress.configure(mode="indeterminate", indeterminate_speed=1.2)
        self.download_progress.start()
        threading.Thread(target=self._download_thread, daemon=True).start()

    def _download_thread(self):
        try:
            os.makedirs(FIRMWARE_DIR, exist_ok=True)
            for root, dirs, files in os.walk(FIRMWARE_DIR, topdown=False):
                for name in files:
                    os.remove(os.path.join(root, name))
                for name in dirs:
                    os.rmdir(os.path.join(root, name))

            rc, _, err = self._gh_run([
                "run", "download", "--repo", REPO, "--dir", FIRMWARE_DIR,
            ])
            if rc == 0:
                self.after(0, self._on_download_ok)
            else:
                self.after(0, self._on_download_fail, err)
        except Exception as e:
            self.after(0, self._on_download_fail, str(e))
        finally:
            self.after(0, lambda: self.btn_download.configure(state="normal"))

    def _on_download_ok(self):
        self.download_progress.stop()
        self.download_progress.configure(mode="determinate")
        self.download_progress.set(1.0)
        self.step_download.set_state("success", "Pronto")
        self._log("Firmware baixado com sucesso!")
        self._scan_firmware_files()

    def _on_download_fail(self, err):
        self.download_progress.stop()
        self.download_progress.configure(mode="determinate")
        self.download_progress.set(0)
        self.step_download.set_state("error", "Falha")
        self._log(f"Erro no download: {err[:120]}")

    # ────────────────────── Firmware Scanning ──────────────────────

    def _find_uf2_files(self):
        uf2s = []
        if os.path.exists(FIRMWARE_DIR):
            for root, _, files in os.walk(FIRMWARE_DIR):
                for f in files:
                    if f.endswith(".uf2"):
                        uf2s.append(os.path.join(root, f))
        return uf2s

    def _classify_fw(self, name):
        low = name.lower()
        if "reset" in low:
            return "reset", "⚠️"
        if "left" in low:
            return "left", "⬅️"
        if "right" in low:
            return "right", "➡️"
        return "other", "📄"

    def _scan_firmware_files(self):
        uf2s = self._find_uf2_files()

        # Limpa cards antigos
        for w in self.fw_cards_frame.winfo_children():
            w.destroy()
        self._fw_cards.clear()

        if not uf2s:
            self.fw_count_label.configure(text="0 arquivos")
            self.fw_empty_label = ctk.CTkLabel(
                self.fw_cards_frame,
                text="Nenhum firmware encontrado — clique \"Baixar Firmware\"",
                font=ctk.CTkFont(size=11), text_color=C_TEXT_DIM,
            )
            self.fw_empty_label.pack(pady=8)
            return

        self.fw_count_label.configure(text=f"{len(uf2s)} arquivo{'s' if len(uf2s) != 1 else ''}")
        for path in sorted(uf2s):
            name = os.path.basename(path)
            side, icon = self._classify_fw(name)
            card = FirmwareCard(self.fw_cards_frame, icon, name, side, self.flash)
            card.pack(fill="x", pady=2)
            self._fw_cards[side] = card

    # ────────────────────── Flash ──────────────────────

    def flash(self, side):
        drive = self._detected_drive
        if not drive or not os.path.exists(drive):
            removables = get_removable_drives()
            bl_found = None
            for letter, label in removables.items():
                if any(bl in label.upper() for bl in BOOTLOADER_NAMES):
                    bl_found = f"{letter}:\\"
                    break
            if bl_found:
                drive = bl_found
            else:
                messagebox.showerror(
                    "Erro",
                    "Nenhum teclado em modo bootloader!\n\n"
                    "1. Conecte via USB\n"
                    "2. Duplo clique no botão RESET\n"
                    "3. Aguarde a detecção automática",
                )
                return

        uf2s = self._find_uf2_files()
        if not uf2s:
            messagebox.showerror("Erro", "Nenhum firmware .uf2 encontrado.\nBaixe o firmware primeiro.")
            return

        target = None
        for f in uf2s:
            bn = os.path.basename(f).lower()
            if side == "left" and "left" in bn and "reset" not in bn:
                target = f
            elif side == "right" and "right" in bn:
                target = f
            elif side == "reset" and "reset" in bn:
                target = f

        if not target:
            messagebox.showerror("Erro", f"Firmware '{side}' não encontrado.")
            return

        card = self._fw_cards.get(side)
        step = {"left": self.step_flash_l, "right": self.step_flash_r}.get(side)

        if card:
            card.start_flash_progress()
        if step:
            step.set_state("active", "Copiando...")

        filename = os.path.basename(target)
        self._log(f"Copiando {filename} → {drive}")

        threading.Thread(
            target=self._flash_thread,
            args=(target, drive, side, filename, card, step),
            daemon=True,
        ).start()

    def _flash_thread(self, target, drive, side, filename, card, step):
        try:
            shutil.copy2(target, drive)
            self.after(0, self._on_flash_ok, side, filename, card, step)
        except Exception as e:
            self.after(0, self._on_flash_fail, side, str(e), card, step)

    def _on_flash_ok(self, side, filename, card, step):
        if card:
            card.finish_flash_progress(True)
        if step:
            step.set_state("success", "Instalado!")
        self._log(f"✓ {filename} instalado com sucesso!")
        messagebox.showinfo("Sucesso", f"Firmware '{side}' instalado!\nO teclado reiniciará automaticamente.")

    def _on_flash_fail(self, side, err, card, step):
        if card:
            card.finish_flash_progress(False)
        if step:
            step.set_state("error", "Falha")
        self._log(f"✗ Erro ao flashear {side}: {err}")
        messagebox.showerror("Erro", f"Falha ao copiar:\n{err}")

    # ────────────────────── Cleanup ──────────────────────

    def destroy(self):
        self._monitoring = False
        super().destroy()


if __name__ == "__main__":
    app = FlasherApp()
    app.mainloop()
