import os
import sys
import subprocess
import threading
import shutil
import glob
import string

# Auto-instala customtkinter se necessário
try:
    import customtkinter as ctk
except ImportError:
    print("Instalando interface gráfica (customtkinter)...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "customtkinter"])
    import customtkinter as ctk

from tkinter import messagebox

# Configuração de tema da interface
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class FlasherApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Sofle ZMK Flasher - AutoInstall")
        self.geometry("600x500")
        self.resizable(False, False)
        
        # Título
        self.label = ctk.CTkLabel(self, text="Gerenciador ZMK - Sofle Keyboard", font=ctk.CTkFont(size=22, weight="bold"))
        self.label.pack(pady=(20, 10))
        
        # --- Passo 1: Download ---
        self.frame1 = ctk.CTkFrame(self)
        self.frame1.pack(pady=10, padx=20, fill="x")
        
        self.step1_label = ctk.CTkLabel(self.frame1, text="1. Baixar Firmware Mais Recente do GitHub", font=ctk.CTkFont(weight="bold"))
        self.step1_label.pack(pady=(10, 5))
        
        self.btn_download = ctk.CTkButton(self.frame1, text="⬇️ Baixar do GitHub Actions", command=self.download_firmware)
        self.btn_download.pack(pady=(0, 10))
        
        # --- Passo 2: Seleção da Unidade ---
        self.frame2 = ctk.CTkFrame(self)
        self.frame2.pack(pady=10, padx=20, fill="x")
        
        self.step2_label = ctk.CTkLabel(self.frame2, text="2. Selecione a Unidade do Teclado (Conecte via USB e dê duplo clique no Reset)", font=ctk.CTkFont(weight="bold"))
        self.step2_label.pack(pady=(10, 5))
        
        self.drive_var = ctk.StringVar(value="")
        drive_frame = ctk.CTkFrame(self.frame2, fg_color="transparent")
        drive_frame.pack(pady=(0, 10))
        
        self.combo_drives = ctk.CTkComboBox(drive_frame, values=self.get_drives(), variable=self.drive_var, width=200)
        self.combo_drives.pack(side="left", padx=10)
        
        self.btn_refresh = ctk.CTkButton(drive_frame, text="🔄 Atualizar Unidades", command=self.refresh_drives, width=140)
        self.btn_refresh.pack(side="left")
        
        # --- Passo 3: Flash (Instalar) ---
        self.frame3 = ctk.CTkFrame(self)
        self.frame3.pack(pady=10, padx=20, fill="x")

        self.step3_label = ctk.CTkLabel(self.frame3, text="3. Instalar o Firmware no Teclado", font=ctk.CTkFont(weight="bold"))
        self.step3_label.pack(pady=(10, 5))
        
        self.action_frame = ctk.CTkFrame(self.frame3, fg_color="transparent")
        self.action_frame.pack(pady=(0, 10))
        
        self.btn_flash_left = ctk.CTkButton(self.action_frame, text="⬅️ Lado Esquerdo", command=lambda: self.flash("left"), fg_color="#2b6b3e", hover_color="#1f4d2c")
        self.btn_flash_left.pack(side="left", padx=10)
        
        self.btn_flash_right = ctk.CTkButton(self.action_frame, text="Lado Direito ➡️", command=lambda: self.flash("right"), fg_color="#2b6b3e", hover_color="#1f4d2c")
        self.btn_flash_right.pack(side="left", padx=10)
        
        self.btn_flash_reset = ctk.CTkButton(self.action_frame, text="⚠️ Reset Settings", command=lambda: self.flash("reset"), fg_color="#8b2f2f", hover_color="#632121")
        self.btn_flash_reset.pack(side="left", padx=10)
        
        # Status Bar
        self.status_var = ctk.StringVar(value="Status: Aguardando ação...")
        self.status_label = ctk.CTkLabel(self, textvariable=self.status_var, text_color="yellow", font=ctk.CTkFont(size=12))
        self.status_label.pack(pady=(10, 0), side="bottom")

    def get_drives(self):
        drives = []
        for letter in string.ascii_uppercase:
            if letter == "C": continue
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                drives.append(drive)
        return drives if drives else ["Nenhuma unidade encontrada"]
    
    def refresh_drives(self):
        drives = self.get_drives()
        self.combo_drives.configure(values=drives)
        if drives:
            self.combo_drives.set(drives[0])
            self.status_var.set("Status: Lista de unidades atualizada.")

    def download_firmware(self):
        self.status_var.set("Status: Baixando do GitHub... (Pode demorar alguns segundos)")
        self.btn_download.configure(state="disabled")
        threading.Thread(target=self._download_thread, daemon=True).start()
        
    def _download_thread(self):
        try:
            os.makedirs("firmwares", exist_ok=True)
            for root, dirs, files in os.walk("firmwares", topdown=False):
                for name in files: os.remove(os.path.join(root, name))
                for name in dirs: os.rmdir(os.path.join(root, name))
            
            # Comando gh run download (baixa o artefato mais recente da branch padrao)
            result = subprocess.run(["gh", "run", "download", "--dir", "firmwares"], 
                                  capture_output=True, text=True, shell=True)
            
            if result.returncode == 0:
                self.status_var.set("Status: Sucesso! Firmware baixado na pasta 'firmwares'.")
            else:
                self.status_var.set("Status: Erro ao baixar. O GitHub Actions terminou de compilar?")
                print(result.stderr)
        except Exception as e:
            self.status_var.set(f"Erro no download: {str(e)}")
        finally:
            self.btn_download.configure(state="normal")
            
    def flash(self, side):
        drive = self.drive_var.get()
        if not drive or drive.startswith("Nenhuma") or not os.path.exists(drive):
            messagebox.showerror("Erro", "Conecte o teclado, DÊ DUPLO CLIQUE NO BOTÃO DE RESET dele\ne então atualize a lista para selecionar a unidade correta!")
            return
            
        self.status_var.set(f"Status: Buscando arquivo UF2 para '{side}'...")
        uf2_files = []
        for root, dirs, files in os.walk("firmwares"):
            for f in files:
                if f.endswith(".uf2"):
                    uf2_files.append(os.path.join(root, f))
        
        target_file = None
        if side == "left":
            target_file = next((f for f in uf2_files if "left" in f.lower() and "reset" not in f.lower()), None)
        elif side == "right":
            target_file = next((f for f in uf2_files if "right" in f.lower() and "reset" not in f.lower()), None)
        elif side == "reset":
            target_file = next((f for f in uf2_files if "reset" in f.lower()), None)
            
        if not target_file:
            messagebox.showerror("Aviso", f"Arquivo .uf2 não encontrado para a opção: {side}.\nVocê já clicou em 'Baixar Firmware'? Se sim, aguarde o fim da compilação no GitHub.")
            return
            
        try:
            filename = os.path.basename(target_file)
            self.status_var.set(f"Status: Copiando {filename} para {drive} ...")
            
            # Copia efetiva (bloqueante, mas pra uf2 eh rapido)
            shutil.copy2(target_file, drive)
            
            self.status_var.set(f"Status: {filename} instalado com sucesso!")
            messagebox.showinfo("Finalizado", f"Instalação do {side} concluída!\nA unidade foi ejetada e o teclado reiniciará sozinho.")
            self.refresh_drives() # Atualiza lista porque teclado desvanece
        except Exception as e:
            self.status_var.set(f"Erro crítico: {str(e)}")
            messagebox.showerror("Erro de Cópia", f"Aconteceu um erro:\n{str(e)}\nO pendrive desapareceu antes da cópia? Tente plugar direto na placa mãe.")

if __name__ == "__main__":
    app = FlasherApp()
    app.refresh_drives()
    app.mainloop()
