"""Tag autocomplete dropdown for the search box (uses e621's /tags/autocomplete.json)."""
import tkinter as tk

# e621 tag category -> colour
CAT_COLORS = {1: "#d4a017", 3: "#c026d3", 4: "#16a34a", 5: "#ea580c", 7: "#9ca3af", 8: "#15803d"}
IGNORED_KEYS = {"Up", "Down", "Return", "Tab", "Escape", "Shift_L", "Shift_R", "Control_L",
                "Control_R", "Alt_L", "Alt_R", "Caps_Lock", "ISO_Left_Tab"}


def fmt_count(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "?"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


class TagAutocomplete:
    DELAY_MS = 250
    MAX_ROWS = 10

    def __init__(self, app, entry):
        self.app = app
        self.entry = entry
        self.cache = {}
        self.items = []
        self.seq = 0
        self.visible = False
        self._job = None

        self.pop = tk.Toplevel(app.root)
        self.pop.withdraw()
        self.pop.overrideredirect(True)
        try:
            self.pop.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.lb = tk.Listbox(self.pop, activestyle="none", exportselection=False,
                             takefocus=0, borderwidth=0, highlightthickness=1)
        self.lb.pack(fill="both", expand=True)
        self.lb.bind("<Button-1>", self._click)

        entry.bind("<KeyRelease>", self._on_key, add="+")
        entry.bind("<Down>", self._down)
        entry.bind("<Up>", self._up)
        entry.bind("<Tab>", self._tab)
        entry.bind("<Escape>", self._escape)
        entry.bind("<FocusOut>", lambda _e: app.root.after(150, self.hide), add="+")
        app.root.bind("<Configure>",
                      lambda e: self.hide() if e.widget is app.root else None, add="+")
        app.theme.on_change(self._retheme)

    # ---- theming
    def _retheme(self, c):
        self.pop.configure(bg=c["border"])
        self.lb.configure(bg=c["entry"], fg=c["fg"], selectbackground=c["accent"],
                          selectforeground="#ffffff", highlightbackground=c["border"],
                          highlightcolor=c["border"])

    # ---- token under the cursor
    def _token(self):
        text = self.entry.get()
        pos = self.entry.index("insert")
        before = text[:pos]
        start = before.rfind(" ") + 1
        tok = before[start:]
        prefix = ""
        if tok and tok[0] in "-~":
            prefix, tok = tok[0], tok[1:]
        return start, prefix, tok

    # ---- typing
    def _on_key(self, e):
        if e.keysym in IGNORED_KEYS:
            return
        if self._job:
            self.app.root.after_cancel(self._job)
        self._job = self.app.root.after(self.DELAY_MS, self._query)

    def _query(self):
        self._job = None
        tok = self._token()[2]
        if len(tok) < 2 or ":" in tok:
            self.hide()
            return
        self.seq += 1
        seq, key = self.seq, tok.lower()
        if key in self.cache:
            self._show(seq, tok, self.cache[key])
            return

        def work():
            try:
                res = self.app.api.autocomplete(tok)
            except Exception:
                return
            self.app.ui(self._store_and_show, seq, tok, res)
        self.app.pool.submit(work)

    def _store_and_show(self, seq, tok, res):
        self.cache[tok.lower()] = res
        self._show(seq, tok, res)

    def _show(self, seq, tok, items):
        try:
            focused = self.app.root.focus_get() is self.entry
        except KeyError:
            focused = False
        if seq != self.seq or self._token()[2] != tok or not focused or not items:
            if seq == self.seq:
                self.hide()
            return
        self.items = items[: self.MAX_ROWS]
        fg = self.app.theme.c["fg"]
        self.lb.delete(0, "end")
        for i, it in enumerate(self.items):
            name, ant = it.get("name", "?"), it.get("antecedent_name")
            label = f"{ant}  ->  {name}" if ant else name
            self.lb.insert("end", f"{label}   ({fmt_count(it.get('post_count'))})")
            self.lb.itemconfig(i, foreground=CAT_COLORS.get(it.get("category"), fg))
        self.lb.configure(height=len(self.items))
        self.lb.update_idletasks()
        x = self.entry.winfo_rootx()
        y = self.entry.winfo_rooty() + self.entry.winfo_height()
        w = max(self.entry.winfo_width(), 260)
        self.pop.geometry(f"{w}x{self.lb.winfo_reqheight()}+{x}+{y}")
        self.pop.deiconify()
        self.pop.lift()
        self.visible = True

    def hide(self):
        if self.visible:
            self.pop.withdraw()
            self.lb.selection_clear(0, "end")
            self.visible = False

    # ---- keyboard / mouse
    def _select(self, i):
        self.lb.selection_clear(0, "end")
        if i is not None:
            self.lb.selection_set(i)
            self.lb.see(i)

    def _down(self, _e):
        if not self.visible:
            return None
        cur = self.lb.curselection()
        self._select(0 if not cur else min(cur[0] + 1, len(self.items) - 1))
        return "break"

    def _up(self, _e):
        if not self.visible:
            return None
        cur = self.lb.curselection()
        self._select(cur[0] - 1 if cur and cur[0] > 0 else None)
        return "break"

    def _tab(self, _e):
        if not self.visible:
            return None
        cur = self.lb.curselection()
        self._accept(cur[0] if cur else 0)
        return "break"

    def _escape(self, _e):
        if not self.visible:
            return None
        self.hide()
        return "break"

    def _click(self, e):
        i = self.lb.nearest(e.y)
        if 0 <= i < len(self.items):
            self._accept(i)
        return "break"

    def accept_selected(self):
        """For the Return key: True if a highlighted suggestion was inserted."""
        cur = self.lb.curselection() if self.visible else ()
        if cur:
            self._accept(cur[0])
            return True
        return False

    def _accept(self, i):
        name = self.items[i]["name"]
        text = self.entry.get()
        pos = self.entry.index("insert")
        start, prefix, _tok = self._token()
        end = text.find(" ", pos)
        end = len(text) if end == -1 else end
        head = text[:start] + prefix + name + " "
        self.entry.delete(0, "end")
        self.entry.insert(0, head + text[end:].lstrip())
        self.entry.icursor(len(head))
        self.hide()
        self.entry.focus_set()
