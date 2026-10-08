"""Main window: search bar, infinite-scroll thumbnail grid, selection, batch download."""
import io
import os
import queue
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from . import config, icons
from .api import Api
from .autocomplete import TagAutocomplete
from .theme import Theme
from .viewer import Viewer

TILE = 166            # px per grid cell (150px preview + padding)
SELECT_COLOR = "#3b82f6"


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = config.load()
        self.api = Api()
        self.pool = ThreadPoolExecutor(max_workers=8)
        self.ui_q = queue.Queue()

        self.generation = 0          # bumps on each new search
        self.loading = False
        self.exhausted = False
        self.tags = ""
        self.lowest_id = None
        self.posts = {}              # id -> post dict
        self.tiles = []              # [(id, label)] in display order
        self.tile_map = {}           # id -> label
        self.selected = set()
        self.cols = 1

        self.theme = Theme(root, self.cfg.get("theme", "dark"))
        root.title("CaveFetch - e621")
        root.geometry("1000x720")
        root.minsize(560, 380)
        self._build()
        self.viewer = Viewer(self)
        self.theme.on_change(self._retheme)
        root.after(50, self._pump)

        if self.cfg.get("user") and self.cfg.get("key"):
            self._do_login(self.cfg["user"], self.cfg["key"], remember=True, silent=True)

    # ---- layout
    def _build(self):
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Tags:").grid(row=0, column=0, padx=(0, 4))
        self.tag_var = tk.StringVar()
        self.entry = ttk.Entry(top, textvariable=self.tag_var)
        self.entry.grid(row=0, column=1, sticky="ew")
        self.entry.bind("<Return>", self._on_return)
        self.entry.focus_set()

        self.rating_var = tk.StringVar(value="any")
        self.rating_combo = ttk.Combobox(top, textvariable=self.rating_var, width=13,
                                         state="readonly",
                                         values=["any", "safe", "questionable", "explicit"])
        self.rating_combo.grid(row=0, column=2, padx=4)
        ttk.Button(top, text="Search", command=self.search).grid(row=0, column=3)
        self.login_btn = ttk.Button(top, text="Login", command=self.login_dialog)
        self.login_btn.grid(row=0, column=4, padx=(8, 0))
        self.bulb_btn = ttk.Button(top, style="Icon.TButton", command=self.toggle_theme,
                                   takefocus=False, cursor="hand2")
        self.bulb_btn.grid(row=0, column=5, padx=(8, 0))

        bar = ttk.Frame(self.root, padding=(6, 0, 6, 6))
        bar.pack(fill="x")
        ttk.Button(bar, text="Select all", command=self.select_all).pack(side="left")
        ttk.Button(bar, text="Clear", command=self.clear_sel).pack(side="left", padx=4)
        ttk.Button(bar, text="Download selected",
                   command=lambda: self.download(False)).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="Download all loaded",
                   command=lambda: self.download(True)).pack(side="left", padx=4)
        self.sel_lbl = ttk.Label(bar, text="0 selected")
        self.sel_lbl.pack(side="right")

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, highlightthickness=0)
        self.vsb = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_yscroll)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_resize)
        self.root.bind_all("<MouseWheel>", self._on_wheel)

        st = ttk.Frame(self.root, padding=4)
        st.pack(fill="x")
        self.status = ttk.Label(st, text="Enter tags and press Search.")
        self.status.pack(side="left")
        self.prog = ttk.Progressbar(st, length=220)
        self.prog.pack(side="right")

        # autocomplete needs self.api / self.pool / self.ui / self.theme, all ready by now
        self.ac = TagAutocomplete(self, self.entry)

    def _on_return(self, _e):
        if self.ac.accept_selected():
            return "break"
        self.ac.hide()
        self.search()
        return "break"

    # ---- theming
    def toggle_theme(self):
        self.theme.toggle()
        self.cfg["theme"] = self.theme.name
        config.save(self.cfg)

    def _retheme(self, c):
        self.canvas.configure(bg=c["grid_bg"])
        self.inner.configure(bg=c["grid_bg"])
        for pid, lbl in self.tiles:
            self._style_tile(pid, lbl)
        # lit bulb = light theme, dark grey bulb = dark theme
        self.bulb_img = icons.lightbulb(26, lit=self.theme.name == "light",
                                        off_color=c["muted"], base_color=c["muted"])
        self.bulb_btn.configure(image=self.bulb_img)
        self.theme.style_combobox(self.rating_combo)

    def _style_tile(self, pid, lbl):
        c = self.theme.c
        lbl.config(bg=c["tile"], fg=c["muted"],
                   highlightbackground=SELECT_COLOR if pid in self.selected else c["grid_bg"])

    # ---- cross-thread helper
    def ui(self, fn, *a):
        self.ui_q.put((fn, a))

    def _pump(self):
        try:
            while True:
                fn, a = self.ui_q.get_nowait()
                fn(*a)
        except queue.Empty:
            pass
        self.root.after(50, self._pump)

    def set_status(self, text):
        self.status.config(text=text)

    # ---- login
    def login_dialog(self):
        if self.api.user:
            if messagebox.askyesno("Logout", f"Logged in as {self.api.user}. Log out?"):
                self.api.logout()
                self.cfg.pop("user", None)
                self.cfg.pop("key", None)
                config.save(self.cfg)
                self.login_btn.config(text="Login")
                self.set_status("Logged out.")
            return
        d = tk.Toplevel(self.root)
        d.title("e621 login")
        d.configure(bg=self.theme.c["bg"])
        d.transient(self.root)
        d.resizable(False, False)
        f = ttk.Frame(d, padding=12)
        f.pack()
        ttk.Label(f, text="Username").grid(row=0, column=0, sticky="w")
        u = ttk.Entry(f, width=34)
        u.grid(row=0, column=1, pady=2)
        ttk.Label(f, text="API key").grid(row=1, column=0, sticky="w")
        k = ttk.Entry(f, width=34, show="*")
        k.grid(row=1, column=1, pady=2)
        rem = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Remember on this PC (saved unencrypted)",
                        variable=rem).grid(row=2, column=0, columnspan=2, sticky="w")

        def go():
            if u.get().strip() and k.get().strip():
                self._do_login(u.get().strip(), k.get().strip(), rem.get())
                d.destroy()
        ttk.Button(f, text="Log in", command=go).grid(row=3, column=1, sticky="e", pady=(8, 0))
        u.focus_set()
        d.bind("<Return>", lambda _e: go())
        d.grab_set()

    def _do_login(self, user, key, remember, silent=False):
        self.set_status("Logging in...")

        def work():
            try:
                prof = self.api.login(user, key)
                self.ui(self._login_ok, prof.get("name", user), user, key, remember)
            except Exception as ex:
                self.ui(self._login_fail, ex, silent)
        self.pool.submit(work)

    def _login_ok(self, name, user, key, remember):
        self.login_btn.config(text=f"Logout ({name})")
        self.set_status(f"Logged in as {name}.")
        if remember:
            self.cfg.update(user=user, key=key)
            config.save(self.cfg)

    def _login_fail(self, ex, silent):
        self.login_btn.config(text="Login")
        self.set_status("Login failed.")
        if not silent:
            messagebox.showerror("Login failed", f"{ex}\n\nUse your API key, not your password.")

    # ---- searching / paging
    def search_for(self, tags):
        """Used by the viewer when a tag in its sidebar is clicked."""
        self.tag_var.set(tags)
        self.root.deiconify()
        self.root.lift()
        self.search()

    def search(self):
        tags = self.tag_var.get().strip()
        r = self.rating_var.get()
        if r != "any":
            tags += f" rating:{r[0]}"
        self.tags = tags.strip()
        self.generation += 1
        self.loading = False
        self.exhausted = False
        self.lowest_id = None
        self.posts.clear()
        self.selected.clear()
        for _id, lbl in self.tiles:
            lbl.destroy()
        self.tiles.clear()
        self.tile_map.clear()
        self.canvas.yview_moveto(0)
        self._update_sel()
        self.load_more()

    def load_more(self):
        if self.loading or self.exhausted:
            return
        self.loading = True
        gen = self.generation
        self.set_status("Loading...")

        def work():
            try:
                posts = self.api.posts(self.tags, self.lowest_id)
                self.ui(self._page_loaded, gen, posts, None)
            except Exception as ex:
                self.ui(self._page_loaded, gen, [], ex)
        self.pool.submit(work)

    def _page_loaded(self, gen, posts, err):
        if gen != self.generation:
            return
        self.loading = False
        if err:
            self.set_status(f"Error: {err}")
            return
        if not posts:
            self.exhausted = True
            self.set_status(f"{len(self.posts)} posts loaded (end of results).")
            return
        for p in posts:
            if p["id"] not in self.posts:
                self.posts[p["id"]] = p
                self._add_tile(p, gen)
        self.lowest_id = min(p["id"] for p in posts)
        self._reflow()
        self.set_status(f"{len(self.posts)} posts loaded.")
        self.root.after(100, self._maybe_fill)

    def _maybe_fill(self):
        if self.canvas.yview()[1] > 0.9:
            self.load_more()

    # ---- tiles
    def _add_tile(self, post, gen):
        pid = post["id"]
        lbl = tk.Label(self.inner, text="...", width=20, height=9,
                       highlightthickness=3, cursor="hand2")
        self._style_tile(pid, lbl)
        lbl.bind("<Button-1>", lambda _e, i=pid: self.toggle(i))
        lbl.bind("<Double-Button-1>", lambda _e, i=pid: self.viewer.show(i))
        self.tiles.append((pid, lbl))
        self.tile_map[pid] = lbl
        url = (post.get("preview") or {}).get("url")
        if not url:
            lbl.config(text="no preview")
            return

        def work():
            try:
                img = Image.open(io.BytesIO(self.api.fetch_bytes(url)))
                img.thumbnail((150, 150))
                img.load()
                self.ui(self._set_thumb, gen, lbl, img)
            except Exception:
                self.ui(self._set_thumb, gen, lbl, None)
        self.pool.submit(work)

    def _set_thumb(self, gen, lbl, img):
        if gen != self.generation or not lbl.winfo_exists():
            return
        if img is None:
            lbl.config(text="error")
            return
        ph = ImageTk.PhotoImage(img)
        lbl.config(image=ph, text="", width=ph.width(), height=ph.height())
        lbl.image = ph  # keep a reference

    def _reflow(self):
        self.cols = max(1, self.canvas.winfo_width() // TILE)
        for n, (_id, lbl) in enumerate(self.tiles):
            lbl.grid(row=n // self.cols, column=n % self.cols, padx=3, pady=3)

    def _on_resize(self, _e):
        if max(1, self.canvas.winfo_width() // TILE) != self.cols:
            self._reflow()

    # ---- scrolling
    def _on_yscroll(self, lo, hi):
        self.vsb.set(lo, hi)
        if float(hi) > 0.92:
            self.load_more()

    def _on_wheel(self, e):
        try:
            if e.widget.winfo_toplevel() is not self.root:
                return        # wheel over viewer / popups is handled there
        except AttributeError:
            return
        self.canvas.yview_scroll(int(-e.delta / 120) * 3, "units")

    # ---- selection
    def toggle(self, pid):
        if pid in self.selected:
            self.selected.discard(pid)
        else:
            self.selected.add(pid)
        self._style_tile(pid, self.tile_map[pid])
        self._update_sel()

    def select_all(self):
        self.selected.update(pid for pid, _ in self.tiles)
        for pid, lbl in self.tiles:
            self._style_tile(pid, lbl)
        self._update_sel()

    def clear_sel(self):
        self.selected.clear()
        for pid, lbl in self.tiles:
            self._style_tile(pid, lbl)
        self._update_sel()

    def _update_sel(self):
        self.sel_lbl.config(text=f"{len(self.selected)} selected")

    # ---- batch download
    def download(self, everything):
        ids = [pid for pid, _ in self.tiles if everything or pid in self.selected]
        if not ids:
            messagebox.showinfo("Nothing to download",
                                "Select some images, or use 'Download all loaded'.")
            return
        self.download_ids(ids)

    def download_ids(self, ids, parent=None):
        folder = filedialog.askdirectory(
            title="Save images to...", parent=parent or self.root,
            initialdir=self.cfg.get("last_dir") or os.path.expanduser("~"))
        if not folder:
            return
        self.cfg["last_dir"] = folder
        config.save(self.cfg)

        jobs, skipped = [], 0
        for pid in ids:
            f = self.posts[pid].get("file") or {}
            url = f.get("url")
            if not url:
                skipped += 1
                continue
            jobs.append((url, os.path.join(folder, f"{pid}.{f.get('ext', 'jpg')}")))

        self.prog.config(maximum=max(1, len(jobs)), value=0)
        state = {"done": 0, "fail": 0, "skip": skipped}
        lock = threading.Lock()

        def one(job):
            url, path = job
            try:
                if not os.path.exists(path):
                    self.api.download_to(url, path)
            except Exception:
                with lock:
                    state["fail"] += 1
            with lock:
                state["done"] += 1
                d = state["done"]
            self.ui(self._dl_progress, d, len(jobs), dict(state))

        def runner():
            with ThreadPoolExecutor(max_workers=3) as ex:
                list(ex.map(one, jobs))
        threading.Thread(target=runner, daemon=True).start()
        self.set_status(f"Downloading {len(jobs)} files...")

    def _dl_progress(self, done, total, st):
        self.prog.config(value=done)
        msg = f"Downloaded {done}/{total}"
        if st["fail"]:
            msg += f" ({st['fail']} failed)"
        if st["skip"]:
            msg += f" ({st['skip']} had no file URL - login may be required)"
        self.set_status(msg)


def main():
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)   # crisp on HiDPI Windows
    except Exception:
        pass
    App(root)
    root.mainloop()
