"""
System tray icon so you can see Phantom is running.
Fixed: draw.text crash on systems without a default PIL font.
Added: Show Memory Stats menu item.
"""
import threading
import subprocess
import sys
from pathlib import Path

LOG_PATH = Path(__file__).parent.parent / "logs" / "server.log"


def _create_image():
    from PIL import Image, ImageDraw
    img  = Image.new("RGBA", (64, 64), color=(18, 18, 18, 255))
    draw = ImageDraw.Draw(img)
    # Teal circle background
    draw.ellipse([4, 4, 60, 60], fill=(79, 152, 163, 255))
    # 'P' letter — drawn as a simple geometric shape to avoid font dependency
    # Vertical bar
    draw.rectangle([18, 14, 26, 50], fill=(255, 255, 255, 255))
    # Top bump of P
    draw.ellipse([24, 14, 46, 34], fill=(255, 255, 255, 255))
    draw.ellipse([26, 17, 43, 31], fill=(79, 152, 163, 255))  # hollow inside
    return img


def _open_log(icon, item):
    try:
        subprocess.Popen(["notepad.exe", str(LOG_PATH)])
    except Exception:
        pass


def _open_memory(icon, item):
    mem_path = Path(__file__).parent.parent / "data" / "memory.json"
    try:
        subprocess.Popen(["notepad.exe", str(mem_path)])
    except Exception:
        pass


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
                Item("Open Log",    _open_log),
                Item("Open Memory", _open_memory),
                pystray.Menu.SEPARATOR,
                Item("Quit Phantom", _quit),
            ),
        )
        icon.run()
    except ImportError:
        pass  # pystray not installed — tray is optional
    except Exception:
        pass  # silently skip if headless or display unavailable


def start_tray_thread():
    t = threading.Thread(target=run_tray, daemon=True)
    t.start()
