import sys
import unittest
from unittest.mock import MagicMock, patch

# Mock GUI and external modules if not present in headless environments
for mod_name in ["tkinter", "tkinter.ttk", "PIL", "PIL.Image", "PIL.ImageTk", "requests"]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

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

from cavefetch.api import Api
from cavefetch.viewer import Viewer


class TestFavorites(unittest.TestCase):
    def test_api_favorite_requires_login(self):
        api = Api()
        api.user = None
        with self.assertRaises(ValueError):
            api.add_favorite(12345)
        with self.assertRaises(ValueError):
            api.remove_favorite(12345)

    def test_api_add_favorite_calls_post(self):
        api = Api()
        api.user = "testuser"
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        with patch.object(api, "post", return_value=mock_resp) as mock_post:
            result = api.add_favorite(999)
            self.assertTrue(result)
            mock_post.assert_called_once_with("/favorites.json", post_id=999)

    def test_api_remove_favorite_calls_delete(self):
        api = Api()
        api.user = "testuser"
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        with patch.object(api, "delete", return_value=mock_resp) as mock_del:
            result = api.remove_favorite(999)
            self.assertTrue(result)
            mock_del.assert_called_once_with("/favorites/999.json")

    def test_viewer_favorite_button_and_info(self):
        app = MagicMock()
        app.is_favorited.return_value = False
        app.theme.c = {
            "bg": "#111827", "panel": "#1f2937", "fg": "#e5e7eb", "muted": "#9ca3af",
            "entry": "#0b1220", "btn": "#374151", "btn_active": "#4b5563", "border": "#374151",
            "accent": "#3b82f6", "tile": "#0b1220", "grid_bg": "#1f2937", "viewer_bg": "#0b0f19",
        }

        v = Viewer.__new__(Viewer)
        v.app = app
        v.c = app.theme.c
        v.pid = 42
        v.post = {
            "id": 42,
            "rating": "s",
            "score": {"total": 10},
            "fav_count": 5,
            "file": {"width": 800, "height": 600, "ext": "png", "size": 102400},
            "tags": {"artist": ["cool_artist"]},
        }
        v.note = ""
        v.fav_btn = MagicMock()
        v.info = MagicMock()
        v.info_artist_val = MagicMock()
        v.info_dim_val = MagicMock()
        v.info_type_val = MagicMock()
        v.info_src_val = MagicMock()
        v.info_fav_val = MagicMock()

        # When not favorited
        v._update_favorite_btn()
        v.fav_btn.config.assert_called_with(text="☆ Favorite")

        # When favorited
        app.is_favorited.return_value = True
        v._update_favorite_btn()
        v.fav_btn.config.assert_called_with(text="★ Favorited")

        # Check _update_info displays favorited state
        v._update_info()
        info_text = v.info.config.call_args[1]["text"]
        self.assertIn("★ FAVORITED", info_text)


if __name__ == "__main__":
    unittest.main()
