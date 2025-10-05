#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
client.py - Cliente GUI de Centro de Control Táctico (Nivel Alfa).
Mejoras: Volante Táctico corregido, Dashboard modularizado, Brújula funcional.
Usa solo tkinter. Ejecutar: python3 client.py
"""

import socket
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import queue
import math

BUFFER = 4096

# --------------------- Telemetry / Networking ---------------------
class TelemetryClient:
    def __init__(self):
        self.sock = None
        self.receiver_thread = None
        self.running = False
        self.q = queue.Queue()

    def connect(self, host, port, timeout=5.0):
        if self.sock:
            self.close()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        try:
            self.sock.connect((host, int(port)))
        except Exception as e:
            self.sock = None
            raise RuntimeError(f"Fallo al conectar: {e}")
        self.sock.settimeout(None)
        self.running = True
        self.receiver_thread = threading.Thread(target=self._receiver, daemon=True)
        self.receiver_thread.start()

    def close(self):
        self.running = False
        try:
            if self.sock:
                self.sock.shutdown(socket.SHUT_RDWR)
                self.sock.close()
        except Exception:
            pass
        self.sock = None

    def send_line(self, line):
        if not self.sock:
            raise RuntimeError("No conectado")
        if not line.endswith("\n"):
            line = line + "\n"
        try:
            self.sock.sendall(line.encode('utf-8'))
        except Exception as e:
            raise RuntimeError(f"Transmit error: {e}")

    def _receiver(self):
        buff = b""
        while self.running:
            try:
                data = self.sock.recv(BUFFER)
                if not data:
                    self.q.put(("status", "DESCONEXIÓN"))
                    break
                buff += data
                while b"\n" in buff:
                    line, buff = buff.split(b"\n", 1)
                    try:
                        txt = line.decode('utf-8').strip()
                    except Exception:
                        continue
                    self.q.put(("line", txt))
            except Exception as e:
                self.q.put(("status", f"ERROR: {e}"))
                break
        self.running = False

    def get_message_nowait(self):
        try:
            return self.q.get_nowait()
        except queue.Empty:
            return None

# --------------------- GUI ---------------------
class FancyClientApp:
    def __init__(self, root):
        self.root = root
        root.title("Centro de Control Táctico — Vehículo Autónomo")
        root.geometry("1200x750")
        
        # Palette - Acentos Cian y Púrpura/Magenta
        self.bg = "#071018"
        self.panel = "#0b1220"
        self.accent = "#2BEAF7"  # Cian Brillante
        self.accent2 = "#8B5CF6" # Púrpura/Magenta
        self.warning = "#FF5A5A"
        self.text = "#E6EEF6"
        self.dim = "#5C6670"
        self.shadow = "#021017"

        # Fonts (simulando "Orbitron" o "Consolas" para tech look)
        self.font_speed = ("Consolas", 72, "bold")
        self.font_title = ("Segoe UI", 14, "bold")
        self.font_label = ("Segoe UI", 10)
        self.font_mono = ("Consolas", 10)

        self.client = TelemetryClient()

        # State Variables
        self.speed_val = 0.0
        self.battery = 100
        self.direction = "N"
        self.connected = False
        self.current_heading_deg = 0.0 # Nuevo para rotación de aguja

        self._configure_styles()
        self._build_ui()
        self.root.after(120, self._poll)

    def _configure_styles(self):
        self.root.configure(bg=self.bg)
        style = ttk.Style()
        style.theme_use('clam') # Base theme

        style.configure('TFrame', background=self.panel)
        style.configure('TLabel', background=self.panel, foreground=self.text, font=self.font_label)
        style.configure('TEntry', fieldbackground="#161b22", foreground=self.text, insertbackground=self.accent)
        style.configure('TButton', background=self.accent2, foreground='white', font=("Segoe UI", 10, "bold"), borderwidth=0)
        style.map('TButton', background=[('active', self._shade(self.accent2, 30))])

    def _build_ui(self):
        # Main frames
        container = tk.Frame(self.root, bg=self.bg)
        container.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)
        container.grid_columnconfigure(0, weight=1)  # Left (Controls)
        container.grid_columnconfigure(1, weight=3)  # Center (Dashboard)
        container.grid_columnconfigure(2, weight=1)  # Right (Log)
        container.grid_rowconfigure(0, weight=1)

        # ------------------- PANEL IZQUIERDO: CONECTIVIDAD -------------------
        left = tk.Frame(container, bg=self.panel, bd=1, relief=tk.SOLID, highlightbackground=self._shade(self.panel, 15), highlightthickness=1)
        left.grid(row=0, column=0, sticky="nsew", padx=(0,15), pady=0)
        left.grid_rowconfigure(6, weight=1)

        tk.Label(left, text="[ MÓDULO DE ENLACE ]", bg=self.panel, fg=self.accent, font=self.font_title).pack(pady=(12,6))

        # Host / Port
        frm_conn = tk.Frame(left, bg=self.panel); frm_conn.pack(padx=15, pady=8, fill=tk.X)
        tk.Label(frm_conn, text="Host:", bg=self.panel, fg=self.text).grid(row=0, column=0, sticky="w", pady=2)
        self.ent_host = ttk.Entry(frm_conn, width=15); self.ent_host.grid(row=0, column=1, padx=6, sticky="ew")
        self.ent_host.insert(0, "3.80.67.50")
        tk.Label(frm_conn, text="Puerto:", bg=self.panel, fg=self.text).grid(row=1, column=0, sticky="w", pady=2)
        self.ent_port = ttk.Entry(frm_conn, width=8); self.ent_port.grid(row=1, column=1, padx=6, sticky="ew")
        self.ent_port.insert(0, "5000")
        self.btn_connect = ttk.Button(frm_conn, text="CONECTAR", command=self.toggle_connect)
        self.btn_connect.grid(row=2, column=0, columnspan=2, pady=(8,0), sticky="ew")

        # Auth
        frm_auth = tk.Frame(left, bg=self.panel); frm_auth.pack(padx=15, pady=10, fill=tk.X)
        tk.Label(frm_auth, text="Usuario:", bg=self.panel, fg=self.text).grid(row=0, column=0, sticky="w", pady=2)
        self.ent_user = ttk.Entry(frm_auth, width=12); self.ent_user.grid(row=0, column=1, padx=6, sticky="ew")
        tk.Label(frm_auth, text="Contraseña:", bg=self.panel, fg=self.text).grid(row=1, column=0, sticky="w", pady=2)
        self.ent_pass = ttk.Entry(frm_auth, width=12, show="*"); self.ent_pass.grid(row=1, column=1, padx=6, sticky="ew")
        self.btn_auth = ttk.Button(frm_auth, text="AUTENTICAR", command=self._do_auth)
        self.btn_auth.grid(row=2, column=0, columnspan=2, pady=(8,0), sticky="ew")

        # Subscription
        frm_sub = tk.Frame(left, bg=self.panel); frm_sub.pack(padx=15, pady=10, fill=tk.X)
        tk.Label(frm_sub, text="Rol de Suscripción:", bg=self.panel, fg=self.accent2).pack(anchor="w")
        ttk.Button(frm_sub, text="OBSERVER", command=lambda: self._send_cmd_text("SUBSCRIBE OBSERVER")).pack(fill=tk.X, pady=4)
        ttk.Button(frm_sub, text="ADMIN", command=lambda: self._send_cmd_text("SUBSCRIBE ADMIN")).pack(fill=tk.X, pady=4)

        # ------------------- PANEL CENTRAL: DASHBOARD -------------------
        center = tk.Frame(container, bg=self.panel, bd=1, relief=tk.SOLID, highlightbackground=self.accent, highlightthickness=2)
        center.grid(row=0, column=1, sticky="nsew")
        
        # Upper row: Speed + Gauge
        top_area = tk.Frame(center, bg=self.panel); top_area.pack(fill=tk.X, pady=(15, 5))
        top_area.grid_columnconfigure(0, weight=1); top_area.grid_columnconfigure(1, weight=1)

        # Speed display (Left Top)
        sp_frame = tk.Frame(top_area, bg=self.panel); sp_frame.grid(row=0, column=0, sticky="ew", padx=(20, 10))
        tk.Label(sp_frame, text="VELOCIDAD ACTUAL", bg=self.panel, fg=self.text, font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 5))
        self.lbl_speed_big = tk.Label(sp_frame, text="--", bg=self.panel, fg=self.accent, font=self.font_speed)
        self.lbl_speed_big.pack(anchor="center")
        tk.Label(sp_frame, text="METROS / SEGUNDO", bg=self.panel, fg=self.dim, font=self.font_label).pack(pady=(5, 0))

        # Gauge (Right Top)
        gauge_frame = tk.Frame(top_area, bg=self.panel); gauge_frame.grid(row=0, column=1, sticky="ew", padx=(10, 20))
        self.gauge_canvas = tk.Canvas(gauge_frame, width=380, height=190, bg=self.panel, highlightthickness=0)
        self.gauge_canvas.pack(padx=6, pady=6)
        self._draw_gauge_base()

        # Separator line
        tk.Frame(center, height=2, bg=self._shade(self.panel, 10)).pack(fill=tk.X, padx=15)

        # Lower row: Battery + Steering + Direction
        mid = tk.Frame(center, bg=self.panel); mid.pack(fill=tk.BOTH, expand=True, pady=(10, 15))
        mid.grid_columnconfigure(0, weight=1); mid.grid_columnconfigure(1, weight=2); mid.grid_columnconfigure(2, weight=1)

        # Battery (Left Bottom)
        batt_frame = tk.Frame(mid, bg=self.panel); batt_frame.grid(row=0, column=0, sticky="nsew", padx=20, pady=10)
        tk.Label(batt_frame, text="NIVEL DE ENERGÍA", bg=self.panel, fg=self.accent2, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.batt_canvas = tk.Canvas(batt_frame, width=70, height=180, bg=self.panel, highlightthickness=0)
        self.batt_canvas.pack(pady=6, anchor="center")
        self.batt_canvas.create_rectangle(20, 10, 50, 160, outline=self.dim, width=2)
        self.batt_fill = self.batt_canvas.create_rectangle(22, 160, 48, 160, fill=self.accent, width=0)
        self.batt_label = tk.Label(batt_frame, text="--%", bg=self.panel, fg=self.accent, font=("Consolas", 14, "bold"))
        self.batt_label.pack(anchor="center")
        
        # Steering wheel (Center Bottom)
        steer_frame = tk.Frame(mid, bg=self.panel); steer_frame.grid(row=0, column=1, sticky="nsew", padx=10, pady=5)
        self.steering_canvas = tk.Canvas(steer_frame, width=360, height=360, bg=self.panel, highlightthickness=0)
        self.steering_canvas.pack(expand=True, anchor="center")
        self._draw_steering()
        
        # Direction / Compass (Right Bottom)
        dir_frame = tk.Frame(mid, bg=self.panel); dir_frame.grid(row=0, column=2, sticky="nsew", padx=20, pady=10)
        tk.Label(dir_frame, text="DIRECCIÓN", bg=self.panel, fg=self.accent2, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.dir_canvas = tk.Canvas(dir_frame, width=160, height=160, bg=self.panel, highlightthickness=0)
        self.dir_canvas.pack(pady=6, anchor="center")
        self._draw_compass()
        self.dir_label = tk.Label(dir_frame, text="--", bg=self.panel, fg=self.accent, font=("Consolas", 14, "bold"))
        self.dir_label.pack(anchor="center")

        # ------------------- PANEL DERECHO: REGISTRO -------------------
        right = tk.Frame(container, bg=self.panel, bd=1, relief=tk.SOLID, highlightbackground=self._shade(self.panel, 15), highlightthickness=1)
        right.grid(row=0, column=2, sticky="nsew", padx=(15,0))
        tk.Label(right, text="[ REGISTRO DE EVENTOS ]", bg=self.panel, fg=self.accent2, font=self.font_title).pack(pady=10)
        
        self.log_text = tk.Text(right, bg="#041118", fg=self.accent, font=self.font_mono, height=30, width=45, bd=0)
        self.log_text.pack(padx=10, pady=5, fill=tk.BOTH, expand=True)
        self.log_text.config(state=tk.DISABLED)

        # ------------------- BARRA DE ESTADO -------------------
        self.status_var = tk.StringVar(value="ESTADO: INICIALIZANDO...")
        status = tk.Label(self.root, textvariable=self.status_var, anchor="w", bg=self.shadow, fg=self.text, font=("Segoe UI", 9))
        status.pack(side=tk.BOTTOM, fill=tk.X)

        # Bindings
        self.steering_canvas.bind("<Button-1>", self._steer_click)

    # --------------- drawing helpers ---------------
    def _draw_gauge_base(self):
        c = self.gauge_canvas
        c.delete("all")
        center_x, center_y = 190, 180
        radius = 150
        
        # Background Arc (0 to 180 degrees)
        c.create_arc(center_x - radius, center_y - radius, center_x + radius, center_y + radius,
                     start=0, extent=180, style=tk.ARC, outline=self.dim, width=16)
        
        # Ticks and Labels
        for i in range(0, 31, 5): # Speed 0 to 30
            angle_deg = (i / 30.0) * 180.0
            ang_rad = math.radians(180 - angle_deg) # 180=0 deg, 0=180 deg
            
            r_outer = radius - 8
            r_inner = radius - 18
            
            x1 = center_x + math.cos(ang_rad) * r_inner
            y1 = center_y - math.sin(ang_rad) * r_inner
            x2 = center_x + math.cos(ang_rad) * r_outer
            y2 = center_y - math.sin(ang_rad) * r_outer
            c.create_line(x1, y1, x2, y2, fill=self.text, width=2)
            
            # Labels
            x_text = center_x + math.cos(ang_rad) * (radius - 30)
            y_text = center_y - math.sin(ang_rad) * (radius - 30)
            c.create_text(x_text, y_text, text=str(i), fill=self.text, font=("Consolas", 8))
            
        # Pointer initial position (0 speed)
        self.gauge_pointer = c.create_line(center_x, center_y, center_x + radius - 18, center_y, fill=self.accent, width=6, capstyle=tk.ROUND, tags="pointer")
        c.create_oval(center_x - 8, center_y - 8, center_x + 8, center_y + 8, fill=self.accent, outline=self.bg, width=2)


    def _draw_steering(self):
        c = self.steering_canvas
        c.delete("all")
        cx, cy = 180, 180
        outer = 150
        inner = 50

        c.create_oval(cx - outer, cy - outer, cx + outer, cy + outer,
                      outline=self._shade(self.accent, -20), width=4)

        # Spoke Lines primero (o se pueden bajar luego)
        spoke_color = self.dim
        # Les damos la tag "spoke"
        c.create_line(cx + 40, cy, cx + outer - 25, cy, fill=spoke_color, width=3, tags="spoke")
        c.create_line(cx - 40, cy, cx - outer + 25, cy, fill=spoke_color, width=3, tags="spoke")
        c.create_line(cx, cy - 40, cx, cy - outer + 25, fill=spoke_color, width=3, tags="spoke")
        c.create_line(cx, cy + 40, cx, cy + outer - 25, fill=spoke_color, width=3, tags="spoke")

        self.segments = {}
        segs = {
            'UP': (75, 30, "DERECHA>>"),
            'RIGHT': (345, 30, "DESACELERAR"),
            'DOWN': (255, 30, "<<IZQUIERDA"),
            'LEFT': (165, 30, "ACELERAR")
        }

        up_down_color = self.accent2
        left_right_color = self.accent

        for key, (start, extent, label) in segs.items():
            color = up_down_color if key in ['UP', 'DOWN'] else left_right_color
            aid = c.create_arc(cx - outer + 10, cy - outer + 10,
                               cx + outer - 10, cy + outer - 10,
                               start=start, extent=extent, style=tk.ARC,
                               outline=color, width=18, tags=("seg", key))
            self.segments[key] = aid

            text_rad = outer - 45
            tx = cx + math.cos(math.radians(start + extent/2 - 90)) * text_rad
            ty = cy - math.sin(math.radians(start + extent/2 - 90)) * text_rad
            c.create_text(tx, ty, text=label, fill=self.text,
                          font=("Consolas", 12, "bold"), tags="seglabel")

        # Asegura que los textos estén encima y las líneas debajo:
        c.tag_lower("spoke")          # baja todas las líneas
        c.tag_raise("seglabel")  
        # Center disk
        c.create_oval(cx - inner, cy - inner, cx + inner, cy + inner, fill=self.panel, outline=self.accent2, width=3)
        c.create_text(cx, cy, text="PILOT", fill=self.accent, font=("Consolas", 14, "bold"))
        
        # Spoke Lines
        


    def _draw_compass(self):
        c = self.dir_canvas
        c.delete("all")
        cx, cy = 80, 80
        r = 60
        
        # Compass Ring
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=self.accent2, width=3)
        
        # Cardinal Points
        for angle, label in [(0,"N"), (90,"E"), (180,"S"), (270,"W")]:
            a = math.radians(-angle + 90) # Adjust for canvas orientation
            x = cx + math.cos(a) * (r - 10)
            y = cy - math.sin(a) * (r - 10)
            color = self.warning if label == "N" else self.text # North is always critical
            c.create_text(x, y, text=label, fill=color, font=("Consolas", 10, "bold"))
            
        # Needle (Redraw position on update)
        self.needle = c.create_line(cx, cy, cx, cy - r + 16, fill=self.accent, width=4, capstyle=tk.ROUND, tags="needle")
        c.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=self.dim, outline="")


    # ---------------- UI actions ----------------
    def _steer_click(self, event):
        cx, cy = 180, 180
        # Convert coordinates to polar (0 is East, increases counter-clockwise)
        dx = event.x - cx
        dy = cy - event.y # Invert Y for standard math angle
        
        # Calculate angle (0-360 deg, 0=Right)
        angle = (math.degrees(math.atan2(dy, dx)) + 360) % 360

        cmd = None
        seg = None
        
        # Check against the corrected segments from draw_steering
        # UP (75-105 deg)
        if 75 <= angle <= 105:
            cmd = "CMD SPEED UP"; seg = 'UP'
        # RIGHT (345-15 deg)
        elif (345 <= angle < 360) or (0 <= angle <= 15):
            cmd = "CMD TURN RIGHT"; seg = 'RIGHT'
        # DOWN (255-285 deg)
        elif 255 <= angle <= 285:
            cmd = "CMD SLOW DOWN"; seg = 'DOWN'
        # LEFT (165-195 deg)
        elif 165 <= angle <= 195:
            cmd = "CMD TURN LEFT"; seg = 'LEFT'

        if cmd:
            self._animate_segment(seg)
            self._send_cmd_text(cmd)

    def _animate_segment(self, seg_key):
        c = self.steering_canvas
        aid = self.segments.get(seg_key)
        if not aid: return
        
        # Flash white then restore original color
        original_color = c.itemcget(aid, "outline")
        c.itemconfig(aid, outline=self.text) 
        self.root.after(180, lambda: c.itemconfig(aid, outline=original_color))
        
    def _rotate_needle(self, direction):
        # Maps cardinal points to angle degrees (0=N, 90=E, 180=S, 270=W)
        mapdir = {"N":0, "E":90, "S":180, "W":270}
        
        # Try to find the closest main direction or use current heading
        target_deg = self.current_heading_deg 
        for k, v in mapdir.items():
            if k in direction.upper():
                target_deg = v
                break
        
        # Animate simple rotation to target_deg
        cx, cy = 80, 80
        r = 44 # Needle length
        
        # Convert bearing (0=N, 90=E) to canvas angle (90=N, 0=E)
        # Canvas angle is 90 - Bearing
        ang = math.radians(90 - target_deg)
        
        x2 = cx + math.cos(ang) * r
        y2 = cy - math.sin(ang) * r
        
        self.dir_canvas.coords(self.needle, cx, cy, x2, y2)


    # ---------------- handle server messages ----------------
    def _handle_line(self, line):
        if line.startswith("TELEMETRY"):
            parts = line.split()
            kv = {}
            for p in parts[1:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    kv[k] = v
            
            v = kv.get("v")
            b = kv.get("battery")
            d = kv.get("dir")
            
            if v is not None:
                try: self.speed_val = float(v)
                except: self.speed_val = 0.0
            
            if b is not None:
                try: self.battery = int(float(b))
                except: self.battery = 0
                self._update_battery_canvas()
            
            if d is not None:
                # Update text label
                self.direction = d.upper()
                self.dir_label.config(text=self.direction)
                
                # Simple parsing for heading/degree if available (e.g., dir=N(15.2))
                try:
                    if '(' in d and ')' in d:
                        heading_str = d.split('(')[1].split(')')[0]
                        self.current_heading_deg = float(heading_str)
                    else:
                        # Fallback: estimate degree from cardinal direction if no number is present
                        if 'N' in self.direction: self.current_heading_deg = 0
                        elif 'E' in self.direction: self.current_heading_deg = 90
                        elif 'S' in self.direction: self.current_heading_deg = 180
                        elif 'W' in self.direction: self.current_heading_deg = 270
                        
                except Exception:
                    self.current_heading_deg = 0.0
                    
                self._rotate_needle(self.direction)
                
    # ---------------- polling and animation ----------------
    def _poll(self):
        # Network Polling
        msg = self.client.get_message_nowait()
        while msg:
            kind, payload = msg
            if kind == "line":
                self._log(payload)
                self._handle_line(payload)
            elif kind == "status":
                self._log(payload, error=True)
                self.status_var.set(f"ESTADO: {payload}")
            msg = self.client.get_message_nowait()
            
        # UI Animation Update
        self._update_gauge()
        
        self.root.after(120, self._poll)


    # (Rest of helper methods: toggle_connect, _disconnect, _do_auth, _send_cmd_text, _log, _update_battery_canvas, _update_gauge, _shade)
    # ... (Keep the rest of the original methods as they are mostly functional) ...
    # Simplified versions of original methods below for conciseness:

    def toggle_connect(self):
        if self.connected:
            self._disconnect()
        else:
            host = self.ent_host.get().strip(); port = self.ent_port.get().strip()
            if not host or not port.isdigit(): messagebox.showerror("Conexión", "Host o puerto inválido"); return
            self._log(f"Conectando a {host}:{port}...")
            try:
                self.client.connect(host, int(port))
            except Exception as e:
                self._log(f"Error conexión: {e}", error=True); messagebox.showerror("Conexión", f"No se pudo conectar: {e}"); return
            self.connected = True
            self.status_var.set("ESTADO: CONECTADO"); self.btn_connect.config(text="DESCONECTAR")
            self._send_cmd_text("SUBSCRIBE OBSERVER"); self._log("Suscrito como OBSERVER")

    def _disconnect(self):
        self.client.close(); self.connected = False
        self.status_var.set("ESTADO: DESCONECTADO"); self.btn_connect.config(text="CONECTAR")
        self._log("Desconectado")

    def _do_auth(self):
        user = self.ent_user.get().strip(); pw = self.ent_pass.get().strip()
        if not user or not pw: messagebox.showinfo("AUTH", "Introduce usuario y contraseña"); return
        try: self._send_cmd_text(f"AUTH {user} {pw}")
        except Exception as e: self._log(f"Error AUTH: {e}", error=True)

    def _send_cmd_text(self, txt):
        try:
            self.client.send_line(txt); self._log(f"> {txt}", send=True)
        except Exception as e:
            self._log(f"Fallo enviar: {e}", error=True)

    def _log(self, text, send=False, error=False):
        self.log_text.config(state=tk.NORMAL)
        prefix = "CMD " if send else "[SYS]"
        color = self.accent if not error else self.warning
        self.log_text.insert("end", f"{prefix} {text}\n")
        self.log_text.see("end")
        self.log_text.config(state=tk.DISABLED)

    def _update_battery_canvas(self):
        level = max(0, min(100, self.battery))
        top = 12 + (1 - level/100.0) * (160 - 12)
        self.batt_canvas.coords(self.batt_fill, 22, top, 48, 158)
        if level < 20: color = self.warning
        elif level < 50: color = self.accent2
        else: color = self.accent
        self.batt_canvas.itemconfig(self.batt_fill, fill=color)
        self.batt_label.config(text=f"{level}%", fg=color)

    def _update_gauge(self):
        target = max(0.0, min(30.0, self.speed_val))
        target_ang = (target / 30.0) * 180.0
        coords = self.gauge_canvas.coords(self.gauge_pointer)
        cx, cy = 190, 180; r = 120
        if coords and len(coords) >= 4:
            x2, y2 = coords[2], coords[3]
            dx, dy = x2 - cx, cy - y2
            cur_ang = math.degrees(math.atan2(dy, dx))
            cur_ang = max(0.0, min(180.0, cur_ang))
        else: cur_ang = 0.0
        
        new_ang = cur_ang + (target_ang - cur_ang) * 0.15
        
        # Map 0-180 (left-to-right) to 180-0 (math angle)
        ang_rad = math.radians(180 - new_ang)
        x2 = cx + math.cos(ang_rad) * (r - 18)
        y2 = cy - math.sin(ang_rad) * (r - 18)
        self.gauge_canvas.coords(self.gauge_pointer, cx, cy, x2, y2)
        self.lbl_speed_big.config(text=str(int(self.speed_val)))

    def _shade(self, hexcol, delta):
        hexcol = hexcol.lstrip('#')
        r = int(hexcol[0:2], 16); g = int(hexcol[2:4], 16); b = int(hexcol[4:6], 16)
        r = min(255, max(0, r + delta)); g = min(255, max(0, g + delta)); b = min(255, max(0, b + delta))
        return f"#{r:02x}{g:02x}{b:02x}"

if __name__ == "__main__":
    root = tk.Tk()
    app = FancyClientApp(root)
    root.mainloop()
