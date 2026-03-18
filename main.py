"""
SecureChat v3.0 — Client
========================
Architettura client-server. Il server relay gira sul Raspberry Pi.
I messaggi sono cifrati E2E: il server non può leggerli.

Autore:  Mezzina Pasquale Massimo
Sito:    massimoesperto.it
GitHub:  github.com/massiprofessor
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox, simpledialog
import json, os, random, time, hashlib, platform, subprocess, logging, threading
from datetime import datetime
from client_core import ChatClient

# ── Configurazione ────────────────────────────────────────────
APP_VERSION  = "3.2.1"
APP_AUTHOR   = "Mezzina Pasquale Massimo"
APP_WEBSITE  = "massimoesperto.it"
APP_GITHUB   = "github.com/massiprofessor"
DEFAULT_HOST = ""  # Lascia vuoto: ogni utente inserisce il proprio server
DEFAULT_PORT = 7300
DEFAULT_ROOM = "Generale"
TYPING_TIMEOUT = 3.0

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    try:
        with open(CONFIG_FILE) as f: return json.load(f)
    except: return {}

def save_config(data: dict):
    try:
        cfg = load_config(); cfg.update(data)
        with open(CONFIG_FILE, "w") as f: json.dump(cfg, f, indent=2)
    except: pass

def play_trill_sound():
    """Riproduce suono trill in background. Funziona anche nell'exe PyInstaller."""
    def _play():
        try:
            if platform.system() == "Windows":
                # Metodo 1: winsound (disponibile in Python standard)
                try:
                    import winsound
                    for freq, dur in [(880, 80), (1100, 80), (880, 80), (1100, 120)]:
                        winsound.Beep(freq, dur)
                    return
                except Exception:
                    pass
                # Metodo 2: ctypes → Beep() di kernel32 (funziona sempre su Windows, anche nell'exe)
                try:
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    for freq, dur in [(880, 80), (1100, 80), (880, 80), (1100, 120)]:
                        kernel32.Beep(freq, dur)
                    return
                except Exception:
                    pass
                # Metodo 3: MessageBeep come ultimo fallback
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBeep(0xFFFFFFFF)
                except Exception:
                    pass
            elif platform.system() == "Darwin":
                os.system("afplay /System/Library/Sounds/Ping.aiff 2>/dev/null")
            else:
                os.system("paplay /usr/share/sounds/freedesktop/stereo/message.oga 2>/dev/null || "
                          "aplay /usr/share/sounds/alsa/Front_Center.wav 2>/dev/null")
        except Exception:
            pass
    t = threading.Thread(target=_play)
    t.daemon = False
    t.start()

def play_mention_sound():
    """Suono leggero per @menzione."""
    def _play():
        try:
            if platform.system() == "Windows":
                try:
                    import winsound
                    winsound.Beep(1200, 120)
                    return
                except Exception: pass
                try:
                    import ctypes
                    ctypes.windll.kernel32.Beep(1200, 120)
                except Exception: pass
        except Exception: pass
    t = threading.Thread(target=_play)
    t.daemon = False
    t.start()



# ── Taskbar Badge (Windows) ───────────────────────────────────
class TaskbarBadge:
    """Mostra un numero sull'icona nella taskbar di Windows."""
    _hwnd = None

    @classmethod
    def _get_hwnd(cls, root):
        if cls._hwnd is None:
            try:
                import ctypes
                cls._hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
                if cls._hwnd == 0:
                    cls._hwnd = root.winfo_id()
            except:
                cls._hwnd = None
        return cls._hwnd

    @classmethod
    def set_count(cls, root, count: int):
        """Aggiorna il badge sul titolo della finestra con il contatore."""
        try:
            if count > 0:
                root.title(f"({count}) SecureChat")
            else:
                root.title("SecureChat")
        except:
            pass
        # Prova anche overlay icona Windows via COM
        cls._try_overlay(root, count)

    @classmethod
    def _try_overlay(cls, root, count: int):
        try:
            import ctypes, ctypes.wintypes
            if platform.system() != "Windows": return
            hwnd = cls._get_hwnd(root)
            if not hwnd: return
            # Crea bitmap 16x16 con numero
            hdc_screen = ctypes.windll.user32.GetDC(0)
            hdc = ctypes.windll.gdi32.CreateCompatibleDC(hdc_screen)
            bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc_screen, 16, 16)
            ctypes.windll.gdi32.SelectObject(hdc, bmp)
            # Sfondo rosso
            brush = ctypes.windll.gdi32.CreateSolidBrush(0x004040F0 if count > 0 else 0x00303030)
            rect = ctypes.wintypes.RECT(0, 0, 16, 16)
            ctypes.windll.user32.FillRect(hdc, ctypes.byref(rect), brush)
            ctypes.windll.gdi32.DeleteObject(brush)
            # Testo numero
            if count > 0:
                label = str(min(count, 99))
                ctypes.windll.gdi32.SetTextColor(hdc, 0x00FFFFFF)
                ctypes.windll.gdi32.SetBkMode(hdc, 1)
                ctypes.windll.user32.DrawTextW(hdc, label, -1, ctypes.byref(rect),
                    0x0001 | 0x0004 | 0x0025)
            hicon = ctypes.windll.user32.CreateIconIndirect
        except:
            pass


# ── Reactions ─────────────────────────────────────────────────
REACTION_EMOJIS = ["👍","❤","😂","😮","😢","🔥","👏","🎉","😎","💯"]

class ReactionBar(tk.Toplevel):
    """Popup emoji per react a un messaggio."""
    def __init__(self, master, x, y, on_pick):
        super().__init__(master)
        self.overrideredirect(True)
        self.configure(bg="#1E2430")
        self.attributes("-topmost", True)
        self.on_pick = on_pick
        f = tk.Frame(self, bg="#1E2430", padx=4, pady=4)
        f.pack()
        for i, e in enumerate(REACTION_EMOJIS):
            btn = tk.Label(f, text=e, font=("Segoe UI Emoji", 18),
                           bg="#1E2430", cursor="hand2", padx=3, pady=2)
            btn.grid(row=0, column=i)
            btn.bind("<Button-1>", lambda ev, em=e: self._pick(em))
            btn.bind("<Enter>",    lambda ev, b=btn: b.configure(bg="#2A3A50"))
            btn.bind("<Leave>",    lambda ev, b=btn: b.configure(bg="#1E2430"))
        # Bordo
        self.configure(highlightbackground="#39D0D8", highlightthickness=1)
        # Posiziona sopra il punto del click
        self.geometry(f"+{x}+{y - 52}")
        self.focus_set()
        self.bind("<FocusOut>", lambda _: self.destroy())

    def _pick(self, emoji):
        self.on_pick(emoji)
        self.destroy()

# ── Emoji Picker ──────────────────────────────────────────────
EMOJI_CATEGORIES = {
    "Faccine 😀": ["😀","😁","😂","🤣","😃","😄","😅","😆","😉","😊","😋","😎","😍","🥰","😘",
                   "😗","😙","😚","🙂","🤗","🤔","🤭","🤫","🤥","😶","😐","😑","😬","🙄",
                   "😯","😦","😧","😮","😲","😴","🤤","😪","😵","🤐","🥴","🤢","🤮","🤧",
                   "😷","🤒","🤕","🥵","🥶","🥳","🤩","😠","😡","🤬","😤","😢","😭","😱"],
    "Gesti 👋":   ["👋","🤚","🖐","✋","🖖","👌","🤌","🤏","✌","🤞","🤟","🤘","🤙","👈","👉",
                   "👆","🖕","👇","☝","👍","👎","✊","👊","🤛","🤜","👏","🙌","🤲","🙏","✍"],
    "Cuori ❤":    ["❤","🧡","💛","💚","💙","💜","🖤","🤍","🤎","💔","❣","💕","💞","💓","💗",
                   "💖","💘","💝","💟","☮","✝","☪","🕉","☸","✡","🔯","🕎","☯","☦","🛐"],
    "Oggetti 📦": ["📱","💻","🖥","🖨","⌨","🖱","🖲","💾","💿","📀","📷","📸","📹","🎥","📽",
                   "🎞","📞","☎","📟","📠","📺","📻","🧭","⏱","⏲","⏰","🕰","⌚","📡","🔋"],
    "Natura 🌿":  ["🌵","🎄","🌲","🌳","🌴","🌱","🌿","☘","🍀","🎍","🎋","🍃","🍂","🍁","🍄",
                   "🐚","🌾","💐","🌷","🌹","🥀","🌺","🌸","🌼","🌻","🌞","🌝","🌛","🌜","🌚"],
    "Cibo 🍕":    ["🍎","🍐","🍊","🍋","🍌","🍉","🍇","🍓","🫐","🍈","🍒","🍑","🥭","🍍","🥥",
                   "🥝","🍅","🫒","🥑","🍆","🥔","🥕","🌽","🌶","🫑","🥦","🥬","🥒","🧄","🧅"],
    "Sport ⚽":   ["⚽","🏀","🏈","⚾","🥎","🎾","🏐","🏉","🥏","🎱","🪀","🏓","🏸","🏒","🥊",
                   "🥋","🥅","⛳","🏹","🎣","🤿","🎽","🎿","🛷","🥌","🎯","🪃","🏋","🤼","🤸"],
}

class EmojiPicker(ctk.CTkToplevel):
    def __init__(self, master, on_select, **kwargs):
        super().__init__(master, **kwargs)
        self.on_select = on_select
        self.title("Emoji")
        self.geometry("420x360")
        self.configure(fg_color=BG_PANEL)
        self.resizable(False, False)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.after(50, self._bring_front)

    def _bring_front(self):
        self.lift()
        self.focus_force()
        self.attributes("-topmost", True)
        self.after(100, lambda: self.attributes("-topmost", False))

        # Tab categoria
        tab_f = ctk.CTkFrame(self, fg_color=BG_DARK, corner_radius=0, height=36)
        tab_f.grid(row=0, column=0, sticky="ew")
        self._cat_var = tk.StringVar(value=list(EMOJI_CATEGORIES.keys())[0])
        for cat in EMOJI_CATEGORIES:
            # Mostra solo l'emoji del titolo come tab
            short = cat.split()[-1]
            ctk.CTkButton(tab_f, text=short, width=44, height=30,
                          fg_color="transparent", hover_color=BG_CARD,
                          font=ctk.CTkFont(size=16),
                          command=lambda c=cat: self._load_cat(c)).pack(side="left", padx=2, pady=3)

        self._grid_frame = ctk.CTkScrollableFrame(self, fg_color=BG_DARK, corner_radius=0)
        self._grid_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        self._load_cat(list(EMOJI_CATEGORIES.keys())[0])

    def _load_cat(self, cat: str):
        for w in self._grid_frame.winfo_children(): w.destroy()
        emojis = EMOJI_CATEGORIES[cat]
        cols = 10
        for i, e in enumerate(emojis):
            ctk.CTkButton(self._grid_frame, text=e, width=34, height=34,
                          fg_color="transparent", hover_color=BG_CARD,
                          font=ctk.CTkFont(size=18),
                          command=lambda em=e: self._pick(em)).grid(
                row=i // cols, column=i % cols, padx=1, pady=1)

    def _pick(self, emoji: str):
        self.on_select(emoji)
        self.destroy()



# ── Tooltip ───────────────────────────────────────────────────
class Tooltip:
    """Mostra un tooltip quando il cursore passa su un widget."""
    def __init__(self, widget, text: str, delay: int = 500):
        self.widget  = widget
        self.text    = text
        self.delay   = delay
        self._job    = None
        self._win    = None
        widget.bind("<Enter>",    self._schedule, add="+")
        widget.bind("<Leave>",    self._cancel,   add="+")
        widget.bind("<Button>",   self._cancel,   add="+")
        widget.bind("<Destroy>",  self._cancel,   add="+")

    def _schedule(self, _=None):
        self._cancel()
        self._job = self.widget.after(self.delay, self._show)

    def _cancel(self, _=None):
        if self._job:
            self.widget.after_cancel(self._job)
            self._job = None
        if self._win:
            try: self._win.destroy()
            except: pass
            self._win = None

    def _show(self):
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._win = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)
        bg = "#1E2430"
        outer = tk.Frame(tw, bg="#39D0D8", bd=1)
        outer.pack()
        tk.Label(outer, text=self.text,
                 font=("Consolas", 10),
                 bg=bg, fg="#E6EDF3",
                 padx=10, pady=5,
                 wraplength=260,
                 justify="left").pack()


def tip(widget, text: str):
    """Shorthand: aggiunge tooltip a un widget."""
    Tooltip(widget, text)

# ── Palette colori ────────────────────────────────────────────
BG_DARK       = "#0D1117"
BG_PANEL      = "#161B22"
BG_CARD       = "#21262D"
BG_INPUT      = "#1C2128"
ACCENT_CYAN   = "#39D0D8"
ACCENT_GREEN  = "#3FB950"
ACCENT_AMBER  = "#F0A94A"
ACCENT_RED    = "#F85149"
ACCENT_PURPLE = "#BC8CFF"
TEXT_PRIMARY  = "#E6EDF3"
TEXT_MUTED    = "#7D8590"
TEXT_DIM      = "#484F58"
BORDER_COLOR  = "#30363D"


# ── Chat Bubble ───────────────────────────────────────────────
class ChatBubble(ctk.CTkFrame):
    def __init__(self, master, nick, text, ts, is_me, is_private=False, mention=False,
                 reactions=None, on_react=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self._reactions = reactions or {}   # {emoji: count}
        self._on_react  = on_react          # callback(emoji)
        if mention:
            color = "#2A2A1A"
        elif is_me:
            color = "#1A3A4A"
        elif is_private:
            color = "#2D1A4A"
        else:
            color = BG_CARD
        nick_col = ACCENT_CYAN if is_me else (ACCENT_PURPLE if is_private else ACCENT_AMBER)
        align    = "e" if is_me else "w"
        padx     = (60, 8) if is_me else (8, 60)
        self._c = c = ctk.CTkFrame(self, fg_color=color, corner_radius=12,
                         border_width=1 if mention else 0,
                         border_color=ACCENT_AMBER if mention else color)
        c.grid(row=0, column=0, sticky=align, padx=padx, pady=2)
        prefix = "🔒 " if is_private else ("📣 " if mention else "")
        ctk.CTkLabel(c, text=prefix + nick,
                     font=ctk.CTkFont("Consolas", 12, "bold"),
                     text_color=nick_col).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 0))
        ctk.CTkLabel(c, text=text, wraplength=360,
                     font=ctk.CTkFont("Consolas", 14),
                     text_color=TEXT_PRIMARY, justify="left").grid(
                     row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(2, 2))
        # Timestamp + hint click destro
        hint = tk.Frame(c, bg=color if isinstance(color, str) else BG_CARD)
        hint.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 5))
        tk.Label(hint, text=ts, font=("Consolas", 9),
                 fg=TEXT_DIM, bg=color).pack(side="right", padx=4)
        tk.Label(hint, text="  ☰", font=("Consolas", 9),
                 fg=TEXT_DIM, bg=color, cursor="hand2").pack(side="right", padx=2)
        # Reactions bar (se presenti)
        self._react_frame = tk.Frame(c, bg=color)
        self._react_frame.grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 6))
        self._render_reactions(color)
        # Click destro o click su ☰ → reaction bar
        for w in [self, c]:
            w.bind("<Button-3>", self._open_reaction_bar)
        hint.winfo_children()[-1].bind("<Button-1>", self._open_reaction_bar)

    def _render_reactions(self, bg_color):
        for w in self._react_frame.winfo_children(): w.destroy()
        for emoji, count in self._reactions.items():
            if count <= 0: continue
            lbl = tk.Label(self._react_frame,
                           text=f"{emoji} {count}",
                           font=("Segoe UI Emoji", 11),
                           fg=TEXT_PRIMARY, bg="#2A3A50",
                           padx=6, pady=2, cursor="hand2",
                           relief="flat")
            lbl.pack(side="left", padx=2)
            lbl.bind("<Button-1>", lambda ev, e=emoji: self._react(e))

    def _open_reaction_bar(self, event):
        if self._on_react is None: return
        ReactionBar(self, event.x_root, event.y_root, self._react)

    def _react(self, emoji: str):
        self._reactions[emoji] = self._reactions.get(emoji, 0) + 1
        # Recupera colore sfondo
        try:
            bg = self._c.cget("fg_color")
            if isinstance(bg, (list, tuple)): bg = bg[1]
        except:
            bg = BG_CARD
        self._render_reactions(bg)
        if self._on_react:
            self._on_react(emoji)


# ── Finestra chat privata ─────────────────────────────────────
class PrivateChatWindow(ctk.CTkToplevel):
    def __init__(self, master, nick, peer_id, client, my_nick):
        super().__init__(master)
        self.title(f"Chat Privata — {nick}")
        self.geometry("520x480")
        self.configure(fg_color=BG_DARK)
        self.after(50, lambda: (self.lift(), self.focus_force(),
                                self.attributes("-topmost", True),
                                self.after(150, lambda: self.attributes("-topmost", False))))
        self.nick = nick; self.peer_id = peer_id
        self.client = client; self.my_nick = my_nick
        self.grid_rowconfigure(1, weight=1); self.grid_columnconfigure(0, weight=1)
        hdr = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=0, height=44)
        hdr.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(hdr, text=f"🔒 Chat privata con {nick}",
                     font=ctk.CTkFont("Consolas", 14, "bold"),
                     text_color=ACCENT_PURPLE).pack(side="left", padx=14)
        self.scroll = ctk.CTkScrollableFrame(self, fg_color=BG_DARK, corner_radius=0)
        self.scroll.grid(row=1, column=0, sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)
        inp = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=0, height=56)
        inp.grid(row=2, column=0, sticky="ew"); inp.grid_columnconfigure(0, weight=1)
        self.var = tk.StringVar()
        e = ctk.CTkEntry(inp, textvariable=self.var,
                         placeholder_text=f"Messaggio a {nick}...",
                         font=ctk.CTkFont("Consolas", 14), fg_color=BG_INPUT,
                         border_color=ACCENT_PURPLE, height=38)
        e.grid(row=0, column=0, sticky="ew", padx=(10, 6), pady=9)
        e.bind("<Return>", lambda _: self._send())
        ctk.CTkButton(inp, text="➤", width=44, height=38,
                      fg_color=ACCENT_PURPLE, hover_color="#9B6EE0",
                      command=self._send).grid(row=0, column=1, padx=(0, 10), pady=9)

    def _send(self):
        text = self.var.get().strip()
        if not text: return
        self.client.send_private(self.peer_id, text)
        self._bubble(self.my_nick, text, True); self.var.set("")

    def add_incoming(self, nick, text): self._bubble(nick, text, False)

    def _bubble(self, nick, text, is_me):
        row = len(self.scroll.winfo_children())
        ChatBubble(self.scroll, nick, text,
                   datetime.now().strftime("%H:%M"), is_me, True).grid(
            row=row, column=0, sticky="ew", padx=8, pady=2)
        self.after(50, lambda: self.scroll._parent_canvas.yview_moveto(1.0))


# ── App principale ────────────────────────────────────────────
class SecureChatApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title("SecureChat")
        self.geometry("1200x740"); self.minsize(900, 580)
        self.configure(fg_color=BG_DARK)
        self.client: ChatClient | None = None
        self.my_nick    = ""
        self.active_room = DEFAULT_ROOM
        self.room_buttons:   dict[str, ctk.CTkButton]        = {}
        self.private_wins:   dict[str, PrivateChatWindow]    = {}
        self.typing_timers:  dict[str, str]                  = {}
        self._last_typing_sent = 0
        self._typing_sched     = None
        self._room_history:    dict[str, list]               = {}
        self._unread:          dict[str, bool]               = {}
        self._unread_count:   int                            = 0
        self._bubble_widgets: dict[str, "ChatBubble"]          = {}  # ts_key → widget
        self._connect_status_active = False
        self._show_setup()

    # ── Setup ─────────────────────────────────────────────────
    def _show_setup(self):
        self._clear()
        canvas = tk.Canvas(self, bg=BG_DARK, highlightthickness=0)
        canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        w = self.winfo_width() or 1200; h = self.winfo_height() or 740
        for x in range(0, w, 40):
            for y in range(0, h, 40):
                canvas.create_oval(x-1, y-1, x+1, y+1, fill=TEXT_DIM, outline="")

        card = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=20,
                            border_width=1, border_color=BORDER_COLOR,
                            width=500)
        card.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(card, text="◈ SecureChat",
                     font=ctk.CTkFont("Consolas", 27, "bold"),
                     text_color=ACCENT_CYAN).pack(pady=(28, 4))
        ctk.CTkLabel(card, text=f"v{APP_VERSION}  •  Chat cifrata E2E",
                     font=ctk.CTkFont("Consolas", 12), text_color=TEXT_MUTED).pack(pady=(0, 20))

        form = ctk.CTkFrame(card, fg_color="transparent")
        form.pack(fill="x", padx=44)

        def lbl(t):
            ctk.CTkLabel(form, text=t, font=ctk.CTkFont("Consolas", 12, "bold"),
                         text_color=TEXT_MUTED, anchor="w").pack(fill="x", pady=(10, 2))

        cfg = load_config()
        lbl("NICKNAME")
        self.nick_var = tk.StringVar(value=cfg.get("nickname", "Utente" + str(random.randint(100, 999))))
        ctk.CTkEntry(form, textvariable=self.nick_var, height=38,
                     font=ctk.CTkFont("Consolas", 14),
                     fg_color=BG_INPUT, border_color=BORDER_COLOR).pack(fill="x")

        lbl("SERVER")
        self.host_var = tk.StringVar(value=cfg.get("server_host", DEFAULT_HOST))
        ctk.CTkEntry(form, textvariable=self.host_var, height=38,
                     font=ctk.CTkFont("Consolas", 14),
                     fg_color=BG_INPUT, border_color=BORDER_COLOR,
                     placeholder_text="es. massimorasp.webhop.me").pack(fill="x")

        lbl("PORTA")
        self.port_var = tk.StringVar(value=str(cfg.get("server_port", DEFAULT_PORT)))
        ctk.CTkEntry(form, textvariable=self.port_var, height=38,
                     font=ctk.CTkFont("Consolas", 14),
                     fg_color=BG_INPUT, border_color=BORDER_COLOR).pack(fill="x")

        btns = ctk.CTkFrame(form, fg_color="transparent")
        btns.pack(fill="x", pady=(20, 0))
        btns.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(btns, text="▶  ENTRA",
                      font=ctk.CTkFont("Consolas", 15, "bold"), height=44,
                      fg_color=ACCENT_CYAN, hover_color="#2BBBC3",
                      text_color=BG_DARK, corner_radius=10,
                      command=self._launch).grid(row=0, column=0, padx=(0, 6), sticky="ew")
        ctk.CTkButton(btns, text="?  Tutorial",
                      font=ctk.CTkFont("Consolas", 14), height=44,
                      fg_color=BG_CARD, hover_color=BORDER_COLOR,
                      text_color=ACCENT_AMBER, corner_radius=10,
                      command=self._show_tutorial).grid(row=0, column=1, padx=(6, 0), sticky="ew")

        ctk.CTkLabel(card, text=f"© {APP_AUTHOR}  •  {APP_WEBSITE}",
                     font=ctk.CTkFont("Consolas", 11), text_color=TEXT_DIM).pack(pady=(16, 8))

    def _launch(self):
        nick = self.nick_var.get().strip()
        if not nick: messagebox.showwarning("Errore", "Inserisci un nickname!"); return
        host = self.host_var.get().strip()
        if not host: messagebox.showwarning("Errore", "Inserisci l'indirizzo del server!"); return
        try:
            port = int(self.port_var.get().strip()); assert 1 <= port <= 65535
        except:
            messagebox.showwarning("Errore", "Porta non valida!"); return
        save_config({"nickname": nick, "server_host": host, "server_port": port})
        self.my_nick = nick
        self.client = ChatClient(nick, host, port, self._on_event)
        # Mostra schermata di caricamento
        self._show_connecting(host, port)
        # Connetti in background
        import threading
        threading.Thread(target=self._do_connect, daemon=True).start()

    def _show_connecting(self, host, port):
        self._clear()
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(f, text="◈ Connessione in corso...",
                     font=ctk.CTkFont("Consolas", 19, "bold"),
                     text_color=ACCENT_CYAN).pack(pady=(0, 8))
        ctk.CTkLabel(f, text=f"{host}:{port}",
                     font=ctk.CTkFont("Consolas", 13), text_color=TEXT_MUTED).pack()
        self._conn_status_lbl = ctk.CTkLabel(f, text="",
                     font=ctk.CTkFont("Consolas", 12), text_color=TEXT_DIM)
        self._conn_status_lbl.pack(pady=(12, 0))

    def _do_connect(self):
        try:
            self.client.connect(timeout=10.0)
            self.after(0, self._show_main)
        except Exception as e:
            msg = str(e)
            self.after(0, lambda m=msg: self._on_connect_error(m))

    def _on_connect_error(self, msg):
        messagebox.showerror("Connessione fallita",
            f"Impossibile connettersi al server:\n{msg}\n\n"
            f"Verifica che il server sia in esecuzione sul Raspberry Pi.")
        self._show_setup()

    # ── Main screen ───────────────────────────────────────────
    def _show_main(self):
        self._clear()
        self.grid_rowconfigure(0, weight=1); self.grid_columnconfigure(0, weight=1)

        root = ctk.CTkFrame(self, fg_color="transparent")
        root.grid(row=0, column=0, sticky="nsew")
        root.grid_rowconfigure(0, weight=1)
        root.grid_columnconfigure(0, minsize=200, weight=0)
        root.grid_columnconfigure(1, weight=1)
        root.grid_columnconfigure(2, minsize=200, weight=0)

        # ── Sidebar sinistra ──────────────────────────────────
        rooms_col = ctk.CTkFrame(root, fg_color="#0A0D12", corner_radius=0, width=210, border_width=1, border_color="#1A2030")
        rooms_col.grid(row=0, column=0, sticky="nsew"); rooms_col.grid_propagate(False)
        rooms_col.grid_rowconfigure(2, weight=1); rooms_col.grid_columnconfigure(0, weight=1)

        logo = ctk.CTkFrame(rooms_col, fg_color="#070A0E", corner_radius=0, height=56)
        logo.grid(row=0, column=0, sticky="ew"); logo.grid_propagate(False)
        ctk.CTkLabel(logo, text="◈ SecureChat",
                     font=ctk.CTkFont("Consolas", 15, "bold"),
                     text_color=ACCENT_CYAN).pack(side="left", padx=12, pady=14)

        rh = ctk.CTkFrame(rooms_col, fg_color="transparent", height=32)
        rh.grid(row=1, column=0, sticky="ew", padx=8, pady=(10, 2))
        rh.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(rh, text="STANZE", font=ctk.CTkFont("Consolas", 11, "bold"),
                     text_color=TEXT_DIM).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(rh, text="+", width=24, height=24,
                      font=ctk.CTkFont("Consolas", 15, "bold"),
                      fg_color="transparent", hover_color=BG_CARD, text_color=TEXT_MUTED,
                      command=self._create_room_dialog).grid(row=0, column=1)

        self.rooms_scroll = ctk.CTkScrollableFrame(rooms_col, fg_color="transparent",
                                                    corner_radius=0)
        self.rooms_scroll.grid(row=2, column=0, sticky="nsew", padx=4)
        self.rooms_scroll.grid_columnconfigure(0, weight=1)

        # Status server
        srv_f = ctk.CTkFrame(rooms_col, fg_color="transparent")
        srv_f.grid(row=3, column=0, sticky="ew", padx=8, pady=6)
        ctk.CTkLabel(srv_f, text="SERVER", font=ctk.CTkFont("Consolas", 10, "bold"),
                     text_color=TEXT_DIM).pack(anchor="w")
        self.srv_lbl = ctk.CTkLabel(srv_f, text="✓ Connesso",
                                     font=ctk.CTkFont("Consolas", 10),
                                     text_color=ACCENT_GREEN)
        self.srv_lbl.pack(anchor="w")
        ctk.CTkLabel(srv_f,
                     text=f"{self.client.server_host}:{self.client.server_port}",
                     font=ctk.CTkFont("Consolas", 10), text_color=TEXT_DIM).pack(anchor="w")

        bot = ctk.CTkFrame(rooms_col, fg_color="transparent")
        bot.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 10))
        bot.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(bot, text="ℹ", width=40, height=28,
                      font=ctk.CTkFont("Consolas", 12),
                      fg_color=BG_CARD, hover_color=BORDER_COLOR, text_color=TEXT_MUTED,
                      command=self._show_credits).grid(row=0, column=0, padx=(0, 3), sticky="ew")
        ctk.CTkButton(bot, text="✕", width=40, height=28,
                      font=ctk.CTkFont("Consolas", 12),
                      fg_color=BG_CARD, hover_color="#3A1A1A", text_color=ACCENT_RED,
                      command=self._quit).grid(row=0, column=1, padx=(3, 0), sticky="ew")

        # ── Area chat centrale ────────────────────────────────
        chat_col = ctk.CTkFrame(root, fg_color=BG_DARK, corner_radius=0)
        chat_col.grid(row=0, column=1, sticky="nsew")
        chat_col.grid_rowconfigure(1, weight=1); chat_col.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(chat_col, fg_color=BG_PANEL, corner_radius=0, height=56, border_width=1, border_color=BORDER_COLOR)
        hdr.grid(row=0, column=0, sticky="ew"); hdr.grid_propagate(False)
        hdr.grid_columnconfigure(0, weight=1)
        self.room_title_lbl = ctk.CTkLabel(hdr, text=f"# {DEFAULT_ROOM}",
                     font=ctk.CTkFont("Consolas", 16, "bold"),
                     text_color=TEXT_PRIMARY)
        self.room_title_lbl.grid(row=0, column=0, sticky="w", padx=16)
        self.online_lbl = ctk.CTkLabel(hdr, text="",
                     font=ctk.CTkFont("Consolas", 12), text_color=ACCENT_GREEN)
        self.online_lbl.grid(row=0, column=1, sticky="e", padx=8)
        ctk.CTkButton(hdr, text="⚡ Trill", width=70, height=30,
                      font=ctk.CTkFont("Consolas", 11),
                      fg_color=BG_CARD, hover_color=BORDER_COLOR,
                      text_color=ACCENT_AMBER,
                      command=self._send_trill).grid(row=0, column=2, padx=(0, 12))

        self.scroll = ctk.CTkScrollableFrame(chat_col, fg_color=BG_DARK, corner_radius=0)
        self.scroll.grid(row=1, column=0, sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)

        self.typing_lbl = ctk.CTkLabel(chat_col, text="",
                     font=ctk.CTkFont("Consolas", 11, slant="italic"), text_color=TEXT_DIM)
        self.typing_lbl.grid(row=2, column=0, sticky="w", padx=16, pady=(0, 2))

        inp_f = ctk.CTkFrame(chat_col, fg_color=BG_PANEL, corner_radius=0, height=60)
        inp_f.grid(row=3, column=0, sticky="ew")
        inp_f.grid_columnconfigure(0, weight=1)
        self.msg_var = tk.StringVar()
        self.msg_entry = ctk.CTkEntry(inp_f, textvariable=self.msg_var,
                     placeholder_text="Scrivi un messaggio... (@nickname per menzione)",
                     font=ctk.CTkFont("Consolas", 14),
                     fg_color=BG_INPUT, border_color=BORDER_COLOR, height=40)
        self.msg_entry.grid(row=0, column=0, sticky="ew", padx=(12, 4), pady=10)
        self.msg_entry.bind("<Return>", lambda _: self._send_message())
        self.msg_entry.bind("<KeyRelease>", self._on_key_release)
        ctk.CTkButton(inp_f, text="😀", width=40, height=40,
                      fg_color=BG_CARD, hover_color=BORDER_COLOR,
                      font=ctk.CTkFont(size=18),
                      command=self._open_emoji_picker).grid(row=0, column=1, padx=(0, 4), pady=10)
        ctk.CTkButton(inp_f, text="➤", width=48, height=40,
                      fg_color=ACCENT_CYAN, hover_color="#2BBBC3", text_color=BG_DARK,
                      command=self._send_message).grid(row=0, column=2, padx=(0, 12), pady=10)

        # ── Sidebar destra: utenti ────────────────────────────
        users_col = ctk.CTkFrame(root, fg_color="#0A0D12", corner_radius=0, width=210, border_width=1, border_color="#1A2030")
        users_col.grid(row=0, column=2, sticky="nsew"); users_col.grid_propagate(False)
        users_col.grid_rowconfigure(1, weight=1); users_col.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(users_col, text="ONLINE",
                     font=ctk.CTkFont("Consolas", 11, "bold"),
                     text_color=TEXT_DIM).grid(row=0, column=0, sticky="w", padx=10, pady=(12, 4))
        self.users_scroll = ctk.CTkScrollableFrame(users_col, fg_color="transparent",
                                                    corner_radius=0)
        self.users_scroll.grid(row=1, column=0, sticky="nsew", padx=4)
        self.users_scroll.grid_columnconfigure(0, weight=1)

        # Aggiungi stanza Generale
        self._add_room_button(DEFAULT_ROOM)
        self._switch_room(DEFAULT_ROOM)
        self._add_system_msg("✓ Benvenuto in SecureChat!", DEFAULT_ROOM)
        self._add_system_msg(f"🔒 Connessione cifrata E2E — il server non legge i messaggi", DEFAULT_ROOM)
        self._update_users()
        self._update_online_count()
        self._setup_tooltips()

    # ── Event dispatcher (chiamato dal thread di rete) ─────────
    def _on_event(self, event_type: str, data: dict):
        text = str(data.get("text", ""))
        if event_type == "message":
            if text.startswith("__TRILL__"):
                play_trill_sound()
            elif f"@{self._get_my_nick()}" in text:
                play_mention_sound()
        self.after(0, self._dispatch, event_type, data)

    def _get_my_nick(self):
        return getattr(self, "my_nick", "")

    def _dispatch(self, event_type: str, data: dict):
        if event_type == "message":
            room  = data.get("room", DEFAULT_ROOM)
            nick  = data["from_nick"]
            text  = data["text"]
            ts    = datetime.fromtimestamp(data["ts"]).strftime("%H:%M")
            if text.startswith("__TRILL__"):
                sender = text[len("__TRILL__"):]
                self._add_trill_bubble(sender, ts, is_me=False, room=room)
            elif not text or text == "[messaggio cifrato — chiave non disponibile]":
                pass
            else:
                is_mention = f"@{self.my_nick}" in text
                self._add_bubble(nick, text, ts, False, room, mention=is_mention)
            if room != self.active_room:
                self._mark_unread(room)

        elif event_type == "private_message":
            peer_id = data["from_id"]
            nick    = data["from_nick"]
            text    = data["text"]
            ts      = datetime.fromtimestamp(data["ts"]).strftime("%H:%M")
            if peer_id not in self.private_wins or not self.private_wins[peer_id].winfo_exists():
                self.private_wins[peer_id] = PrivateChatWindow(
                    self, nick, peer_id, self.client, self.my_nick)
            self.private_wins[peer_id].add_incoming(nick, text)
            self.private_wins[peer_id].lift()

        elif event_type == "peer_joined":
            nick = data["nick"]; room = data.get("room", DEFAULT_ROOM)
            self._add_system_msg(f"→ {nick} è entrato", room, ACCENT_GREEN)
            self._update_users()
            self._update_online_count()

        elif event_type == "peer_left":
            nick = data["nick"]
            self._add_system_msg(f"← {nick} ha lasciato", self.active_room, ACCENT_RED)
            self._update_users()
            self._update_online_count()

        elif event_type == "room_list":
            for r in data.get("rooms", []):
                if r["name"] not in self.room_buttons:
                    self._add_room_button(r["name"], r.get("protected", False))
            joined = data.get("joined")
            if joined:
                self._switch_room(joined)
                self._add_system_msg(f"✓ Sei entrato in #{joined}", joined, ACCENT_GREEN)

        elif event_type == "typing":
            room = data.get("room", DEFAULT_ROOM)
            if room == self.active_room:
                nick = data["nick"]
                pid  = data["from_id"]
                self.typing_timers[pid] = nick
                self._update_typing_label()
                if pid in self.typing_timers:
                    self.after(int(TYPING_TIMEOUT * 1000),
                               lambda p=pid: self._clear_typing(p))

        elif event_type == "typing_stop":
            pid = data.get("from_id", "")
            self._clear_typing(pid)

        elif event_type == "error":
            self._add_system_msg(f"⚠ {data['msg']}", self.active_room, ACCENT_RED)

        elif event_type == "disconnected":
            self._set_server_status("disconnected")
            self._add_system_msg("⚠ Connessione persa — riconnessione in corso...",
                                  self.active_room, ACCENT_RED)

        elif event_type == "reconnecting":
            self._set_server_status("reconnecting", data.get("wait", 0))

        elif event_type == "reconnected":
            self._set_server_status("connected")
            self._add_system_msg("✓ Riconnesso al server", self.active_room, ACCENT_GREEN)

        elif event_type == "reaction":
            room    = data.get("room", DEFAULT_ROOM)
            msg_ts  = data.get("msg_ts", "")
            emoji   = data.get("emoji", "")
            nick    = data.get("from_nick", "")
            # Aggiorna history
            for item in self._room_history.get(room, []):
                ts_key = f"{room}:{item.get('ts','')}:{item.get('nick','')}"
                if ts_key == msg_ts and item["type"] == "bubble":
                    item["reactions"][emoji] = item["reactions"].get(emoji, 0) + 1
                    break
            # Aggiorna widget se visibile
            widget = self._bubble_widgets.get(msg_ts)
            if widget and widget.winfo_exists():
                widget._reactions[emoji] = widget._reactions.get(emoji, 0) + 1
                try:
                    bg = widget._c.cget("fg_color")
                    if isinstance(bg, (list, tuple)): bg = bg[1]
                except:
                    bg = BG_CARD
                widget._render_reactions(bg)

    # ── UI helpers ────────────────────────────────────────────
    def _add_room_button(self, name: str, protected: bool = False):
        prefix = "🔒 #" if protected else "# "
        btn = ctk.CTkButton(self.rooms_scroll,
                            text=prefix + name, anchor="w", height=34,
                            font=ctk.CTkFont("Consolas", 13),
                            fg_color="transparent", hover_color=BG_CARD,
                            text_color=TEXT_MUTED, corner_radius=6,
                            command=lambda n=name: self._switch_room(n))
        btn.grid(row=len(self.room_buttons), column=0, sticky="ew", pady=1)
        self.room_buttons[name] = btn

    def _switch_room(self, name: str):
        for n, b in self.room_buttons.items():
            b.configure(fg_color=BG_CARD if n == name else "transparent",
                        text_color=TEXT_PRIMARY if n == name else TEXT_MUTED)
        self.active_room = name
        self._clear_unread(name)
        if hasattr(self, "room_title_lbl"):
            self.room_title_lbl.configure(text=f"# {name}")
        if name not in self._room_history:
            self._room_history[name] = []
        # Ripopola la chat con lo storico
        for w in self.scroll.winfo_children(): w.destroy()
        for item in self._room_history.get(name, []):
            if item["type"] == "bubble":
                ChatBubble(self.scroll, item["nick"], item["text"],
                           item["ts"], item["is_me"],
                           mention=item.get("mention", False),
                           reactions=item.get("reactions", {})).grid(
                    row=len(self.scroll.winfo_children()),
                    column=0, sticky="ew", padx=8, pady=2)
            elif item["type"] == "trill":
                f = ctk.CTkFrame(self.scroll, fg_color="#1A1A2E", corner_radius=10,
                                 border_width=1, border_color=ACCENT_AMBER)
                f.grid(row=len(self.scroll.winfo_children()), column=0,
                       sticky="ew", padx=24, pady=4)
                f.grid_columnconfigure(0, weight=1)
                ctk.CTkLabel(f, text=item["text"],
                             font=ctk.CTkFont("Consolas", 13, "bold"),
                             text_color=ACCENT_AMBER).grid(row=0, column=0, sticky="w", padx=12, pady=(6,2))
                ctk.CTkLabel(f, text=item["ts"], font=ctk.CTkFont("Consolas", 10),
                             text_color=TEXT_DIM).grid(row=1, column=0, sticky="e", padx=12, pady=(0,5))
            elif item["type"] == "system":
                ctk.CTkLabel(self.scroll, text=item["text"],
                             font=ctk.CTkFont("Consolas", 11, slant="italic"),
                             text_color=item.get("color", TEXT_DIM)).grid(
                    row=len(self.scroll.winfo_children()),
                    column=0, sticky="ew", padx=16, pady=1)
        self._scroll_bottom()

    def _add_bubble(self, nick: str, text: str, ts: str, is_me: bool, room: str, mention: bool = False):
        entry = {"type": "bubble", "nick": nick, "text": text, "ts": ts,
                 "is_me": is_me, "mention": mention, "reactions": {}}
        self._room_history.setdefault(room, []).append(entry)
        if room == self.active_room:
            row = len(self.scroll.winfo_children())
            ts_key = f"{room}:{ts}:{nick}"
            def make_react(en, tsk, rm, sender_nick):
                def on_react(emoji):
                    en["reactions"][emoji] = en["reactions"].get(emoji, 0) + 1
                    if self.client:
                        self.client.send_reaction(rm, tsk, emoji)
                return on_react
            bubble = ChatBubble(self.scroll, nick, text, ts, is_me, mention=mention,
                       reactions=entry["reactions"],
                       on_react=make_react(entry, ts_key, room, nick))
            bubble.grid(row=row, column=0, sticky="ew", padx=8, pady=2)
            self._bubble_widgets[ts_key] = bubble
            self._scroll_bottom()

    def _add_trill_bubble(self, sender: str, ts: str, is_me: bool, room: str = None):
        """Bubble speciale per il trill."""
        room = room or self.active_room
        text = f"⚡ {sender} ti ha inviato un trillo!" if not is_me else "⚡ Trillo inviato!"
        self._room_history.setdefault(room, []).append(
            {"type": "trill", "sender": sender, "text": text, "ts": ts, "is_me": is_me})
        if room == self.active_room:
            row = len(self.scroll.winfo_children())
            f = ctk.CTkFrame(self.scroll, fg_color="#1A1A2E", corner_radius=10,
                             border_width=1, border_color=ACCENT_AMBER)
            f.grid(row=row, column=0, sticky="ew", padx=24, pady=4)
            f.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(f, text=text,
                         font=ctk.CTkFont("Consolas", 13, "bold"),
                         text_color=ACCENT_AMBER).grid(row=0, column=0, sticky="w", padx=12, pady=(6, 2))
            ctk.CTkLabel(f, text=ts, font=ctk.CTkFont("Consolas", 10),
                         text_color=TEXT_DIM).grid(row=1, column=0, sticky="e", padx=12, pady=(0, 5))
            self._scroll_bottom()

    def _add_system_msg(self, text: str, room: str = "", color: str = TEXT_DIM):
        room = room or self.active_room
        self._room_history.setdefault(room, []).append(
            {"type": "system", "text": text, "color": color})
        if room == self.active_room:
            row = len(self.scroll.winfo_children())
            ctk.CTkLabel(self.scroll, text=text,
                         font=ctk.CTkFont("Consolas", 11, slant="italic"),
                         text_color=color).grid(
                row=row, column=0, sticky="ew", padx=16, pady=1)
            self._scroll_bottom()

    def _scroll_bottom(self):
        self.after(50, lambda: self.scroll._parent_canvas.yview_moveto(1.0))

    def _mark_unread(self, room: str):
        self._unread[room] = True
        self._unread_count += 1
        TaskbarBadge.set_count(self, self._unread_count)
        btn = self.room_buttons.get(room)
        if btn:
            cur = btn.cget("text")
            if not cur.startswith("●"):
                btn.configure(text="● " + cur.lstrip("● "), text_color=ACCENT_AMBER)

    def _clear_unread(self, room: str):
        if self._unread.pop(room, None):
            self._unread_count = max(0, self._unread_count - 1)
            TaskbarBadge.set_count(self, self._unread_count)
        btn = self.room_buttons.get(room)
        if btn:
            cur = btn.cget("text")
            btn.configure(text=cur.lstrip("● "), text_color=TEXT_PRIMARY)

    def _update_users(self):
        if not hasattr(self, "users_scroll"): return
        for w in self.users_scroll.winfo_children(): w.destroy()
        if not self.client: return
        # Me
        me_f = ctk.CTkFrame(self.users_scroll, fg_color=BG_CARD, corner_radius=6)
        me_f.grid(row=0, column=0, sticky="ew", pady=1, padx=2)
        me_f.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(me_f, text=f"◈ {self.my_nick}",
                     font=ctk.CTkFont("Consolas", 12, "bold"),
                     text_color=ACCENT_CYAN, anchor="w").grid(
            row=0, column=0, sticky="w", padx=8, pady=4)
        # Altri peer
        for i, (cid, info) in enumerate(self.client.peers.items()):
            nick = info["nick"]
            row_f = ctk.CTkFrame(self.users_scroll, fg_color="transparent", corner_radius=6)
            row_f.grid(row=i+1, column=0, sticky="ew", pady=1, padx=2)
            row_f.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row_f, text=f"○ {nick}",
                         font=ctk.CTkFont("Consolas", 12), text_color=TEXT_MUTED,
                         anchor="w").grid(row=0, column=0, sticky="w", padx=8, pady=3)
            chat_btn = ctk.CTkButton(row_f, text="💬", width=26, height=26,
                          font=ctk.CTkFont("Consolas", 12),
                          fg_color="transparent", hover_color=BG_CARD,
                          text_color=ACCENT_PURPLE,
                          command=lambda c=cid, n=nick: self._open_private(c, n))
            chat_btn.grid(row=0, column=1, padx=(0, 4))
            tip(chat_btn, f"Apri chat privata con {nick}")

    def _update_online_count(self):
        if not hasattr(self, "online_lbl") or not self.client: return
        n = len(self.client.peers) + 1  # +1 siamo noi
        self.online_lbl.configure(text=f"{n} online")

    def _set_server_status(self, status: str, wait: int = 0):
        if not hasattr(self, "srv_lbl"): return
        if status == "connected":
            self.srv_lbl.configure(text="✓ Connesso", text_color=ACCENT_GREEN)
        elif status == "disconnected":
            self.srv_lbl.configure(text="✗ Disconnesso", text_color=ACCENT_RED)
        elif status == "reconnecting":
            self.srv_lbl.configure(text=f"↻ Riconnessione ({wait}s)...",
                                   text_color=ACCENT_AMBER)

    def _update_typing_label(self):
        if not hasattr(self, "typing_lbl"): return
        names = list(self.typing_timers.values())
        if not names:
            self.typing_lbl.configure(text="")
        elif len(names) == 1:
            self.typing_lbl.configure(text=f"{names[0]} sta scrivendo...")
        else:
            self.typing_lbl.configure(text=f"{', '.join(names)} stanno scrivendo...")

    def _clear_typing(self, peer_id: str):
        self.typing_timers.pop(peer_id, None)
        self._update_typing_label()

    # ── Azioni utente ─────────────────────────────────────────
    def _send_message(self):
        if not self.client: return
        text = self.msg_var.get().strip()
        if not text: return
        self.msg_var.set("")
        ts = datetime.now().strftime("%H:%M")
        self._add_bubble(self.my_nick, text, ts, True, self.active_room)
        self.client.send_message(self.active_room, text)
        if self._typing_sched:
            self.after_cancel(self._typing_sched); self._typing_sched = None
        self.client.send_typing_stop(self.active_room)

    def _on_key_release(self, event):
        if not self.client: return
        now = time.time()
        if now - self._last_typing_sent > 2.0:
            self.client.send_typing(self.active_room)
            self._last_typing_sent = now
        if self._typing_sched:
            self.after_cancel(self._typing_sched)
        self._typing_sched = self.after(int(TYPING_TIMEOUT * 1000),
                                         lambda: self.client.send_typing_stop(self.active_room))

    def _send_trill(self):
        if not self.client: return
        # Usa tag speciale __TRILL__ per riconoscimento affidabile lato ricevente
        self.client.send_message(self.active_room, f"__TRILL__{self.my_nick}")
        ts = datetime.now().strftime("%H:%M")
        self._add_trill_bubble(self.my_nick, ts, is_me=True)
        play_trill_sound()

    def _setup_tooltips(self):
        """Aggiunge tooltip a tutti i pulsanti dell'interfaccia."""
        # Recupera i widget dopo che la UI è stata costruita
        try:
            # Sidebar sinistra
            for w in self.winfo_children():
                self._add_tips_recursive(w)
        except Exception:
            pass

    def _add_tips_recursive(self, parent):
        """Aggiunge tooltip ai pulsanti noti per testo."""
        TIPS = {
            "+":           "Crea una nuova stanza chat",
            "ℹ":           "Informazioni su SecureChat",
            "✕":           "Esci da SecureChat",
            "▶  ENTRA":    "Connettiti al server e accedi alla chat",
            "?  Tutorial": "Mostra la guida all'uso",
            "⚡ Trill":    "Invia un trillo a tutta la stanza — suono + notifica",
            "😀":          "Apri il selettore emoji",
            "➤":           "Invia il messaggio",
            # 💬 ha tooltip dinamico applicato in _update_users
            "✓ Crea stanza": "Crea la stanza con il nome e la password inseriti",
        }
        try:
            for child in parent.winfo_children():
                try:
                    if isinstance(child, ctk.CTkButton):
                        label = child.cget("text")
                        if label in TIPS:
                            tip(child, TIPS[label])
                    self._add_tips_recursive(child)
                except Exception:
                    pass
        except Exception:
            pass

    def _open_emoji_picker(self):
        def insert_emoji(e):
            pos = self.msg_entry.index(tk.INSERT)
            cur = self.msg_var.get()
            self.msg_var.set(cur[:pos] + e + cur[pos:])
            self.msg_entry.icursor(pos + len(e))
            self.msg_entry.focus()
        EmojiPicker(self, insert_emoji)

    def _open_private(self, peer_id: str, nick: str):
        if peer_id not in self.private_wins or not self.private_wins[peer_id].winfo_exists():
            self.private_wins[peer_id] = PrivateChatWindow(
                self, nick, peer_id, self.client, self.my_nick)
        self.private_wins[peer_id].lift()

    def _create_room_dialog(self):
        if not self.client: return
        dlg = ctk.CTkToplevel(self)
        dlg.title("Nuova stanza"); dlg.geometry("400x280"); dlg.configure(fg_color=BG_PANEL)
        dlg.after(50, lambda: (dlg.lift(), dlg.focus_force(), dlg.attributes("-topmost", True), dlg.after(100, lambda: dlg.attributes("-topmost", False))))
        dlg.grab_set()
        ctk.CTkLabel(dlg, text="Crea nuova stanza",
                     font=ctk.CTkFont("Consolas", 15, "bold"),
                     text_color=ACCENT_CYAN).pack(pady=(18, 4))
        form = ctk.CTkFrame(dlg, fg_color="transparent")
        form.pack(fill="x", padx=24)
        ctk.CTkLabel(form, text="NOME", font=ctk.CTkFont("Consolas", 11, "bold"),
                     text_color=TEXT_MUTED).pack(anchor="w", pady=(8, 2))
        name_var = tk.StringVar()
        ctk.CTkEntry(form, textvariable=name_var, height=36,
                     font=ctk.CTkFont("Consolas", 13),
                     fg_color=BG_INPUT, border_color=BORDER_COLOR).pack(fill="x")
        ctk.CTkLabel(form, text="PASSWORD (opzionale)",
                     font=ctk.CTkFont("Consolas", 11, "bold"),
                     text_color=TEXT_MUTED).pack(anchor="w", pady=(8, 2))
        pass_var = tk.StringVar()
        ctk.CTkEntry(form, textvariable=pass_var, show="*", height=36,
                     font=ctk.CTkFont("Consolas", 13),
                     fg_color=BG_INPUT, border_color=BORDER_COLOR).pack(fill="x")

        def create():
            name = name_var.get().strip()
            if not name: return
            self.client.create_room(name, pass_var.get())
            self._add_room_button(name, bool(pass_var.get()))
            self._switch_room(name)
            dlg.destroy()

        ctk.CTkButton(form, text="✓ Crea stanza", height=36,
                      fg_color=ACCENT_CYAN, hover_color="#2BBBC3",
                      text_color=BG_DARK, font=ctk.CTkFont("Consolas", 13, "bold"),
                      command=create).pack(fill="x", pady=(16, 0))

    def _clear(self):
        for w in self.winfo_children(): w.destroy()
        self.room_buttons.clear()

    def _quit(self):
        if self.client: self.client.disconnect()
        self.destroy()

    # ── Tutorial / Crediti ────────────────────────────────────
    def _show_tutorial(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title("Tutorial"); dlg.geometry("520x460"); dlg.configure(fg_color=BG_PANEL)
        dlg.after(50, lambda: (dlg.lift(), dlg.focus_force(), dlg.attributes("-topmost", True), dlg.after(100, lambda: dlg.attributes("-topmost", False))))
        ctk.CTkLabel(dlg, text="◈ Come usare SecureChat",
                     font=ctk.CTkFont("Consolas", 17, "bold"),
                     text_color=ACCENT_CYAN).pack(pady=(18, 4))
        ctk.CTkLabel(dlg, text=f"v{APP_VERSION}", font=ctk.CTkFont("Consolas", 11),
                     text_color=TEXT_DIM).pack()
        steps = [
            ("1. Avvio", "Inserisci nickname e clicca ENTRA. Entri automaticamente in #Generale."),
            ("2. Messaggi", "Scrivi nella barra in basso e premi Invio o ➤."),
            ("3. Stanze", "Clicca + per creare una stanza. Puoi proteggerla con password."),
            ("4. Chat privata", "Clicca 💬 accanto al nome utente per aprire una chat privata."),
            ("5. Cifratura", "Tutti i messaggi sono cifrati E2E. Il server non può leggerli."),
            ("6. Trill", "Manda ⚡ TRILL! a tutta la stanza con il pulsante apposito."),
        ]
        s = ctk.CTkScrollableFrame(dlg, fg_color=BG_DARK, corner_radius=8)
        s.pack(fill="both", expand=True, padx=16, pady=12)
        for title, desc in steps:
            f = ctk.CTkFrame(s, fg_color=BG_CARD, corner_radius=8)
            f.pack(fill="x", pady=4, padx=4)
            ctk.CTkLabel(f, text=title, font=ctk.CTkFont("Consolas", 13, "bold"),
                         text_color=ACCENT_AMBER).pack(anchor="w", padx=12, pady=(8, 2))
            ctk.CTkLabel(f, text=desc, font=ctk.CTkFont("Consolas", 12),
                         text_color=TEXT_MUTED, wraplength=420, justify="left").pack(
                anchor="w", padx=12, pady=(0, 8))

    def _show_credits(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title("Info"); dlg.geometry("360x220"); dlg.configure(fg_color=BG_PANEL)
        dlg.after(50, lambda: (dlg.lift(), dlg.focus_force(), dlg.attributes("-topmost", True), dlg.after(100, lambda: dlg.attributes("-topmost", False))))
        ctk.CTkLabel(dlg, text="◈ SecureChat",
                     font=ctk.CTkFont("Consolas", 19, "bold"),
                     text_color=ACCENT_CYAN).pack(pady=(24, 4))
        ctk.CTkLabel(dlg, text=f"Versione {APP_VERSION}",
                     font=ctk.CTkFont("Consolas", 12), text_color=TEXT_DIM).pack()
        ctk.CTkLabel(dlg, text=f"© {APP_AUTHOR}",
                     font=ctk.CTkFont("Consolas", 13), text_color=TEXT_MUTED).pack(pady=(12, 2))
        ctk.CTkLabel(dlg, text=APP_WEBSITE,
                     font=ctk.CTkFont("Consolas", 12), text_color=ACCENT_CYAN).pack()
        ctk.CTkLabel(dlg, text=APP_GITHUB,
                     font=ctk.CTkFont("Consolas", 11), text_color=TEXT_DIM).pack(pady=(2, 0))


# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    app = SecureChatApp()
    app.mainloop()