#!/usr/bin/env python3
"""
client.py - Cliente GUI simple para conectarse al servidor del vehículo.
Guarda como clients/python_client/client.py y ejecuta: python3 client.py
No necesita librerías externas.
"""

import socket, threading, time
import tkinter as tk
from tkinter import ttk, messagebox
import queue

BUFFER = 4096

class TelemetryClient:
    def __init__(self):
        self.sock = None
        self.receiver_thread = None
        self.running = False
        self.q = queue.Queue()  # para pasar mensajes al hilo GUI

    def connect(self, host, port):
        if self.sock:
            self.close()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(5.0)
        self.sock.connect((host, int(port)))
        self.sock.settimeout(None)
        self.running = True
        self.receiver_thread = threading.Thread(target=self._receiver, daemon=True)
        self.receiver_thread.start()
        return True

    def close(self):
        self.running = False
        try:
            if self.sock:
                self.sock.shutdown(socket.SHUT_RDWR)
                self.sock.close()
        except:
            pass
        self.sock = None

    def send_line(self, line):
        if not self.sock:
            raise RuntimeError("No conectado")
        if not line.endswith("\n"):
            line = line + "\n"
        self.sock.sendall(line.encode('utf-8'))

    def _receiver(self):
        buff = b""
        while self.running:
            try:
                data = self.sock.recv(BUFFER)
                if not data:
                    self.q.put(("status", "DISCONNECTED"))
                    break
                buff += data
                while b"\n" in buff:
                    line, buff = buff.split(b"\n", 1)
                    try:
                        text = line.decode('utf-8').strip()
                    except:
                        continue
                    # pasar al GUI
                    self.q.put(("line", text))
            except Exception as e:
                # señal de desconexión
                self.q.put(("status", "DISCONNECTED"))
                break
        self.running = False

    def get_message_nowait(self):
        try:
            return self.q.get_nowait()
        except queue.Empty:
            return None

# ---------- GUI ----------
class App:
    def __init__(self, root):
        self.root = root
        root.title("Cliente Vehículo - Telemetría")
        self.client = TelemetryClient()

        frm = ttk.Frame(root, padding=10)
        frm.grid(row=0, column=0, sticky="nsew")

        # conexión
        ttk.Label(frm, text="Host:").grid(row=0, column=0, sticky="w")
        self.host_entry = ttk.Entry(frm, width=18); self.host_entry.grid(row=0, column=1)
        self.host_entry.insert(0, "18.215.151.54")  # cámbialo

        ttk.Label(frm, text="Port:").grid(row=0, column=2, sticky="w")
        self.port_entry = ttk.Entry(frm, width=6); self.port_entry.grid(row=0, column=3)
        self.port_entry.insert(0, "5000")

        self.btn_connect = ttk.Button(frm, text="Conectar", command=self.toggle_connect)
        self.btn_connect.grid(row=0, column=4, padx=6)

        # telemetría
        self.lbl_speed = ttk.Label(frm, text="Velocidad: -- m/s", font=("Segoe UI", 12))
        self.lbl_speed.grid(row=1, column=0, columnspan=2, pady=(10,0), sticky="w")
        self.lbl_batt = ttk.Label(frm, text="Batería: -- %", font=("Segoe UI", 12))
        self.lbl_batt.grid(row=1, column=2, columnspan=2, pady=(10,0), sticky="w")
        self.lbl_dir = ttk.Label(frm, text="Dirección: --", font=("Segoe UI", 12))
        self.lbl_dir.grid(row=1, column=4, pady=(10,0), sticky="w")

        # auth / subscribe
        ttk.Label(frm, text="Usuario:").grid(row=2, column=0, sticky="w", pady=(10,0))
        self.user_entry = ttk.Entry(frm, width=12); self.user_entry.grid(row=2, column=1, pady=(10,0))
        ttk.Label(frm, text="Pass:").grid(row=2, column=2, sticky="w", pady=(10,0))
        self.pass_entry = ttk.Entry(frm, width=12, show="*"); self.pass_entry.grid(row=2, column=3, pady=(10,0))
        self.btn_auth = ttk.Button(frm, text="AUTH", command=self.do_auth); self.btn_auth.grid(row=2, column=4, pady=(10,0))

        self.btn_sub_obs = ttk.Button(frm, text="SUBSCRIBE OBSERVER", command=lambda: self.send("SUBSCRIBE OBSERVER"))
        self.btn_sub_obs.grid(row=3, column=0, columnspan=2, pady=(10,0))
        self.btn_sub_admin = ttk.Button(frm, text="SUBSCRIBE ADMIN", command=lambda: self.send("SUBSCRIBE ADMIN"))
        self.btn_sub_admin.grid(row=3, column=2, columnspan=2, pady=(10,0))

        # comandos
        self.btn_speed_up = ttk.Button(frm, text="SPEED UP", command=lambda: self.send("CMD SPEED UP"))
        self.btn_speed_up.grid(row=4, column=0, pady=(12,0))
        self.btn_slow = ttk.Button(frm, text="SLOW DOWN", command=lambda: self.send("CMD SLOW DOWN"))
        self.btn_slow.grid(row=4, column=1)
        self.btn_left = ttk.Button(frm, text="TURN LEFT", command=lambda: self.send("CMD TURN LEFT"))
        self.btn_left.grid(row=4, column=2)
        self.btn_right = ttk.Button(frm, text="TURN RIGHT", command=lambda: self.send("CMD TURN RIGHT"))
        self.btn_right.grid(row=4, column=3)

        # status / raw
        self.status_var = tk.StringVar(value="DISCONNECTED")
        ttk.Label(frm, textvariable=self.status_var).grid(row=5, column=0, columnspan=5, sticky="w", pady=(10,0))
        self.raw = tk.Text(frm, height=10, width=72)
        self.raw.grid(row=6, column=0, columnspan=5, pady=(6,0))

        # polling mensajes
        self.root.after(150, self.poll)

    def toggle_connect(self):
        if self.client.sock:
            self.client.close()
            self.status_var.set("DISCONNECTED")
            self.btn_connect.config(text="Conectar")
        else:
            host = self.host_entry.get().strip()
            port = self.port_entry.get().strip()
            try:
                self.client.connect(host, int(port))
            except Exception as e:
                messagebox.showerror("Conexión", f"No se pudo conectar: {e}")
                return
            self.status_var.set("CONNECTED")
            self.btn_connect.config(text="Desconectar")
            # subscribe observer por defecto
            time.sleep(0.1)
            try:
                self.send("SUBSCRIBE OBSERVER")
            except:
                pass

    def do_auth(self):
        user = self.user_entry.get().strip()
        pw = self.pass_entry.get().strip()
        if not user or not pw:
            messagebox.showinfo("AUTH", "Escribe usuario y contraseña")
            return
        try:
            self.send(f"AUTH {user} {pw}")
        except Exception as e:
            messagebox.showerror("AUTH", f"Error: {e}")

    def send(self, txt):
        try:
            self.client.send_line(txt)
            self.raw.insert("end", f"> {txt}\n")
            self.raw.see("end")
        except Exception as e:
            messagebox.showerror("Error", f"No conectado: {e}")

    def poll(self):
        msg = self.client.get_message_nowait()
        while msg:
            kind, payload = msg
            if kind == "line":
                self.raw.insert("end", f"< {payload}\n")
                self.raw.see("end")
                self.handle_server_line(payload)
            elif kind == "status":
                self.status_var.set(payload)
            msg = self.client.get_message_nowait()
        self.root.after(150, self.poll)

    def handle_server_line(self, line):
        # parse TELEMETRY v=.. battery=.. dir=.. timestamp=..
        if line.startswith("TELEMETRY"):
            # split by spaces and parse key=val
            parts = line.split()
            kv = {}
            for p in parts[1:]:
                if "=" in p:
                    k,v = p.split("=",1)
                    kv[k] = v
            v = kv.get("v")
            b = kv.get("battery")
            d = kv.get("dir")
            if v is not None:
                self.lbl_speed.config(text=f"Velocidad: {v} m/s")
            if b is not None:
                self.lbl_batt.config(text=f"Batería: {b} %")
            if d is not None:
                self.lbl_dir.config(text=f"Dirección: {d}")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
