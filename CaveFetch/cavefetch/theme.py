"""Dark / light theme manager for ttk and classic tk widgets."""
import tkinter as tk
from tkinter import ttk

PALETTES = {
    "dark": dict(
        bg="#111827", panel="#1f2937", fg="#e5e7eb", muted="#9ca3af",
        entry="#0b1220", btn="#374151", btn_active="#4b5563", border="#374151",
        accent="#3b82f6", tile="#0b1220", grid_bg="#1f2937", viewer_bg="#0b0f19",
    ),
    "light": dict(
        bg="#f3f4f6", panel="#e5e7eb", fg="#111827", muted="#6b7280",
        entry="#ffffff", btn="#e5e7eb", btn_active="#d1d5db", border="#c7ccd4",
        accent="#2563eb", tile="#f9fafb", grid_bg="#e5e7eb", viewer_bg="#d9dde3",
    ),
}


class Theme:
    def __init__(self, root, name="dark"):
        self.root = root
        self.name = name if name in PALETTES else "dark"
        self.style = ttk.Style(root)
        try:
            self.style.theme_use("clam")   # the only built-in ttk theme that is fully recolorable
        except tk.TclError:
            pass
        self._listeners = []
        self.apply()

    @property
    def c(self):
        return PALETTES[self.name]

    def on_change(self, fn):
        """Register fn(colors); it is also called once immediately."""
        self._listeners.append(fn)
        fn(self.c)

    def toggle(self):
        self.name = "light" if self.name == "dark" else "dark"
        self.apply()

    def apply(self):
        c, s = self.c, self.style
        self.root.configure(bg=c["bg"])
        s.configure(".", background=c["bg"], foreground=c["fg"], fieldbackground=c["entry"],
                    bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"],
                    troughcolor=c["panel"], focuscolor=c["btn"], insertcolor=c["fg"])
        s.configure("TFrame", background=c["bg"])
        s.configure("TLabel", background=c["bg"], foreground=c["fg"])
        s.configure("TButton", background=c["btn"], foreground=c["fg"], bordercolor=c["border"],
                    lightcolor=c["btn"], darkcolor=c["btn"], padding=(10, 4), focusthickness=1)
        s.map("TButton",
              background=[("pressed", c["accent"]), ("active", c["btn_active"])],
              lightcolor=[("active", c["btn_active"])], darkcolor=[("active", c["btn_active"])],
              foreground=[("disabled", c["muted"])])
        s.configure("Tool.TButton", padding=(6, 3))
        s.configure("Icon.TButton", background=c["bg"], lightcolor=c["bg"], darkcolor=c["bg"],
                    bordercolor=c["bg"], padding=3)
        s.map("Icon.TButton", background=[("active", c["btn_active"])],
              lightcolor=[("active", c["btn_active"])], darkcolor=[("active", c["btn_active"])])
        s.configure("TEntry", fieldbackground=c["entry"], foreground=c["fg"],
                    insertcolor=c["fg"], bordercolor=c["border"], padding=3)
        s.map("TEntry", bordercolor=[("focus", c["accent"])], lightcolor=[("focus", c["accent"])])
        s.configure("TCombobox", fieldbackground=c["entry"], background=c["btn"],
                    foreground=c["fg"], arrowcolor=c["fg"], bordercolor=c["border"],
                    selectbackground=c["entry"], selectforeground=c["fg"], padding=3)
        s.map("TCombobox",
              fieldbackground=[("readonly", c["entry"])],
              foreground=[("readonly", c["fg"])],
              selectbackground=[("readonly", c["entry"])],
              selectforeground=[("readonly", c["fg"])],
              background=[("active", c["btn_active"])])
        s.configure("TCheckbutton", background=c["bg"], foreground=c["fg"],
                    indicatorbackground=c["entry"], indicatorforeground=c["fg"])
        s.map("TCheckbutton", background=[("active", c["bg"])],
              indicatorbackground=[("selected", c["accent"])])
        s.configure("Vertical.TScrollbar", background=c["btn"], troughcolor=c["panel"],
                    bordercolor=c["panel"], arrowcolor=c["fg"],
                    lightcolor=c["btn"], darkcolor=c["btn"])
        s.map("Vertical.TScrollbar", background=[("active", c["btn_active"])])
        s.configure("Horizontal.TProgressbar", background=c["accent"], troughcolor=c["panel"],
                    bordercolor=c["border"], lightcolor=c["accent"], darkcolor=c["accent"])
        for fn in list(self._listeners):
            try:
                fn(c)
            except tk.TclError:
                pass   # widget was destroyed

    def style_combobox(self, combo):
        """The drop-down list of a ttk.Combobox is a plain tk Listbox; recolor it directly."""
        c = self.c
        try:
            combo.tk.eval(
                f"set _pd [ttk::combobox::PopdownWindow {combo}]; "
                f"$_pd.f.l configure -background {c['entry']} -foreground {c['fg']} "
                f"-selectbackground {c['accent']} -selectforeground #ffffff")
        except tk.TclError:
            pass
