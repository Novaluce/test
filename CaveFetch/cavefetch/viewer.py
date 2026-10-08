"""In-app image viewer window: fit/100% zoom, pan, prev/next, tag sidebar."""
import io
import tkinter as tk
import webbrowser
from tkinter import ttk

from PIL import Image, ImageTk

from .api import BASE

RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS
IMG_EXT = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
CATEGORIES = [("artist", "Artists", "#d4a017"), ("copyright", "Copyrights", "#c026d3"),
              ("character", "Characters", "#16a34a"), ("species", "Species", "#ea580c"),
              ("general", "General", None), ("lore", "Lore", "#15803d"), ("meta", "Meta", "#9ca3af")]


class Viewer:
    def __init__(self, app):
        self.app = app
        self.c = app.theme.c
        self.index = -1
        self.pid = None
        self.post = None
        self.gen = 0
        self.pil = None
        self.photo = None
        self.fit = True
        self.note = ""
        self.msg = ""
        self._rz = None

        w = self.win = tk.Toplevel(app.root)
        w.withdraw()
        w.title("CaveFetch - viewer")
        w.geometry("1100x800")
        w.minsize(520, 380)
        w.protocol("WM_DELETE_WINDOW", self.hide)

        bar = ttk.Frame(w, padding=6)
        bar.pack(fill="x")
        for text, cmd in (("< Prev", self.prev), ("Next >", self.next)):
            ttk.Button(bar, text=text, command=cmd, style="Tool.TButton").pack(side="left", padx=(0, 4))
        self.pos_lbl = ttk.Label(bar, text="")
        self.pos_lbl.pack(side="left", padx=8)
        for text, cmd in (("Open in browser", self.open_browser), ("Tags", self.toggle_tags),
                          ("Download", self.download), ("Load original", self.load_original)):
            ttk.Button(bar, text=text, command=cmd, style="Tool.TButton").pack(side="right", padx=(4, 0))
        self.fit_btn = ttk.Button(bar, text="100%", command=self.toggle_fit,
                                  style="Tool.TButton", width=5)
        self.fit_btn.pack(side="right", padx=(4, 0))

        self.info = ttk.Label(w, text="", padding=(8, 4))
        self.info.pack(side="bottom", fill="x")

        body = ttk.Frame(w)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.tags_frame = ttk.Frame(body)
        self.tags_frame.grid(row=0, column=1, sticky="ns")
        self.txt = tk.Text(self.tags_frame, width=28, wrap="none", borderwidth=0,
                           highlightthickness=0, cursor="arrow", padx=8, pady=6)
        sb = ttk.Scrollbar(self.tags_frame, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="y")
        self.txt.bind("<Button-1>", self._tag_click)
        self.txt.tag_bind("link", "<Enter>", lambda _e: self.txt.config(cursor="hand2"))
        self.txt.tag_bind("link", "<Leave>", lambda _e: self.txt.config(cursor="arrow"))

        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<Double-Button-1>", lambda _e: self.toggle_fit())
        self.canvas.bind("<ButtonPress-1>", lambda e: (self.canvas.focus_set(),
                                                       self.canvas.scan_mark(e.x, e.y)))
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.bind("<MouseWheel>", self._wheel)
        w.bind("<Left>", lambda _e: self.prev())
        w.bind("<Right>", lambda _e: self.next())
        w.bind("<Escape>", lambda _e: self.hide())
        w.bind("<f>", lambda _e: self.toggle_fit())

        app.theme.on_change(self._retheme)

    # ---- theming
    def _retheme(self, c):
        self.c = c
        self.win.configure(bg=c["bg"])
        self.canvas.configure(bg=c["viewer_bg"])
        self.txt.configure(bg=c["entry"], fg=c["fg"], selectbackground=c["accent"],
                           selectforeground="#ffffff")
        self.txt.tag_configure("head", font=("Segoe UI", 10, "bold"), spacing1=4)
        for key, _title, color in CATEGORIES:
            self.txt.tag_configure(f"cat_{key}", foreground=color or c["fg"])
        if self.pil is None and self.msg:
            self._center_msg(self.msg)

    # ---- open / navigate
    def show(self, pid):
        ids = [i for i, _ in self.app.tiles]
        if pid not in ids:
            return
        self.index = ids.index(pid)
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        self.canvas.focus_set()
        self._open_current()

    def hide(self):
        self.gen += 1          # drop any in-flight load
        self.win.withdraw()

    def prev(self):
        if self.index > 0:
            self.index -= 1
            self._open_current()

    def next(self):
        tiles = self.app.tiles
        if self.index + 1 < len(tiles):
            self.index += 1
            self._open_current()
        if self.index >= len(tiles) - 6:
            self.app.load_more()      # keep the grid (and "Next") topped up

    def _open_current(self, original=False):
        tiles = self.app.tiles
        pid, _ = tiles[self.index]
        self.pid, self.post = pid, self.app.posts[pid]
        self.gen += 1
        gen = self.gen
        self.pil = None
        self.fit = True
        self.pos_lbl.config(text=f"{self.index + 1} / {len(tiles)}")
        self.win.title(f"CaveFetch - #{pid}")
        self._fill_tags()
        url, self.note = self._pick_url(original)
        self._update_info()
        if not url:
            self._center_msg("No file available for this post.\n"
                             "(Log in, or it may be blacklisted / deleted.)")
            return
        self._center_msg("Loading...")
        self.app.pool.submit(self._fetch, gen, url)

    def _pick_url(self, original):
        f = self.post.get("file") or {}
        sample = self.post.get("sample") or {}
        preview = (self.post.get("preview") or {}).get("url")
        ext = (f.get("ext") or "").lower()
        if ext in IMG_EXT:
            if original or not sample.get("url"):
                return f.get("url") or sample.get("url") or preview, ""
            return sample.get("url"), "   (resized sample - 'Load original' for full size)"
        # video / flash: show a still, can't play inside Tk
        return (sample.get("url") or preview,
                f"   ({ext} file shown as still image - use 'Open in browser' to play)")

    def _fetch(self, gen, url):
        try:
            img = Image.open(io.BytesIO(self.app.api.fetch_bytes(url)))
            img.load()
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")
            self.app.ui(self._loaded, gen, img, None)
        except Exception as ex:
            self.app.ui(self._loaded, gen, None, ex)

    def _loaded(self, gen, img, err):
        if gen != self.gen:
            return
        if err:
            self._center_msg(f"Could not load image:\n{err}")
            return
        self.pil, self.msg = img, ""
        self.render()

    # ---- drawing
    def _center_msg(self, text):
        self.msg = text
        self.canvas.delete("all")
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.canvas.create_text(cw / 2, ch / 2, text=text, fill=self.c["fg"],
                                justify="center", width=max(cw - 40, 100))
        self.canvas.configure(scrollregion=(0, 0, cw, ch))

    def _on_configure(self, _e):
        if self._rz:
            self.win.after_cancel(self._rz)
        self._rz = self.win.after(100, self._redraw)

    def _redraw(self):
        self._rz = None
        if self.pil is not None:
            self.render()
        elif self.msg:
            self._center_msg(self.msg)

    def render(self):
        if self.pil is None:
            return
        cw, ch = max(self.canvas.winfo_width(), 1), max(self.canvas.winfo_height(), 1)
        iw, ih = self.pil.size
        s = min(cw / iw, ch / ih, 1.0) if self.fit else 1.0
        w, h = max(1, int(iw * s)), max(1, int(ih * s))
        img = self.pil if (w, h) == (iw, ih) else self.pil.resize((w, h), RESAMPLE)
        self.photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(max((cw - w) // 2, 0), max((ch - h) // 2, 0),
                                 anchor="nw", image=self.photo)
        self.canvas.configure(scrollregion=(0, 0, max(w, cw), max(h, ch)))
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.fit_btn.config(text="100%" if self.fit else "Fit")

    def toggle_fit(self):
        self.fit = not self.fit
        self.render()

    def _wheel(self, e):
        if not self.fit:
            self.canvas.yview_scroll(int(-e.delta / 120) * 3, "units")

    # ---- info + tags
    def _update_info(self):
        p = self.post
        f = p.get("file") or {}
        score = (p.get("score") or {}).get("total", 0)
        kb = (f.get("size") or 0) / 1024
        self.info.config(text=(f"#{self.pid}    rating {str(p.get('rating', '?')).upper()}    "
                               f"score {score}    favs {p.get('fav_count', 0)}    "
                               f"{f.get('width')}x{f.get('height')}    {f.get('ext', '?')}    "
                               f"{kb:.0f} KB{self.note}"))

    def _fill_tags(self):
        t = self.txt
        t.config(state="normal")
        t.delete("1.0", "end")
        tags = self.post.get("tags") or {}
        for key, title, _color in CATEGORIES:
            names = tags.get(key) or []
            if not names:
                continue
            t.insert("end", title + "\n", ("head", f"cat_{key}"))
            for n in names:
                t.insert("end", n + "\n", ("link", f"cat_{key}", f"t:{n}"))
            t.insert("end", "\n")
        t.config(state="disabled")

    def _tag_click(self, e):
        for name in self.txt.tag_names(self.txt.index(f"@{e.x},{e.y}")):
            if name.startswith("t:"):
                self.hide()
                self.app.search_for(name[2:])
                return

    def toggle_tags(self):
        if self.tags_frame.winfo_ismapped():
            self.tags_frame.grid_remove()
        else:
            self.tags_frame.grid()

    # ---- actions
    def load_original(self):
        if self.index >= 0:
            self._open_current(original=True)

    def download(self):
        if self.pid is not None:
            self.app.download_ids([self.pid], parent=self.win)

    def open_browser(self):
        if self.pid is not None:
            webbrowser.open(f"{BASE}/posts/{self.pid}")
