# CaveFetch - Desktop Launcher

### Quick Start:

#### Windows:
Double-click `run.bat`. It will automatically check for Python, install `requests` and `pillow`, and start the app immediately!

#### macOS / Linux:
Open a terminal in this folder and run:
```bash
chmod +x run.sh
./run.sh
```
or simply:
```bash
pip install -r requirements.txt
python3 run.py
```

### Viewer
Double-click a post to open it. Videos play in that window. Under the picture there is a seek bar (drag or click to skip) plus **Mute** and a **Vol** slider. Space still plays and pauses. Volume stays where you set it when you move to the next post.

### Favorites
- **Favorite button & shortcut**: In the viewer window toolbar, click the **★ Favorite** button (or press `V` on your keyboard) to toggle favorite status on any post.
- **e621 Website Sync**: When logged into your e621 account (via the **Login** button), favoriting a post immediately adds or removes it from your account on the **e621 website** (`https://e621.net/favorites`).
- **Main window button**: Click **★ Favorites** in the main toolbar to quickly view all your favorited posts.
