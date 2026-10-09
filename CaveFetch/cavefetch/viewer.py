"""In-app image viewer window: fit/100% zoom, pan, prev/next, tag sidebar.
Supports static images, animated GIFs, and in-window video playback
with a seek bar, volume slider, and mute.
"""
import io
import math
import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from PIL import Image, ImageSequence, ImageTk

from .api import BASE

RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS
IMG_EXT = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
VIDEO_EXT = {"webm", "mp4", "mov", "avi", "mkv"}
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
        self.zoom_scale = 1.0
        self.min_scale = 0.05
        self.max_scale = 10.0
        self._zoom_step = 1.15
        self.note = ""
        self.msg = ""
        self._rz = None
        self._resample_fast = getattr(Image, "Resampling", Image).BILINEAR
        self._crop_bounds = None
        self._pan_job = None
        # Animation support (GIF / animated WebP)
        self.frames = None          # list[Image] or None
        self.frame_durs = None      # list[int] ms
        self.frame_idx = 0
        self.anim_job = None
        self.is_anim = False
        self.is_video = False
        self.video_url = None
        self.video_state = ""          # buffering | playing | paused | error
        self.video_playing = False
        self.video_job = None
        self._video_stop = None
        self._video_pause = threading.Event()
        self._video_q = queue.Queue(maxsize=1)
        self._video_hold = False
        self.video_duration = 0.0      # seconds, 0 until the container reports it
        self.video_position = 0.0
        self._seek_to = None
        self._seek_lock = threading.Lock()
        self._scrubbing = False
        self._scrub_frac = 0.0
        self._wall_t0 = None
        self._wall_pos0 = 0.0
        self._frame_epoch = 0
        self.video_muted = False
        self._audio_gain = 1.0

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

        # Zoom controls group in toolbar
        zbar = ttk.Frame(bar)
        zbar.pack(side="left", padx=12)
        ttk.Button(zbar, text="-", width=3, command=self.zoom_out, style="Tool.TButton").pack(side="left", padx=1)
        self.zoom_lbl = ttk.Label(zbar, text="100%", width=6, anchor="center")
        self.zoom_lbl.pack(side="left", padx=2)
        ttk.Button(zbar, text="+", width=3, command=self.zoom_in, style="Tool.TButton").pack(side="left", padx=1)
        self.fit_btn = ttk.Button(zbar, text="Fit", width=5, command=self.toggle_fit, style="Tool.TButton")
        self.fit_btn.pack(side="left", padx=3)
        ttk.Button(zbar, text="1:1", width=4, command=self.zoom_reset, style="Tool.TButton").pack(side="left", padx=1)

        self.fav_btn = ttk.Button(bar, text="★ Favorite", command=self.toggle_favorite, style="Tool.TButton")
        self.fav_btn.pack(side="right", padx=(4, 0))
        for text, cmd in (("Open in browser", self.open_browser), ("Info", self.toggle_info_collapse),
                          ("Tags", self.toggle_tags), ("Download", self.download),
                          ("Load original", self.load_original)):
            ttk.Button(bar, text=text, command=cmd, style="Tool.TButton").pack(side="right", padx=(4, 0))
        self.play_btn = ttk.Button(bar, text="▶ Play", command=self.toggle_video, style="Tool.TButton")
        # Play button is packed only when a video is shown (see _update_info / render)

        self.info = ttk.Label(w, text="", padding=(8, 4))
        self.info.pack(side="bottom", fill="x")

        # Video transport: seek bar + mute + volume. Packed only while a video is open.
        self.media_bar = ttk.Frame(w, padding=(8, 3))
        self.time_cur = ttk.Label(self.media_bar, text="0:00", width=8, anchor="e")
        self.time_cur.pack(side="left")
        # Filled portion is playback position / video length. Not a free-running slider.
        self.progress = tk.Canvas(self.media_bar, height=18, highlightthickness=0,
                                  borderwidth=0, cursor="hand2")
        self.progress.pack(side="left", fill="x", expand=True, padx=8)
        self.progress.bind("<ButtonPress-1>", self._progress_press)
        self.progress.bind("<B1-Motion>", self._progress_drag)
        self.progress.bind("<ButtonRelease-1>", self._progress_release)
        self.progress.bind("<Configure>", lambda _e: self._paint_progress())
        self.time_dur = ttk.Label(self.media_bar, text="0:00", width=8, anchor="w")
        self.time_dur.pack(side="left")
        self.mute_btn = ttk.Button(self.media_bar, text="Mute", width=8,
                                   command=self.toggle_mute, style="Tool.TButton")
        self.mute_btn.pack(side="left", padx=(12, 4))
        ttk.Label(self.media_bar, text="Vol").pack(side="left", padx=(4, 2))
        self.vol_scale = ttk.Scale(self.media_bar, from_=0, to=100, orient="horizontal",
                                   length=110, command=self._on_volume)
        self.vol_scale.pack(side="left", padx=(0, 2))
        self.vol_scale.set(100)

        body = self.body = ttk.Frame(w)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        # Collapsable Information Tab in bottom-right corner of canvas
        self.info_panel = tk.Frame(self.canvas, relief="groove", borderwidth=1)
        self.info_collapsed = False
        self.source_url = ""

        self.info_hdr = tk.Frame(self.info_panel, cursor="hand2")
        self.info_hdr.pack(fill="x")
        self.info_hdr.bind("<Button-1>", lambda _e: self.toggle_info_collapse())

        self.info_hdr_title = tk.Label(self.info_hdr, text="ℹ Image Info",
                                       font=("Segoe UI", 9, "bold"), cursor="hand2")
        self.info_hdr_title.pack(side="left", padx=8, pady=4)
        self.info_hdr_title.bind("<Button-1>", lambda _e: self.toggle_info_collapse())

        self.info_toggle_btn = tk.Label(self.info_hdr, text="▾",
                                        font=("Segoe UI", 10, "bold"), cursor="hand2")
        self.info_toggle_btn.pack(side="right", padx=8, pady=4)
        self.info_toggle_btn.bind("<Button-1>", lambda _e: self.toggle_info_collapse())

        self.info_body = tk.Frame(self.info_panel, padx=10, pady=8)
        self.info_body.pack(fill="both", expand=True)

        self._info_lbls = []
        for r, title in enumerate(["Artist:", "Dimensions:", "File type:", "Source:", "Favorite:"]):
            l = tk.Label(self.info_body, text=title, font=("Segoe UI", 8, "bold"), anchor="w")
            l.grid(row=r, column=0, sticky="w", pady=2)
            self._info_lbls.append(l)

        self.info_artist_val = tk.Label(self.info_body, text="—", font=("Segoe UI", 8),
                                        anchor="w", fg="#d4a017", cursor="hand2")
        self.info_artist_val.grid(row=0, column=1, sticky="w", padx=(6, 0), pady=2)
        self.info_artist_val.bind("<Button-1>", self._on_artist_click)

        self.info_dim_val = tk.Label(self.info_body, text="—", font=("Segoe UI", 8), anchor="w")
        self.info_dim_val.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=2)

        self.info_type_val = tk.Label(self.info_body, text="—", font=("Segoe UI", 8), anchor="w")
        self.info_type_val.grid(row=2, column=1, sticky="w", padx=(6, 0), pady=2)

        self.info_src_val = tk.Label(self.info_body, text="—", font=("Segoe UI", 8, "underline"),
                                     anchor="w", fg="#3b82f6", cursor="hand2")
        self.info_src_val.grid(row=3, column=1, sticky="w", padx=(6, 0), pady=2)
        self.info_src_val.bind("<Button-1>", self._on_source_click)

        self.info_fav_val = tk.Label(self.info_body, text="—", font=("Segoe UI", 8, "bold"),
                                     anchor="w", fg="#f59e0b", cursor="hand2")
        self.info_fav_val.grid(row=4, column=1, sticky="w", padx=(6, 0), pady=2)
        self.info_fav_val.bind("<Button-1>", lambda _e: self.toggle_favorite())

        self.info_panel.place(relx=1.0, rely=1.0, x=-12, y=-12, anchor="se")

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
        self.canvas.bind("<B1-Motion>", self._on_pan)
        self.canvas.bind("<ButtonRelease-1>", self._on_pan_release)
        self.canvas.bind("<ButtonPress-2>", lambda e: (self.canvas.focus_set(),
                                                       self.canvas.scan_mark(e.x, e.y)))
        self.canvas.bind("<B2-Motion>", self._on_pan)
        self.canvas.bind("<ButtonRelease-2>", self._on_pan_release)
        self.canvas.bind("<ButtonPress-3>", lambda e: (self.canvas.focus_set(),
                                                       self.canvas.scan_mark(e.x, e.y)))
        self.canvas.bind("<B3-Motion>", self._on_pan)
        self.canvas.bind("<ButtonRelease-3>", self._on_pan_release)
        self.canvas.bind("<MouseWheel>", self._wheel)
        w.bind("<Left>", lambda _e: self.prev())
        w.bind("<Right>", lambda _e: self.next())
        w.bind("<Escape>", lambda _e: self.hide())
        w.bind("<space>", lambda _e: self.toggle_video())
        w.bind("<f>", lambda _e: self.toggle_fit())
        w.bind("<plus>", lambda _e: self.zoom_in())
        w.bind("<equal>", lambda _e: self.zoom_in())
        w.bind("<KP_Add>", lambda _e: self.zoom_in())
        w.bind("<minus>", lambda _e: self.zoom_out())
        w.bind("<underscore>", lambda _e: self.zoom_out())
        w.bind("<KP_Subtract>", lambda _e: self.zoom_out())
        w.bind("<0>", lambda _e: self.zoom_reset())
        w.bind("<KP_0>", lambda _e: self.zoom_reset())
        w.bind("<Control-0>", lambda _e: self.zoom_reset())
        w.bind("<Control-plus>", lambda _e: self.zoom_in())
        w.bind("<Control-equal>", lambda _e: self.zoom_in())
        w.bind("<Control-minus>", lambda _e: self.zoom_out())
        w.bind("<v>", lambda _e: self.toggle_favorite())
        w.bind("<V>", lambda _e: self.toggle_favorite())

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

        # Style collapsable info panel
        self.info_panel.configure(bg=c["panel"])
        self.info_hdr.configure(bg=c["panel"])
        self.info_hdr_title.configure(bg=c["panel"], fg=c["fg"])
        self.info_toggle_btn.configure(bg=c["panel"], fg=c["muted"])
        self.info_body.configure(bg=c["entry"])
        for lbl in getattr(self, "_info_lbls", []):
            lbl.configure(bg=c["entry"], fg=c["muted"])
        self.info_artist_val.configure(bg=c["entry"], fg="#d4a017")
        self.info_dim_val.configure(bg=c["entry"], fg=c["fg"])
        self.info_type_val.configure(bg=c["entry"], fg=c["fg"])
        self.info_src_val.configure(bg=c["entry"], fg=c["accent"])
        if hasattr(self, "info_fav_val"):
            self.info_fav_val.configure(bg=c["entry"])
        prog = getattr(self, "progress", None)
        if prog is not None:
            try:
                prog.configure(bg=c["bg"])
            except tk.TclError:
                pass
            self._paint_progress()

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
        self._stop_anim()
        self._stop_video()
        if getattr(self, "_pan_job", None) is not None:
            try:
                self.win.after_cancel(self._pan_job)
            except Exception:
                pass
            self._pan_job = None
        self._crop_bounds = None
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

    def _stop_anim(self):
        """Cancel any running frame animation."""
        if self.anim_job is not None:
            try:
                self.win.after_cancel(self.anim_job)
            except Exception:
                pass
            self.anim_job = None

    def _open_current(self, original=False):
        tiles = self.app.tiles
        pid, _ = tiles[self.index]
        self.pid, self.post = pid, self.app.posts[pid]
        self.gen += 1
        gen = self.gen
        self._stop_anim()
        self._stop_video()
        if getattr(self, "_pan_job", None) is not None:
            try:
                self.win.after_cancel(self._pan_job)
            except Exception:
                pass
            self._pan_job = None
        self._crop_bounds = None
        self.pil = None
        self.frames = None
        self.frame_durs = None
        self.frame_idx = 0
        self.is_anim = False
        self.is_video = False
        self.video_url = None
        self.video_state = ""
        self._video_hold = False
        self._reset_transport()
        self.fit = True
        self.pos_lbl.config(text=f"{self.index + 1} / {len(tiles)}")
        self.win.title(f"CaveFetch - #{pid}")
        self._fill_tags()
        url, self.note = self._pick_url(original)
        self._update_info()
        self._update_favorite_btn()
        if self.is_video and self.video_url:
            self.video_state = "buffering"
            self.play_btn.config(text="…")
            self._video_ready(gen, self.video_url)
        if not url:
            if self.is_video:
                self._center_msg("Buffering video...")
                return
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
        if ext in VIDEO_EXT:
            self.is_video = True
            self.video_url = f.get("url") or sample.get("url")
            return (sample.get("url") or preview,
                    "   (video)")
        # flash or unknown: show a still
        return (sample.get("url") or preview,
                f"   ({ext} file shown as still image - use 'Open in browser' to play)")

    def _fetch(self, gen, url):
        try:
            data = self.app.api.fetch_bytes(url)
            img = Image.open(io.BytesIO(data))
            img.load()
            frames = []
            durs = []
            if getattr(img, "is_animated", False) and getattr(img, "n_frames", 1) > 1:
                for frame in ImageSequence.Iterator(img):
                    f = frame.copy()
                    if f.mode not in ("RGB", "RGBA"):
                        f = f.convert("RGBA")
                    frames.append(f)
                    # duration in ms; some GIFs store 0 → treat as 100 ms
                    d = frame.info.get("duration", 100)
                    durs.append(max(20, d or 100))
                img = frames[0]
            else:
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGBA")
                frames = [img]
                durs = [100]
            self.app.ui(self._loaded, gen, img, frames, durs, None)
        except Exception as ex:
            self.app.ui(self._loaded, gen, None, None, None, ex)

    def _loaded(self, gen, img, frames, durs, err):
        if gen != self.gen:
            return
        if err:
            self._center_msg(f"Could not load image:\n{err}")
            return
        self.pil = img
        self.frames = frames
        self.frame_durs = durs
        self.frame_idx = 0
        self.is_anim = bool(frames and len(frames) > 1)
        self.msg = ""
        self.render()
        if self.is_anim:
            self._schedule_anim()

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

    def render(self, keep_scroll=False):
        if self.pil is None:
            return
        # Use the current animation frame when available
        src = self.pil
        if self.is_anim and self.frames:
            src = self.frames[self.frame_idx]
        cw, ch = max(self.canvas.winfo_width(), 1), max(self.canvas.winfo_height(), 1)
        iw, ih = src.size
        if self.fit:
            s = min(cw / iw, ch / ih, 1.0)
            self.zoom_scale = s
        else:
            s = self.zoom_scale
        w, h = max(1, int(iw * s)), max(1, int(ih * s))

        # Resample filter selection:
        # At magnification (s >= 1.0), NEAREST is instant and crisp without blur/ringing.
        # Downscaling uses fast BILINEAR for real-time responsiveness.
        if self.video_state == "playing":
            resample = self._resample_fast
        elif s >= 1.0:
            resample = getattr(Image, "Resampling", Image).NEAREST
        else:
            resample = self._resample_fast

        img_x = max((cw - w) // 2, 0)
        img_y = max((ch - h) // 2, 0)

        # High-zoom viewport optimization:
        # If output exceeds 2 megapixels and zoomed in (s > 1.0),
        # only crop and resize the region visible in the canvas viewport (+ generous buffer margin).
        MAX_DIRECT_PIXELS = 2_000_000
        if w * h > MAX_DIRECT_PIXELS and s > 1.0:
            vx0 = self.canvas.canvasx(0)
            vy0 = self.canvas.canvasy(0)
            vx1 = self.canvas.canvasx(cw)
            vy1 = self.canvas.canvasy(ch)
            margin = 400

            vis_x0 = max(img_x, vx0 - margin)
            vis_y0 = max(img_y, vy0 - margin)
            vis_x1 = min(img_x + w, vx1 + margin)
            vis_y1 = min(img_y + h, vy1 + margin)

            if vis_x1 > vis_x0 and vis_y1 > vis_y0:
                src_x0 = max(0, min(iw, int((vis_x0 - img_x) / s)))
                src_y0 = max(0, min(ih, int((vis_y0 - img_y) / s)))
                src_x1 = max(src_x0 + 1, min(iw, int(math.ceil((vis_x1 - img_x) / s))))
                src_y1 = max(src_y0 + 1, min(ih, int(math.ceil((vis_y1 - img_y) / s))))

                crop_w = max(1, int(round((src_x1 - src_x0) * s)))
                crop_h = max(1, int(round((src_y1 - src_y0) * s)))

                crop = src.crop((src_x0, src_y0, src_x1, src_y1))
                img = crop if (crop_w, crop_h) == (src_x1 - src_x0, src_y1 - src_y0) else crop.resize((crop_w, crop_h), resample)
                place_x = img_x + int(round(src_x0 * s))
                place_y = img_y + int(round(src_y0 * s))
                self._crop_bounds = (vis_x0, vis_y0, vis_x1, vis_y1)
            else:
                img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
                place_x, place_y = img_x, img_y
                self._crop_bounds = None
        else:
            self._crop_bounds = None
            img = src if (w, h) == (iw, ih) else src.resize((w, h), resample)
            place_x, place_y = img_x, img_y

        self.photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(place_x, place_y, anchor="nw", image=self.photo)
        self.canvas.configure(scrollregion=(0, 0, max(w, cw), max(h, ch)))
        if not keep_scroll:
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
        pct = int(round(s * 100))
        self.zoom_lbl.config(text=f"{pct}%")
        self.fit_btn.config(text="Fit" if not self.fit else "100%")
        if self.is_video and self.video_state == "paused":
            self.canvas.create_text(cw / 2, ch - 28, text="Paused",
                                    fill="#ffffff", font=("Segoe UI", 11, "bold"))
        elif self.is_video and self.video_state == "buffering":
            self.canvas.create_text(cw / 2, ch - 28, text="Buffering video…",
                                    fill="#ffffff", font=("Segoe UI", 11, "bold"))

    def _on_pan(self, e):
        self.canvas.scan_dragto(e.x, e.y, gain=1)
        if getattr(self, "_crop_bounds", None):
            self._schedule_pan_crop()

    def _on_pan_release(self, _e):
        if getattr(self, "_pan_job", None) is not None:
            try:
                self.win.after_cancel(self._pan_job)
            except Exception:
                pass
            self._pan_job = None
        if getattr(self, "_crop_bounds", None):
            self.render(keep_scroll=True)

    def _schedule_pan_crop(self):
        if getattr(self, "_pan_job", None) is not None:
            return
        self._pan_job = self.win.after(80, self._do_pan_crop)

    def _do_pan_crop(self):
        self._pan_job = None
        if getattr(self, "_crop_bounds", None):
            cw = max(self.canvas.winfo_width(), 1)
            ch = max(self.canvas.winfo_height(), 1)
            vx0 = self.canvas.canvasx(0)
            vy0 = self.canvas.canvasy(0)
            vx1 = self.canvas.canvasx(cw)
            vy1 = self.canvas.canvasy(ch)
            cb = self._crop_bounds
            threshold = 100
            if vx0 < cb[0] + threshold or vy0 < cb[1] + threshold or vx1 > cb[2] - threshold or vy1 > cb[3] - threshold:
                self.render(keep_scroll=True)

    def toggle_fit(self):
        self.fit = not self.fit
        if not self.fit:
            self.zoom_scale = 1.0
        self.render(keep_scroll=False)

    def _schedule_anim(self):
        """Schedule the next animation frame."""
        self._stop_anim()
        if not self.is_anim or not self.frame_durs:
            return
        dur = self.frame_durs[self.frame_idx]
        self.anim_job = self.win.after(dur, self._next_frame)

    def _next_frame(self):
        self.anim_job = None
        if not self.is_anim or self.pil is None or not self.frames:
            return
        self.frame_idx = (self.frame_idx + 1) % len(self.frames)
        # Keep current scroll/zoom position while advancing frames
        self.render(keep_scroll=True)
        self._schedule_anim()

    def toggle_video(self):
        """Play or pause the clip that is already decoding inside the viewer."""
        if not self.is_video:
            return
        if self.video_state == "playing":
            self._video_pause.set()
            self.video_state = "paused"
            self.play_btn.config(text="▶ Play")
            if self.pil is not None:
                self.render(keep_scroll=True)
        elif self.video_state == "paused":
            self._video_pause.clear()
            self.video_state = "playing"
            self.play_btn.config(text="❚❚ Pause")
        elif self.video_state == "buffering":
            self._video_hold = not self._video_hold
            self.play_btn.config(text="▶ Play" if self._video_hold else "…")

    def _stop_video(self):
        """Stop the decode thread."""
        self.video_playing = False
        if self._video_stop is not None:
            self._video_stop.set()
        if getattr(self, "_seek_lock", None) is not None:
            with self._seek_lock:
                self._seek_to = None
        if self.video_job is not None:
            try:
                self.win.after_cancel(self.video_job)
            except Exception:
                pass
            self.video_job = None

    def _video_fail(self, gen, message):
        if gen != self.gen:
            return
        self.video_playing = False
        self.video_state = "error"
        self.play_btn.config(text="▶ Play")
        if self.pil is None:
            self._center_msg(f"Could not play video:\n{message}")
        else:
            self.note = f"   (video failed: {message})"
            self._update_info()

    def _video_ready(self, gen, source):
        if gen != self.gen:
            return
        try:
            import av  # noqa: F401
        except ImportError:
            self._video_fail(gen, "Video playback needs the 'av' package.\nRun: pip install av")
            return
        self._video_stop = threading.Event()
        self._video_pause = threading.Event()
        if self._video_hold:
            self._video_pause.set()
        q = queue.Queue(maxsize=2)
        self._video_q = q
        self.video_playing = True
        self.video_state = "paused" if self._video_hold else "playing"
        self.play_btn.config(text="▶ Play" if self._video_hold else "❚❚ Pause")
        self.note = "   (video)"
        self._wall_t0 = time.perf_counter()
        self._wall_pos0 = 0.0
        self._update_info()
        threading.Thread(
            target=self._decode_video,
            args=(gen, source, self._video_stop, self._video_pause, q),
            daemon=True,
        ).start()
        self._pump_video()

    def _pump_video(self):
        if not self.video_playing:
            return
        self._paint_progress()
        img = None
        gen = None
        try:
            item = self._video_q.get_nowait()
        except queue.Empty:
            item = None
        if item is not None:
            gen = item[0]
            img = item[1]
            epoch = item[2] if len(item) > 2 else None
            if img is None:
                self.video_playing = False
                if self.video_state == "playing":
                    self.video_state = "paused"
                    self.play_btn.config(text="▶ Play")
                self._paint_progress()
                return
            if epoch is not None and epoch != self._frame_epoch:
                img = None
        if img is not None and gen == self.gen:
            size_changed = self.pil is None or self.pil.size != img.size
            self.pil = img
            self.msg = ""
            self.render(keep_scroll=not size_changed)
        if self.video_playing and gen != "end":
            self.video_job = self.win.after(15, self._pump_video)

    def _put_frame(self, out_q, gen, img, stop_event):
        epoch = self._frame_epoch
        while not stop_event.is_set():
            if self._seek_pending():
                return
            try:
                out_q.put((gen, img, epoch), timeout=0.2)
                return
            except queue.Full:
                continue

    def _wait_pts(self, frame, clock, pause_event, stop_event):
        """Pace a frame. Returns False if playback should interrupt for a seek or stop."""
        if self._seek_pending():
            return False
        if frame.pts is None or frame.time_base is None:
            while pause_event.is_set() and not stop_event.is_set():
                if self._seek_pending():
                    return False
                time.sleep(0.03)
            return not stop_event.is_set()
        pts = float(frame.pts * frame.time_base)
        if clock["origin"] is None:
            clock["origin"] = pts
        while not stop_event.is_set():
            if self._seek_pending():
                return False
            if pause_event.is_set():
                t0 = time.perf_counter()
                while pause_event.is_set() and not stop_event.is_set():
                    if self._seek_pending():
                        return False
                    time.sleep(0.03)
                clock["start"] += time.perf_counter() - t0
            target = clock["start"] + (pts - clock["origin"])
            remaining = target - time.perf_counter()
            if remaining <= 0:
                return True
            time.sleep(min(0.02, remaining))
        return False

    def _open_audio_out(self, container):
        """Return (audio_stream, player, resampler) or Nones if audio can't play."""
        if not container.streams.audio:
            return None, None, None
        try:
            import sounddevice as sd
        except Exception:
            return None, None, None
        try:
            import av
            player = sd.OutputStream(samplerate=48000, channels=2, dtype="int16")
            player.start()
            resampler = av.audio.resampler.AudioResampler(
                format="s16", layout="stereo", rate=48000)
        except Exception:
            return None, None, None
        return container.streams.audio[0], player, resampler

    def _write_audio(self, player, resampler, frame, pause_event, stop_event):
        try:
            frames = resampler.resample(frame)
        except Exception:
            return False
        for out in frames:
            if stop_event.is_set():
                return False
            while pause_event.is_set() and not stop_event.is_set():
                if self._seek_pending():
                    return "seek"
                time.sleep(0.03)
            try:
                pcm = out.to_ndarray().reshape(-1, 2)
                pcm = self._apply_gain(pcm)
                player.write(pcm)
            except Exception:
                return False
        return True

    def _decode_video(self, gen, source, stop_event, pause_event, out_q):
        import av
        try:
            ua = "CaveFetch/1.1"
            try:
                ua = self.app.api.cdn.headers.get("User-Agent") or ua
            except Exception:
                pass
            options = {
                "user_agent": ua,
                "rw_timeout": "20000000",
                "reconnect": "1",
                "reconnect_streamed": "1",
                "reconnect_delay_max": "2",
                "probesize": "8000000",
                "analyzeduration": "8000000",
            }
            while not stop_event.is_set():
                container = av.open(source, options=options)
                player = None
                demux = None
                try:
                    vstream = container.streams.video[0]
                    try:
                        vstream.thread_type = "AUTO"
                    except Exception:
                        pass
                    dur = self._media_duration(container, vstream)
                    if dur > 0 and gen == self.gen:
                        self.video_duration = dur
                    astream, player, resampler = self._open_audio_out(container)
                    streams = (vstream, astream) if astream is not None else (vstream,)
                    clock = {"start": None, "origin": None}
                    got = False
                    audio_ok = player is not None
                    skip_until = None
                    force_frame = False
                    demux = container.demux(streams)
                    while not stop_event.is_set():
                        seek_to = self._take_seek()
                        if seek_to is not None:
                            try:
                                demux.close()
                            except Exception:
                                pass
                            player, resampler = self._seek_playback(
                                container, vstream, astream, player, resampler, seek_to)
                            audio_ok = player is not None and resampler is not None
                            clock = {"start": None, "origin": None}
                            skip_until = seek_to
                            force_frame = True
                            self.video_position = seek_to
                            self._wall_t0 = time.perf_counter()
                            self._wall_pos0 = seek_to
                            demux = container.demux(streams)
                            continue
                        try:
                            packet = next(demux)
                        except StopIteration:
                            break
                        interrupted = False
                        for frame in packet.decode():
                            if stop_event.is_set() or self._seek_pending():
                                interrupted = True
                                break
                            is_audio = astream is not None and packet.stream is astream
                            stream = astream if is_audio else vstream
                            pts = self._media_pts(frame, stream)
                            if skip_until is not None and pts is not None and pts + 0.04 < skip_until:
                                continue
                            if is_audio:
                                if audio_ok:
                                    status = self._write_audio(
                                        player, resampler, frame, pause_event, stop_event)
                                    if status == "seek":
                                        interrupted = True
                                        break
                                    if not status:
                                        audio_ok = False
                                continue
                            if pause_event.is_set() and not force_frame:
                                while pause_event.is_set() and not stop_event.is_set():
                                    if self._seek_pending():
                                        break
                                    time.sleep(0.03)
                                if stop_event.is_set() or self._seek_pending():
                                    interrupted = True
                                    break
                            force_frame = False
                            if not pause_event.is_set():
                                if clock["start"] is None:
                                    clock["start"] = time.perf_counter()
                                if not self._wait_pts(frame, clock, pause_event, stop_event):
                                    interrupted = True
                                    break
                            if skip_until is not None:
                                skip_until = None
                            if gen == self.gen:
                                self._note_position(pts, pause_event)
                            img = frame.to_image()
                            if img.mode not in ("RGB", "RGBA"):
                                img = img.convert("RGB")
                            self._put_frame(out_q, gen, img, stop_event)
                            got = True
                        if interrupted:
                            continue
                    if stop_event.is_set() or not got:
                        break
                finally:
                    try:
                        if demux is not None:
                            demux.close()
                    except Exception:
                        pass
                    if player is not None:
                        try:
                            player.stop()
                            player.close()
                        except Exception:
                            pass
                    container.close()
        except Exception as ex:
            self.app.ui(self._video_fail, gen, str(ex))
        finally:
            try:
                out_q.put((gen, None), timeout=0.2)
            except queue.Full:
                pass

    # ---- transport (seek bar + volume)
    @staticmethod
    def _fmt_time(seconds):
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            seconds = 0.0
        if seconds != seconds or seconds in (float("inf"), float("-inf")):
            seconds = 0.0
        seconds = int(max(0, min(seconds, 12 * 3600)))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    @staticmethod
    def _sane_duration(seconds):
        """A real clip length. Rejects missing, NaN, and multi-day sentinel values."""
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            return None
        if seconds != seconds or seconds in (float("inf"), float("-inf")):
            return None
        if seconds <= 0.05 or seconds >= 12 * 3600:
            return None
        return seconds

    @staticmethod
    def _parse_duration_tag(value):
        """Parse 'HH:MM:SS.sss', 'MM:SS', or a plain seconds string."""
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() in ("n/a", "unknown", "inf", "infinite"):
            return None
        if ":" not in text:
            try:
                return float(text)
            except ValueError:
                return None
        parts = text.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        if len(nums) == 3:
            return nums[0] * 3600 + nums[1] * 60 + nums[2]
        if len(nums) == 2:
            return nums[0] * 60 + nums[1]
        if len(nums) == 1:
            return nums[0]
        return None

    @staticmethod
    def _pick_duration(candidates):
        """Choose one finite length. Drops outliers so a bad timestamp can't make it endless."""
        vals = []
        for raw in candidates:
            sane = Viewer._sane_duration(raw)
            if sane:
                vals.append(sane)
        if not vals:
            return 0.0
        vals.sort()
        mid = vals[len(vals) // 2]
        cluster = [v for v in vals if mid / 8.0 <= v <= mid * 8.0]
        return max(cluster) if cluster else mid

    @staticmethod
    def _fit_seconds(pts, duration):
        """Map a timestamp onto the clip length.

        A missing time base leaves the raw tick count (often millions). If that
        number can't sit inside the real duration, try the usual media scales.
        """
        try:
            pts = float(pts)
        except (TypeError, ValueError):
            return None
        if pts != pts or pts < 0 or pts in (float("inf"), float("-inf")):
            return None
        try:
            dur = float(duration or 0)
        except (TypeError, ValueError):
            dur = 0.0
        if dur != dur or dur < 0 or dur in (float("inf"), float("-inf")):
            dur = 0.0
        if dur > 0.05:
            if pts <= dur + 1.0:
                return pts
            # Way too big to be a position in this clip: raw ticks with no time base.
            if pts > 10000:
                for scale in (1000, 1000000, 90000, 60000, 48000, 44100, 30000, 25000, 24000, 16000):
                    scaled = pts / float(scale)
                    if 0 <= scaled <= dur + 1.0:
                        return scaled
            return None
        if pts <= 3 * 3600:
            return pts
        return None

    def _add_meta_durations(self, meta, found):
        if not meta:
            return
        try:
            items = list(meta.items())
        except Exception:
            return
        for key, val in items:
            if "duration" not in str(key).lower():
                continue
            sane = self._sane_duration(self._parse_duration_tag(val))
            if sane:
                found.append(sane)

    def _media_pts(self, frame, stream):
        """Seconds from the start of the clip, or None if this timestamp can't be one."""
        if frame is None or frame.pts is None:
            return None
        bases = []
        stream_tb = getattr(stream, "time_base", None) if stream is not None else None
        if stream_tb:
            bases.append(stream_tb)
        frame_tb = getattr(frame, "time_base", None)
        if frame_tb and frame_tb not in bases:
            bases.append(frame_tb)
        dur = float(self.video_duration or 0)
        for tb in bases:
            try:
                secs = float(frame.pts * tb)
            except Exception:
                continue
            if (stream is not None and stream_tb and tb == stream_tb
                    and getattr(stream, "start_time", None)):
                try:
                    start = float(stream.start_time * stream_tb)
                    if 0 < start < 12 * 3600:
                        secs -= start
                except Exception:
                    pass
            fitted = self._fit_seconds(max(0.0, secs), dur)
            if fitted is not None:
                return fitted
        return self._fit_seconds(frame.pts, dur)

    def _note_position(self, pts, pause_event):
        """Remember where playback is, in seconds, never as a raw tick count."""
        if pts is not None:
            self.video_position = pts
            self._wall_t0 = time.perf_counter()
            self._wall_pos0 = pts
            return
        dur = float(self.video_duration or 0)
        if dur <= 0 or self._wall_t0 is None or pause_event.is_set():
            return
        elapsed = time.perf_counter() - self._wall_t0
        self.video_position = min(dur, max(0.0, self._wall_pos0 + elapsed))

    def _media_duration(self, container, vstream):
        import av
        found = []

        def add_tb(value, tb):
            if not value or not tb or value <= 0 or value >= 2 ** 62:
                return
            try:
                sane = self._sane_duration(float(value * tb))
            except Exception:
                return
            if sane:
                found.append(sane)

        try:
            add_tb(container.duration, av.time_base)
        except Exception:
            pass
        streams = []
        if vstream is not None:
            streams.append(vstream)
        try:
            streams.extend(list(container.streams.audio))
        except Exception:
            pass
        for stream in streams:
            try:
                add_tb(stream.duration, stream.time_base)
            except Exception:
                pass
            try:
                frames = getattr(stream, "frames", 0) or 0
                rate = getattr(stream, "average_rate", None) or getattr(stream, "base_rate", None)
                if frames and rate:
                    sane = self._sane_duration(float(frames) / float(rate))
                    if sane:
                        found.append(sane)
            except Exception:
                pass
            self._add_meta_durations(getattr(stream, "metadata", None), found)
        self._add_meta_durations(getattr(container, "metadata", None), found)
        try:
            size = ((self.post or {}).get("file") or {}).get("size") or 0
            br = getattr(container, "bit_rate", 0) or 0
            if not br and vstream is not None:
                br = getattr(vstream, "bit_rate", 0) or 0
            if size and br and br > 1000:
                sane = self._sane_duration((float(size) * 8.0) / float(br))
                if sane:
                    found.append(sane)
        except Exception:
            pass
        return self._pick_duration(found)

    def _new_resampler(self):
        import av
        return av.audio.resampler.AudioResampler(format="s16", layout="stereo", rate=48000)

    def _restart_audio_player(self, player):
        if player is not None:
            try:
                player.stop()
            except Exception:
                pass
            try:
                player.close()
            except Exception:
                pass
        try:
            import sounddevice as sd
            new_player = sd.OutputStream(samplerate=48000, channels=2, dtype="int16")
            new_player.start()
            return new_player
        except Exception:
            return None

    def _seek_playback(self, container, vstream, astream, player, resampler, seconds):
        """Seek to `seconds` and drop decoded audio so the new position stays in sync."""
        target = max(0.0, float(seconds))
        try:
            import av
            container.seek(int(target / av.time_base), backward=True, any_frame=False)
        except Exception:
            try:
                if vstream is not None and vstream.time_base:
                    container.seek(int(target / vstream.time_base),
                                   stream=vstream, backward=True, any_frame=False)
            except Exception:
                pass
        if astream is None:
            return player, resampler
        player = self._restart_audio_player(player)
        try:
            resampler = self._new_resampler()
        except Exception:
            if player is not None:
                try:
                    player.stop()
                    player.close()
                except Exception:
                    pass
            return None, None
        if player is None:
            return None, None
        return player, resampler

    def _reset_transport(self):
        """Clear the seek bar for a newly opened post. Volume is left as the user set it."""
        self.video_duration = 0.0
        self.video_position = 0.0
        self._scrubbing = False
        if getattr(self, "_seek_lock", None) is not None:
            with self._seek_lock:
                self._seek_to = None
                self._frame_epoch = getattr(self, "_frame_epoch", 0) + 1
        self._scrub_frac = 0.0
        self._wall_t0 = None
        self._wall_pos0 = 0.0
        bar = getattr(self, "progress", None)
        if bar is not None and hasattr(bar, "delete"):
            try:
                bar.delete("all")
            except Exception:
                pass
        cur = getattr(self, "time_cur", None)
        if cur is not None:
            cur.config(text="0:00")
        dur_lbl = getattr(self, "time_dur", None)
        if dur_lbl is not None:
            dur_lbl.config(text="0:00")

    def _show_transport(self, show):
        bar = getattr(self, "media_bar", None)
        body = getattr(self, "body", None)
        if bar is None:
            return
        try:
            mapped = bool(bar.winfo_ismapped())
        except Exception:
            mapped = False
        if show:
            if not mapped and body is not None:
                try:
                    bar.pack(side="bottom", fill="x", before=body)
                except Exception:
                    bar.pack(side="bottom", fill="x")
        elif mapped:
            try:
                bar.pack_forget()
            except Exception:
                pass

    def _event_frac(self, event):
        bar = self.progress
        width = 1
        try:
            width = max(int(bar.winfo_width()), 1)
        except Exception:
            width = 1
        try:
            x = float(event.x)
        except (TypeError, ValueError, AttributeError):
            x = 0.0
        return max(0.0, min(1.0, x / float(width)))

    def _paint_progress(self):
        if not getattr(self, "is_video", False):
            return
        bar = getattr(self, "progress", None)
        if bar is None or not hasattr(bar, "create_rectangle"):
            return
        dur = float(self.video_duration or 0)
        if dur < 0 or dur != dur or dur == float("inf"):
            dur = 0.0
            self.video_duration = 0.0
        if self._scrubbing:
            frac = self._scrub_frac
            pos = frac * dur if dur > 0 else 0.0
        else:
            pos = float(self.video_position or 0)
            if pos != pos or pos < 0 or pos == float("inf"):
                pos = 0.0
            if dur > 0:
                pos = min(pos, dur)
                frac = pos / dur
            else:
                frac = 0.0
        try:
            width = max(int(bar.winfo_width()), 1)
            height = max(int(bar.winfo_height()), 1)
        except Exception:
            return
        colors = getattr(self, "c", None) or {}
        trough = colors.get("entry", "#0b1220")
        accent = colors.get("accent", "#3b82f6")
        bg = colors.get("bg", "#111827")
        try:
            bar.configure(bg=bg)
            bar.delete("all")
            top, bot = 6, max(height - 6, 8)
            bar.create_rectangle(0, top, width, bot, fill=trough, outline="")
            fill_w = int(round(width * max(0.0, min(1.0, frac))))
            if fill_w > 0:
                bar.create_rectangle(0, top, fill_w, bot, fill=accent, outline="")
            x = max(0, min(width, fill_w))
            bar.create_rectangle(max(0, x - 2), top - 3, min(width, x + 2), bot + 3,
                                 fill=accent, outline="")
        except Exception:
            return
        cur = getattr(self, "time_cur", None)
        dur_lbl = getattr(self, "time_dur", None)
        if cur is not None:
            cur.config(text=self._fmt_time(pos))
        if dur_lbl is not None:
            dur_lbl.config(text=self._fmt_time(dur) if dur > 0 else "--:--")

    def _progress_press(self, event):
        if not self.is_video or self.video_duration <= 0:
            return
        self._scrubbing = True
        self._scrub_frac = self._event_frac(event)
        self._paint_progress()

    def _progress_drag(self, event):
        if not self._scrubbing:
            return
        self._scrub_frac = self._event_frac(event)
        self._paint_progress()

    def _progress_release(self, event):
        if not self._scrubbing:
            return
        self._scrub_frac = self._event_frac(event)
        dur = float(self.video_duration or 0)
        self._scrubbing = False
        if dur <= 0:
            self._paint_progress()
            return
        t = max(0.0, min(dur, self._scrub_frac * dur))
        self.video_position = t
        self._wall_t0 = time.perf_counter()
        self._wall_pos0 = t
        self._paint_progress()
        self._request_seek(t)

    def _request_seek(self, seconds):
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            return
        dur = float(self.video_duration or 0)
        if dur > 0:
            seconds = min(seconds, max(0.0, dur - 0.05))
        seconds = max(0.0, seconds)
        with self._seek_lock:
            self._seek_to = seconds
            self._frame_epoch += 1
        self._drain_video_q()

    def _take_seek(self):
        with self._seek_lock:
            target = self._seek_to
            self._seek_to = None
            return target

    def _seek_pending(self):
        with self._seek_lock:
            return self._seek_to is not None

    def _drain_video_q(self):
        q = getattr(self, "_video_q", None)
        if q is None:
            return
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            pass

    def _sync_gain(self):
        if self.video_muted:
            self._audio_gain = 0.0
            return
        try:
            level = float(self.vol_scale.get())
        except Exception:
            level = 100.0
        self._audio_gain = max(0.0, min(1.0, level / 100.0))

    def _update_mute_btn(self):
        btn = getattr(self, "mute_btn", None)
        if btn is None:
            return
        btn.config(text="Unmute" if self.video_muted else "Mute")

    def toggle_mute(self):
        self.video_muted = not self.video_muted
        self._sync_gain()
        self._update_mute_btn()

    def _on_volume(self, _val=None):
        try:
            level = float(self.vol_scale.get())
        except Exception:
            level = 100.0
        if level > 0 and self.video_muted:
            self.video_muted = False
        self._sync_gain()
        self._update_mute_btn()

    def _apply_gain(self, pcm):
        gain = float(getattr(self, "_audio_gain", 1.0))
        if gain >= 0.999:
            return pcm
        try:
            import numpy as np
            if gain <= 0.001:
                return np.zeros_like(pcm)
            return np.clip(pcm.astype(np.float32) * gain, -32768, 32767).astype(np.int16)
        except Exception:
            return pcm

    def zoom_reset(self):
        """Reset to 100% (1:1 original pixel scale)."""
        self.fit = False
        self.zoom_scale = 1.0
        self.render(keep_scroll=False)

    def zoom_in(self):
        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)
        self.zoom_at(cw // 2, ch // 2, self._zoom_step)

    def zoom_out(self):
        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)
        self.zoom_at(cw // 2, ch // 2, 1.0 / self._zoom_step)

    def zoom_at(self, cursor_x, cursor_y, factor):
        """Zoom image centered on the given canvas coordinates (focal zoom)."""
        if self.pil is None:
            return
        cw, ch = max(self.canvas.winfo_width(), 1), max(self.canvas.winfo_height(), 1)
        iw, ih = self.pil.size

        if self.fit:
            current_scale = min(cw / iw, ch / ih, 1.0)
        else:
            current_scale = self.zoom_scale

        new_scale = max(self.min_scale, min(self.max_scale, current_scale * factor))
        if abs(new_scale - current_scale) < 1e-4:
            return

        cx = self.canvas.canvasx(cursor_x)
        cy = self.canvas.canvasy(cursor_y)

        cur_w = max(1, int(iw * current_scale))
        cur_h = max(1, int(ih * current_scale))
        cur_img_x = max((cw - cur_w) // 2, 0)
        cur_img_y = max((ch - cur_h) // 2, 0)

        rel_x = (cx - cur_img_x) / cur_w if cur_w > 0 else 0.5
        rel_y = (cy - cur_img_y) / cur_h if cur_h > 0 else 0.5

        self.fit = False
        self.zoom_scale = new_scale

        new_w = max(1, int(iw * new_scale))
        new_h = max(1, int(ih * new_scale))
        new_img_x = max((cw - new_w) // 2, 0)
        new_img_y = max((ch - new_h) // 2, 0)

        target_cx = new_img_x + rel_x * new_w
        target_cy = new_img_y + rel_y * new_h

        tot_w = max(new_w, cw)
        tot_h = max(new_h, ch)
        desired_scroll_x = target_cx - cursor_x
        desired_scroll_y = target_cy - cursor_y

        max_scroll_x = max(0, tot_w - cw)
        max_scroll_y = max(0, tot_h - ch)
        clamped_x = max(0, min(max_scroll_x, desired_scroll_x))
        clamped_y = max(0, min(max_scroll_y, desired_scroll_y))

        fx = clamped_x / tot_w if tot_w > 0 else 0.0
        fy = clamped_y / tot_h if tot_h > 0 else 0.0
        self.canvas.configure(scrollregion=(0, 0, tot_w, tot_h))
        self.canvas.xview_moveto(fx)
        self.canvas.yview_moveto(fy)

        self.render(keep_scroll=True)

    def _wheel(self, e):
        """Scroll wheel zooms in/out centered at cursor position."""
        if self.pil is None:
            return
        if getattr(e, "state", 0) & 0x0001:
            # Shift held: scroll horizontally
            delta = e.delta if e.delta else 0
            if delta != 0:
                self.canvas.xview_scroll(int(-delta / 120) * 3, "units")
            return

        delta = e.delta if e.delta else 0
        if delta == 0:
            return
        factor = self._zoom_step ** (delta / 120.0)
        self.zoom_at(e.x, e.y, factor)

    # ---- info + tags
    def toggle_info_collapse(self):
        """Toggle the bottom-right information tab between expanded and collapsed."""
        self.info_collapsed = not self.info_collapsed
        if self.info_collapsed:
            self.info_body.pack_forget()
            self.info_toggle_btn.config(text="▴")
            self.info_hdr_title.config(text="ℹ Info")
        else:
            self.info_body.pack(fill="both", expand=True)
            self.info_toggle_btn.config(text="▾")
            self.info_hdr_title.config(text="ℹ Image Info")

    def _on_source_click(self, _e=None):
        if getattr(self, "source_url", None):
            webbrowser.open(self.source_url)

    def _on_artist_click(self, _e=None):
        artists = ((self.post or {}).get("tags") or {}).get("artist") or []
        if artists:
            self.hide()
            self.app.search_for(artists[0])

    def _update_info(self):
        p = self.post
        f = p.get("file") or {}
        score = (p.get("score") or {}).get("total", 0)
        kb = (f.get("size") or 0) / 1024
        size_str = f"{kb/1024:.1f} MB" if kb >= 1024 else f"{kb:.0f} KB"

        # Bottom status bar summary
        is_fav = getattr(self.app, "is_favorited", lambda _p: False)(self.pid) if self.pid else False
        fav_label = "    ★ FAVORITED" if is_fav else ""
        self.info.config(text=(f"#{self.pid}    rating {str(p.get('rating', '?')).upper()}    "
                               f"score {score}    favs {p.get('fav_count', 0)}{fav_label}    "
                               f"{f.get('width')}x{f.get('height')}    {f.get('ext', '?')}    "
                               f"{size_str}{self.note}"))

        # 1. Artist name
        artists = (p.get("tags") or {}).get("artist") or []
        artist_text = ", ".join(artists) if artists else "Unknown / Anonymous"
        self.info_artist_val.config(text=artist_text)

        # 2. Image dimensions
        w = f.get("width", "?")
        h = f.get("height", "?")
        self.info_dim_val.config(text=f"{w} × {h} px")

        # 3. File type
        ext = (f.get("ext") or "?").upper()
        self.info_type_val.config(text=f"{ext} ({size_str})")

        # 4. Source of the image
        sources = p.get("sources") or []
        self.source_url = sources[0] if (sources and sources[0]) else f"{BASE}/posts/{self.pid}"
        src_disp = self.source_url
        if len(src_disp) > 30:
            src_disp = src_disp[:28] + "..."
        self.info_src_val.config(text=src_disp)

        # 5. Favorite status in info panel
        if hasattr(self, "info_fav_val"):
            self.info_fav_val.config(
                text="★ Favorited (Click to remove)" if is_fav else "☆ Not favorited (Click to add)",
                fg="#f59e0b" if is_fav else self.c["muted"]
            )

        # Show / hide the Play button and the seek/volume bar depending on media type
        play_btn = getattr(self, "play_btn", None)
        if play_btn is not None:
            if getattr(self, "is_video", False):
                if not play_btn.winfo_ismapped():
                    play_btn.pack(side="right", padx=(4, 0))
            elif play_btn.winfo_ismapped():
                play_btn.pack_forget()
        self._show_transport(bool(getattr(self, "is_video", False)))

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

    def toggle_favorite(self):
        """Toggle favorite for the current post. Syncs to the e621 website when logged in."""
        if self.pid is None:
            return
        is_fav = getattr(self.app, "is_favorited", lambda _p: False)(self.pid)
        if is_fav:
            self.fav_btn.config(text="…")
            remove_fn = getattr(self.app, "remove_favorite", lambda _p, **kw: None)
            remove_fn(self.pid, callback=lambda _s, _err: self._on_favorite_toggled())
        else:
            api_user = getattr(getattr(self.app, "api", None), "user", None)
            if not api_user:
                ask = messagebox.askyesno(
                    "e621 Website Sync",
                    "To have this favorite visible on your e621 website account, you need to log in with your API key.\n\n"
                    "Would you like to log in now?\n"
                    "(Click 'No' to save as a favorite on this computer only.)",
                    parent=self.win
                )
                if ask:
                    login_fn = getattr(self.app, "login_dialog", None)
                    if login_fn:
                        login_fn()
                    return
            self.fav_btn.config(text="…")
            add_fn = getattr(self.app, "add_favorite", lambda _p, **kw: None)
            add_fn(self.pid, callback=lambda _s, _err: self._on_favorite_toggled())

    def _on_favorite_toggled(self):
        self._update_favorite_btn()
        self._update_info()

    def _update_favorite_btn(self):
        if not hasattr(self, "fav_btn"):
            return
        is_fav = getattr(self.app, "is_favorited", lambda _p: False)(self.pid) if self.pid else False
        if is_fav:
            self.fav_btn.config(text="★ Favorited")
        else:
            self.fav_btn.config(text="☆ Favorite")
