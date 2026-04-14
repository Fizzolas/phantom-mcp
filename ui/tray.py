"""
System tray icon so you can see Phantom is running.
Requires: pip install pystray pillow
"""
import threading, subprocess, sys
from pathlib import Path

LOG_PATH = Path(__file__).parent.parent / "logs" / "server.log"

def _create_image():
    from PIL import Image, ImageDraw
    img  = Image.new("RGB", (64, 64), color=(18, 18, 18))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill=(79, 152, 163))
    draw.text((20, 20), "P", fill="white")
    return img

def _open_log(icon, item):
    subprocess.Popen(["notepad.exe", str(LOG_PATH)])

def _quit(icon, item):
    icon.stop()
    sys.exit(0)

def run_tray():
    try:
        import pystray
        from pystray import MenuItem as Item
        icon = pystray.Icon(
            "Phantom MCP",
            _create_image(),
            "Phantom MCP — Running",
            menu=pystray.Menu(
                Item("Open Log", _open_log),
                Item("Quit Phantom", _quit),
            )
        )
        icon.run()
    except ImportError:
        pass

def start_tray_thread():
    t = threading.Thread(target=run_tray, daemon=True)
    t.start()
