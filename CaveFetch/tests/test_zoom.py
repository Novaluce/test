"""Unit tests for CaveFetch Viewer zooming feature and calculations."""
import sys
import unittest
from unittest.mock import MagicMock

# Mock GUI and external modules if not present in headless / non-desktop environments
for mod_name in ["tkinter", "tkinter.ttk", "PIL", "PIL.Image", "PIL.ImageTk", "requests"]:
    if mod_name not in sys.modules:
        m = MagicMock()
        sys.modules[mod_name] = m

import PIL.Image
PIL.Image.Resampling = MagicMock()
PIL.Image.Resampling.LANCZOS = 1
PIL.Image.Resampling.BILINEAR = 2

class MockImage:
    def __init__(self, size=(1000, 800)):
        self.size = size
    def resize(self, size, resample=None):
        return MockImage(size)

PIL.Image.new = lambda mode, size, color: MockImage(size)
Image = PIL.Image

from cavefetch.viewer import Viewer


class MockApp:
    def __init__(self):
        self.root = MagicMock()
        self.theme = MagicMock()
        self.theme.c = {
            "bg": "#111827", "panel": "#1f2937", "fg": "#e5e7eb", "muted": "#9ca3af",
            "entry": "#0b1220", "btn": "#374151", "btn_active": "#4b5563", "border": "#374151",
            "accent": "#3b82f6", "tile": "#0b1220", "grid_bg": "#1f2937", "viewer_bg": "#0b0f19",
        }
        self.posts = {}
        self.tiles = []


class TestZoomFeature(unittest.TestCase):
    def setUp(self):
        # We test the math and logic of zoom_at and zoom operations
        # without requiring a live X11/Windows display
        self.app = MockApp()

    def test_zoom_step_and_bounds(self):
        v = Viewer.__new__(Viewer)
        v.app = self.app
        v.c = self.app.theme.c
        v.fit = True
        v.zoom_scale = 1.0
        v.min_scale = 0.05
        v.max_scale = 10.0
        v._zoom_step = 1.15
        v.pil = Image.new("RGBA", (1000, 800), (255, 0, 0, 255))
        v.canvas = MagicMock()
        v.canvas.winfo_width.return_value = 1000
        v.canvas.winfo_height.return_value = 800
        v.canvas.canvasx = lambda x: x
        v.canvas.canvasy = lambda y: y
        v.zoom_lbl = MagicMock()
        v.fit_btn = MagicMock()

        # Render mock
        v.render = MagicMock()

        # Test wheel delta zoom in
        delta = 120
        factor = v._zoom_step ** (delta / 120.0)
        v.zoom_at(500, 400, factor)

        self.assertFalse(v.fit)
        self.assertAlmostEqual(v.zoom_scale, 1.15, places=3)

        # Test wheel delta zoom out
        delta = -120
        factor = v._zoom_step ** (delta / 120.0)
        v.zoom_at(500, 400, factor)
        self.assertAlmostEqual(v.zoom_scale, 1.0, places=3)

        # Test zoom clamp max
        v.zoom_at(500, 400, 100.0)
        self.assertEqual(v.zoom_scale, 10.0)

        # Test zoom clamp min
        v.zoom_at(500, 400, 0.0001)
        self.assertEqual(v.zoom_scale, 0.05)

    def test_zoom_shortcuts_and_reset(self):
        v = Viewer.__new__(Viewer)
        v.app = self.app
        v.c = self.app.theme.c
        v.fit = True
        v.zoom_scale = 1.0
        v.min_scale = 0.05
        v.max_scale = 10.0
        v._zoom_step = 1.15
        v.pil = Image.new("RGBA", (1000, 800), (0, 255, 0, 255))
        v.canvas = MagicMock()
        v.canvas.winfo_width.return_value = 1000
        v.canvas.winfo_height.return_value = 800
        v.canvas.canvasx = lambda x: x
        v.canvas.canvasy = lambda y: y
        v.zoom_lbl = MagicMock()
        v.fit_btn = MagicMock()
        v.render = MagicMock()

        # zoom_in
        v.zoom_in()
        self.assertAlmostEqual(v.zoom_scale, 1.15, places=3)
        self.assertFalse(v.fit)

        # zoom_reset (1:1)
        v.zoom_reset()
        self.assertEqual(v.zoom_scale, 1.0)
        self.assertFalse(v.fit)

        # toggle_fit
        v.toggle_fit()
        self.assertTrue(v.fit)

    def test_mouse_wheel_event(self):
        v = Viewer.__new__(Viewer)
        v.app = self.app
        v.c = self.app.theme.c
        v.fit = True
        v.zoom_scale = 1.0
        v.min_scale = 0.05
        v.max_scale = 10.0
        v._zoom_step = 1.15
        v.pil = Image.new("RGBA", (1000, 800), (0, 0, 255, 255))
        v.canvas = MagicMock()
        v.canvas.winfo_width.return_value = 1000
        v.canvas.winfo_height.return_value = 800
        v.canvas.canvasx = lambda x: x
        v.canvas.canvasy = lambda y: y
        v.zoom_lbl = MagicMock()
        v.fit_btn = MagicMock()
        v.render = MagicMock()

        # Event simulating scroll wheel up (+120)
        event_up = MagicMock()
        event_up.delta = 120
        event_up.x = 400
        event_up.y = 300
        event_up.state = 0

        v._wheel(event_up)
        self.assertAlmostEqual(v.zoom_scale, 1.15, places=3)

        # Event simulating scroll wheel down (-120)
        event_down = MagicMock()
        event_down.delta = -120
        event_down.x = 400
        event_down.y = 300
        event_down.state = 0

        v._wheel(event_down)
        self.assertAlmostEqual(v.zoom_scale, 1.0, places=3)

        # Shift + Wheel should scroll horizontal instead of zoom
        event_shift = MagicMock()
        event_shift.delta = 120
        event_shift.state = 1
        v._wheel(event_shift)
        v.canvas.xview_scroll.assert_called_with(-3, "units")

    def test_collapsable_info_tab(self):
        v = Viewer.__new__(Viewer)
        v.app = self.app
        v.info_collapsed = False
        v.info_body = MagicMock()
        v.info_toggle_btn = MagicMock()
        v.info_hdr_title = MagicMock()

        # Test collapse toggle
        v.toggle_info_collapse()
        self.assertTrue(v.info_collapsed)
        v.info_body.pack_forget.assert_called_once()
        v.info_toggle_btn.config.assert_called_with(text="▴")

        # Test expand toggle
        v.toggle_info_collapse()
        self.assertFalse(v.info_collapsed)
        v.info_body.pack.assert_called_with(fill="both", expand=True)
        v.info_toggle_btn.config.assert_called_with(text="▾")

        # Test info updating
        v.pid = 12345
        v.note = ""
        v.info = MagicMock()
        v.info_artist_val = MagicMock()
        v.info_dim_val = MagicMock()
        v.info_type_val = MagicMock()
        v.info_src_val = MagicMock()
        v.post = {
            "id": 12345,
            "rating": "s",
            "score": {"total": 50},
            "fav_count": 10,
            "sources": ["https://twitter.com/example/status/1"],
            "file": {"width": 1920, "height": 1080, "ext": "png", "size": 1024000},
            "tags": {"artist": ["super_artist"]},
        }

        v._update_info()
        v.info_artist_val.config.assert_called_with(text="super_artist")
        v.info_dim_val.config.assert_called_with(text="1920 × 1080 px")
        self.assertEqual(v.source_url, "https://twitter.com/example/status/1")

    def test_video_transport_seek_volume(self):
        import queue
        import threading

        import numpy as np

        v = Viewer.__new__(Viewer)
        v._seek_lock = threading.Lock()
        v._seek_to = None
        v._frame_epoch = 0
        v._video_q = queue.Queue()
        v.video_duration = 12.0

        self.assertEqual(Viewer._fmt_time(0), "0:00")
        self.assertEqual(Viewer._fmt_time(65), "1:05")
        self.assertEqual(Viewer._fmt_time(3661), "1:01:01")
        self.assertEqual(Viewer._fmt_time("nope"), "0:00")

        v._request_seek(3.25)
        self.assertEqual(v._frame_epoch, 1)
        self.assertTrue(v._seek_pending())
        self.assertAlmostEqual(v._take_seek(), 3.25)
        self.assertIsNone(v._take_seek())
        self.assertFalse(v._seek_pending())

        # Seek past the end clamps just before the duration so the decoder can land.
        v._request_seek(100)
        self.assertAlmostEqual(v._take_seek(), 11.95)
        v.video_duration = 0
        v._request_seek(-2)
        self.assertEqual(v._take_seek(), 0.0)

        pcm = np.array([[1000, -1000], [2000, -2000]], dtype=np.int16)
        v._audio_gain = 1
        self.assertTrue(np.array_equal(v._apply_gain(pcm), pcm))
        v._audio_gain = 0
        muted = v._apply_gain(pcm)
        self.assertTrue(np.all(muted == 0))
        self.assertEqual(muted.shape, pcm.shape)
        v._audio_gain = 0.5
        quieter = v._apply_gain(pcm)
        self.assertEqual(quieter.dtype, np.int16)
        self.assertEqual(int(quieter[0, 0]), 500)
        self.assertEqual(int(quieter[0, 1]), -500)
        self.assertEqual(int(quieter[1, 0]), 1000)

        # Length must stay finite: a raw tick count or a multi-day sentinel is not a duration.
        self.assertEqual(Viewer._parse_duration_tag("00:01:02.5"), 62.5)
        self.assertEqual(Viewer._parse_duration_tag("1:02"), 62.0)
        self.assertIsNone(Viewer._sane_duration(1954370))
        self.assertIsNone(Viewer._sane_duration(float("inf")))
        self.assertAlmostEqual(Viewer._pick_duration([2.0, 1.9, 1954370, float("inf")]), 2.0)
        self.assertEqual(Viewer._pick_duration([9223372036854]), 0.0)
        # 1,954,370 ticks inside a 2.0s clip is 1.954s, not an endless counter.
        self.assertAlmostEqual(Viewer._fit_seconds(1954370, 2.0), 1.95437, places=3)
        self.assertAlmostEqual(Viewer._fit_seconds(1.2, 2.0), 1.2)
        self.assertIsNone(Viewer._fit_seconds(1954370, 0))
        self.assertEqual(Viewer._fmt_time(62), "1:02")
        self.assertEqual(Viewer._fmt_time(float("inf")), "0:00")

    def test_api_login_endpoint(self):
        from cavefetch.api import Api
        api = Api()
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 999, "name": "TestUser"}
        api.get = MagicMock(return_value=mock_response)

        res = api.login("TestUser", "my_api_key_123")
        # Verify /users/me.json was requested (NOT /profile.json)
        api.get.assert_called_with("/users/me.json")
        self.assertEqual(res["name"], "TestUser")
        self.assertEqual(api.api.auth, ("TestUser", "my_api_key_123"))


if __name__ == "__main__":
    unittest.main()
