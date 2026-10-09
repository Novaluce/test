"""Tiny JSON settings store in %APPDATA%\\CaveFetch\\config.json."""
import json
import os

DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "CaveFetch")
FILE = os.path.join(DIR, "config.json")


def load():
    try:
        with open(FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(cfg):
    try:
        os.makedirs(DIR, exist_ok=True)
        with open(FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except OSError:
        pass
