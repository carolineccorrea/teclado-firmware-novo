import os
import sys
import subprocess
import threading
import shutil
import json
import string
import time
import ctypes

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

# Garante que gh CLI está no PATH (Windows pode não herdar o PATH atualizado)
def _ensure_gh_in_path():
    import winreg
    try:
        machine_path = winreg.QueryValueEx(
            winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            "Path"
        )[0]
    except Exception:
        machine_path = ""
    try:
        user_path = winreg.QueryValueEx(
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment"),
            "Path"
        )[0]
    except Exception:
        user_path = ""
    full = machine_path + ";" + user_path
    os.environ["PATH"] = full + ";" + os.environ.get("PATH", "")

_ensure_gh_in_path()


def get_volume_label(drive_letter):
    """Retorna o label do volume de uma unidade Windows."""
    buf = ctypes.create_unicode_buffer(256)
    try:
        ctypes.windll.kernel32.GetVolumeInformationW(
            f"{drive_letter}:\\", buf, 256, None, None, None, None, 0
        )
        return buf.value.strip()
    except Exception:
        return ""


def get_removable_drives():
    """Retorna dict {letter: label} de drives removíveis."""
    drives = {}
    for letter in string.ascii_uppercase:
        if letter == "C":
            continue
        drive_path = f"{letter}:\\"
        try:
            dtype = ctypes.windll.kernel32.GetDriveTypeW(drive_path)
            if dtype == 2:  # DRIVE_REMOVABLE
                label = get_volume_label(letter)
                drives[letter] = label
        except Exception:
            pass
    return drives


class FlasherApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Sofle ZMK Flasher")
        self.geometry("720x680")
        self.resizable(False, False)

        self._monitoring = True
        self._known_drives = set()
        self._build_polling = False
        self._last_run_id = None

        # ── Header ──
        ctk.CTkLabel(
            self,
            text="⌨️  Sofle ZMK Flasher",
            font=ctk.CTkFont(size=24, weight="bold"),
        ).pack(pady=(18, 4))
        ctk.CTkLabel(
            self, text="Detecção automática • GitHub Actions • Flash",
            text_color="gray60", font=ctk.CTkFont(size=12),
        ).pack(pady=(0, 12))

        # ── GitHub Actions ──
        gh_frame = ctk.CTkFrame(self)
        gh_frame.pack(pady=6, padx=20, fill="x")

        ctk.CTkLabel(gh_frame, text="GitHub Actions", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(10, 2))

        self.build_status_var = ctk.StringVar(value="⏳ Verificando último build...")
        self.build_status_label = ctk.CTkLabel(
            gh_frame, textvariable=self.build_status_var,
            font=ctk.CTkFont(size=12),
        )
        self.build_status_label.pack(pady=2)

        btn_row = ctk.CTkFrame(gh_frame, fg_color="transparent")
        btn_row.pack(pady=(4, 10))

        self.btn_trigger = ctk.CTkButton(
            btn_row, text="🚀 Trigger Build", command=self.trigger_build,
            fg_color="#4a4a8a", hover_color="#3a3a6a", width=160,
        )
        self.btn_trigger.pack(side="left", padx=6)

        self.btn_download = ctk.CTkButton(
            btn_row, text="⬇️ Baixar Firmware", command=self.download_firmware,
            width=160,
        )
        self.btn_download.pack(side="left", padx=6)

        # ── Detecção USB ──
        usb_frame = ctk.CTkFrame(self)
        usb_frame.pack(pady=6, padx=20, fill="x")

        ctk.CTkLabel(usb_frame, text="Detecção USB (automática)", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(10, 2))

        self.usb_status_var = ctk.StringVar(value="🔍 Monitorando drives USB...")
        self.usb_status_label = ctk.CTkLabel(
            usb_frame, textvariable=self.usb_status_var,
            font=ctk.CTkFont(size=12),
        )
        self.usb_status_label.pack(pady=2)

        self.detected_drive_var = ctk.StringVar(value="Nenhum teclado detectado")
        self.detected_label = ctk.CTkLabel(
            usb_frame, textvariable=self.detected_drive_var,
            text_color="#00dd00", font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.detected_label.pack(pady=(2, 10))

        # ── Firmware Files ──
        fw_frame = ctk.CTkFrame(self)
        fw_frame.pack(pady=6, padx=20, fill="x")

        ctk.CTkLabel(fw_frame, text="Firmwares Disponíveis", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(10, 4))

        self.fw_list_var = ctk.StringVar(value="Nenhum .uf2 encontrado")
        ctk.CTkLabel(fw_frame, textvariable=self.fw_list_var, font=ctk.CTkFont(size=11), justify="left").pack(pady=(0, 4))

        # ── Flash Buttons ──
        flash_frame = ctk.CTkFrame(self)
        flash_frame.pack(pady=6, padx=20, fill="x")

        ctk.CTkLabel(flash_frame, text="Instalar Firmware", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(10, 6))

        btn_flash_row = ctk.CTkFrame(flash_frame, fg_color="transparent")
        btn_flash_row.pack(pady=(0, 12))

        self.btn_flash_left = ctk.CTkButton(
            btn_flash_row, text="⬅️ Lado Esquerdo",
            command=lambda: self.flash("left"),
            fg_color="#2b6b3e", hover_color="#1f4d2c", width=150,
        )
        self.btn_flash_left.pack(side="left", padx=8)

        self.btn_flash_right = ctk.CTkButton(
            btn_flash_row, text="Lado Direito ➡️",
            command=lambda: self.flash("right"),
            fg_color="#2b6b3e", hover_color="#1f4d2c", width=150,
        )
        self.btn_flash_right.pack(side="left", padx=8)

        self.btn_flash_reset = ctk.CTkButton(
            btn_flash_row, text="⚠️ Reset",
            command=lambda: self.flash("reset"),
            fg_color="#8b2f2f", hover_color="#632121", width=120,
        )
        self.btn_flash_reset.pack(side="left", padx=8)

        # ── Log ──
        self.status_var = ctk.StringVar(value="Iniciando...")
        ctk.CTkLabel(
            self, textvariable=self.status_var,
            text_color="yellow", font=ctk.CTkFont(size=11),
        ).pack(pady=(8, 8), side="bottom")

        # ── Iniciar threads ──
        self._scan_firmware_files()
        threading.Thread(target=self._usb_watch_loop, daemon=True).start()
        threading.Thread(target=self._poll_build_status, daemon=True).start()

    # ────────────────────── USB Auto-Detection ──────────────────────

    def _usb_watch_loop(self):
        """Polling loop: detecta quando teclado entra em bootloader."""
        self._known_drives = set(get_removable_drives().keys())
        while self._monitoring:
            time.sleep(1)
            try:
                current = get_removable_drives()
                current_set = set(current.keys())
                new_drives = current_set - self._known_drives

                if new_drives:
                    for letter in new_drives:
                        label = current.get(letter, "")
                        upper_label = label.upper()
                        is_bootloader = any(bl in upper_label for bl in BOOTLOADER_NAMES)

                        if is_bootloader:
                            self.after(0, self._on_bootloader_detected, letter, label)
                        else:
                            self.after(
                                0, self.usb_status_var.set,
                                f"📀 Nova unidade: {letter}:\\ ({label or 'sem nome'})",
                            )

                gone = self._known_drives - current_set
                if gone:
                    self.after(0, self.usb_status_var.set, "🔍 Monitorando drives USB...")
                    self.after(0, self.detected_drive_var.set, "Nenhum teclado detectado")
                    self.after(0, self.detected_label.configure, {"text_color": "gray60"})

                self._known_drives = current_set
            except Exception:
                pass

    def _on_bootloader_detected(self, letter, label):
        """Chamado na main thread quando bootloader é detectado."""
        drive = f"{letter}:\\"
        self.usb_status_var.set(f"✅ Bootloader detectado!")
        self.detected_drive_var.set(f"🎯 {drive}  ({label})")
        self.detected_label.configure(text_color="#00ff44")
        self._detected_drive = drive
        self.status_var.set(f"Teclado em modo bootloader em {drive} - pronto para flash!")

    # ────────────────────── GitHub Actions ──────────────────────

    def _gh_run(self, args):
        """Executa gh CLI e retorna (returncode, stdout, stderr)."""
        result = subprocess.run(
            ["gh"] + args, capture_output=True, text=True,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        return result.returncode, result.stdout, result.stderr

    def _poll_build_status(self):
        """Polling loop: verifica status do último build a cada 15s."""
        while self._monitoring:
            try:
                rc, out, _ = self._gh_run([
                    "run", "list", "--repo", REPO,
                    "--limit", "1",
                    "--json", "databaseId,displayTitle,status,conclusion,createdAt",
                ])
                if rc == 0 and out.strip():
                    runs = json.loads(out)
                    if runs:
                        run = runs[0]
                        rid = run["databaseId"]
                        status = run["status"]
                        conclusion = run.get("conclusion") or ""
                        title = run.get("displayTitle", "")[:50]

                        if status == "completed":
                            if conclusion == "success":
                                icon = "✅"
                                color = "#00dd00"
                                # Auto-download se é um run novo
                                if self._last_run_id and rid != self._last_run_id:
                                    self.after(0, self._auto_download_firmware)
                            else:
                                icon = "❌"
                                color = "#dd3333"
                        elif status in ("in_progress", "queued", "waiting"):
                            icon = "🔄"
                            color = "#ddaa00"
                        else:
                            icon = "⏳"
                            color = "gray60"

                        self._last_run_id = rid
                        msg = f"{icon} #{rid}  {status} {conclusion}  {title}"
                        self.after(0, self.build_status_var.set, msg)
                        self.after(0, self.build_status_label.configure, {"text_color": color})
            except Exception:
                pass

            # Polling mais rápido se build em progresso
            interval = 10 if self._build_polling else 30
            time.sleep(interval)

    def trigger_build(self):
        """Dispara workflow build.yml no GitHub Actions."""
        self.btn_trigger.configure(state="disabled")
        self.build_status_var.set("🚀 Disparando build...")
        self._build_polling = True
        threading.Thread(target=self._trigger_thread, daemon=True).start()

    def _trigger_thread(self):
        try:
            rc, out, err = self._gh_run([
                "workflow", "run", "build.yml", "--repo", REPO,
            ])
            if rc == 0:
                self.after(0, self.build_status_var.set, "🚀 Build disparado! Aguardando início...")
                self.after(0, self.status_var.set, "Build disparado no GitHub Actions. Polling ativo.")
            else:
                self.after(0, self.build_status_var.set, f"❌ Erro ao disparar: {err[:80]}")
        except Exception as e:
            self.after(0, self.build_status_var.set, f"❌ Erro: {e}")
        finally:
            self.after(0, lambda: self.btn_trigger.configure(state="normal"))

    # ────────────────────── Download Firmware ──────────────────────

    def download_firmware(self):
        self.btn_download.configure(state="disabled")
        self.status_var.set("⬇️ Baixando firmware do GitHub Actions...")
        threading.Thread(target=self._download_thread, daemon=True).start()

    def _auto_download_firmware(self):
        """Auto-download quando build novo completa com sucesso."""
        self.status_var.set("🔄 Novo build detectado! Baixando automaticamente...")
        threading.Thread(target=self._download_thread, daemon=True).start()

    def _download_thread(self):
        try:
            os.makedirs(FIRMWARE_DIR, exist_ok=True)
            # Limpa firmwares antigos
            for root, dirs, files in os.walk(FIRMWARE_DIR, topdown=False):
                for name in files:
                    os.remove(os.path.join(root, name))
                for name in dirs:
                    os.rmdir(os.path.join(root, name))

            rc, out, err = self._gh_run([
                "run", "download", "--repo", REPO, "--dir", FIRMWARE_DIR,
            ])

            if rc == 0:
                self.after(0, self.status_var.set, "✅ Firmware baixado com sucesso!")
                self.after(0, self._scan_firmware_files)
            else:
                self.after(0, self.status_var.set, f"❌ Erro no download: {err[:80]}")
        except Exception as e:
            self.after(0, self.status_var.set, f"❌ Erro: {e}")
        finally:
            self.after(0, lambda: self.btn_download.configure(state="normal"))

    # ────────────────────── Firmware File Scanning ──────────────────────

    def _scan_firmware_files(self):
        """Scan pasta firmwares e atualiza lista na UI."""
        uf2s = self._find_uf2_files()
        if uf2s:
            lines = []
            for f in uf2s:
                name = os.path.basename(f)
                if "left" in name.lower() and "reset" not in name.lower():
                    lines.append(f"  ⬅️  {name}")
                elif "right" in name.lower():
                    lines.append(f"  ➡️  {name}")
                elif "reset" in name.lower():
                    lines.append(f"  ⚠️  {name}")
                else:
                    lines.append(f"  📄  {name}")
            self.fw_list_var.set("\n".join(lines))
        else:
            self.fw_list_var.set("Nenhum .uf2 encontrado — clique 'Baixar Firmware'")

    def _find_uf2_files(self):
        """Retorna lista de caminhos .uf2 na pasta firmwares."""
        uf2s = []
        if os.path.exists(FIRMWARE_DIR):
            for root, _, files in os.walk(FIRMWARE_DIR):
                for f in files:
                    if f.endswith(".uf2"):
                        uf2s.append(os.path.join(root, f))
        return uf2s

    # ────────────────────── Flash ──────────────────────

    def flash(self, side):
        drive = getattr(self, "_detected_drive", None)
        if not drive or not os.path.exists(drive):
            # Tenta qualquer drive removível
            removables = get_removable_drives()
            if removables:
                letter = list(removables.keys())[0]
                drive = f"{letter}:\\"
            else:
                messagebox.showerror(
                    "Erro",
                    "Nenhuma unidade detectada!\n\n"
                    "1. Conecte o teclado via USB\n"
                    "2. Dê duplo clique no botão RESET\n"
                    "3. Aguarde a detecção automática",
                )
                return

        uf2s = self._find_uf2_files()
        if not uf2s:
            messagebox.showerror("Erro", "Nenhum firmware .uf2 encontrado.\nClique em 'Baixar Firmware' primeiro.")
            return

        target = None
        if side == "left":
            target = next((f for f in uf2s if "left" in os.path.basename(f).lower() and "reset" not in os.path.basename(f).lower()), None)
        elif side == "right":
            target = next((f for f in uf2s if "right" in os.path.basename(f).lower()), None)
        elif side == "reset":
            target = next((f for f in uf2s if "reset" in os.path.basename(f).lower()), None)

        if not target:
            messagebox.showerror("Erro", f"Arquivo .uf2 para '{side}' não encontrado.")
            return

        filename = os.path.basename(target)
        self.status_var.set(f"📝 Copiando {filename} → {drive}")
        try:
            shutil.copy2(target, drive)
            self.status_var.set(f"✅ {filename} instalado com sucesso em {drive}!")
            messagebox.showinfo("Sucesso", f"Firmware '{side}' instalado!\nO teclado irá reiniciar automaticamente.")
        except Exception as e:
            self.status_var.set(f"❌ Erro: {e}")
            messagebox.showerror("Erro", f"Falha ao copiar:\n{e}")

    def destroy(self):
        self._monitoring = False
        super().destroy()


if __name__ == "__main__":
    app = FlasherApp()
    app.mainloop()
