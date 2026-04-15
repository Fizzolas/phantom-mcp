"""
File operations.
read_file caps output at MAX_READ_CHARS to prevent context overflow.
Agents can write/delete files they created freely; user files require auth_guard approval.

FIX (sweep-2):
  - search_files: Path.rglob() follows symlinks and can loop forever on Windows
    AppData junctions. Now tracks visited real paths (via os.stat st_ino+st_dev)
    to detect and skip circular symlinks, plus the depth cap that was already described
    in the docstring but not actually implemented.
  - list_dir: PermissionError on individual entries (common in system folders)
    now skipped gracefully instead of crashing the whole listing.
  - read_file: binary file detection — returns a clear error instead of a
    garbled UnicodeDecodeError when the agent accidentally tries to read an exe/zip.

FIX (sweep-4):
  - read_dir_tree: truncation string had literal embedded newlines (SyntaxError).
    Replaced with proper \n escape sequences.
"""
import asyncio
import os
from pathlib import Path

MAX_READ_CHARS = 12000
MAX_SEARCH_DEPTH = 20
MAX_SEARCH_RESULTS = 200

# Common binary extensions — return a clear error instead of garbage
_BINARY_EXTS = {
    ".exe", ".dll", ".so", ".bin", ".zip", ".tar", ".gz", ".rar",
    ".7z", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico",
    ".mp3", ".mp4", ".wav", ".ogg", ".pdf", ".psd", ".db",
    ".sqlite", ".pyc", ".pyd", ".class", ".o",
}


def _truncate(text: str) -> str:
    if len(text) <= MAX_READ_CHARS:
        return text
    half = MAX_READ_CHARS // 2
    return (
        text[:half]
        + f"\n\n... [file truncated — {len(text)} total chars, showing first+last {half}] ...\n\n"
        + text[-half:]
    )


async def read_file(path: str) -> dict:
    try:
        p = Path(path)
        if not p.exists():
            return {"error": f"File not found: {path}"}
        if not p.is_file():
            return {"error": f"Path is not a file: {path}"}
        # FIX: bail early on known binary formats
        if p.suffix.lower() in _BINARY_EXTS:
            return {
                "error": f"Binary file detected ({p.suffix}). Cannot read as text.",
                "path": str(p.resolve()),
                "size_bytes": p.stat().st_size,
                "hint": "Use run_cmd or run_powershell to process this file type.",
            }
        text = p.read_text(encoding="utf-8", errors="replace")
        truncated = len(text) > MAX_READ_CHARS
        return {
            "content": _truncate(text),
            "size_bytes": p.stat().st_size,
            "truncated": truncated,
            "path": str(p.resolve()),
        }
    except Exception as e:
        return {"error": str(e)}


async def write_file(path: str, content: str) -> dict:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(p.resolve()), "bytes_written": len(content.encode())}
    except Exception as e:
        return {"error": str(e)}


async def append_file(path: str, content: str) -> dict:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(content)
        return {"ok": True, "path": str(p.resolve()), "bytes_appended": len(content.encode())}
    except Exception as e:
        return {"error": str(e)}


async def list_dir(path: str) -> dict:
    try:
        p = Path(path)
        if not p.exists():
            return {"error": f"Path not found: {path}"}
        entries = []
        for item in sorted(p.iterdir()):
            try:
                entries.append({
                    "name": item.name,
                    "type": "dir" if item.is_dir() else "file",
                    # FIX: wrap stat() so a PermissionError on one entry
                    # doesn't kill the entire directory listing
                    "size_bytes": item.stat().st_size if item.is_file() else None,
                })
            except (PermissionError, OSError):
                entries.append({"name": item.name, "type": "unknown", "size_bytes": None})
        return {"path": str(p.resolve()), "entries": entries, "count": len(entries)}
    except Exception as e:
        return {"error": str(e)}


async def delete_file(path: str) -> dict:
    def _do_delete():
        import shutil
        p = Path(path)
        if not p.exists():
            return {"error": f"Not found: {path}"}
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return {"ok": True, "deleted": str(p.resolve())}
    try:
        return await asyncio.to_thread(_do_delete)
    except Exception as e:
        return {"error": str(e)}


def file_exists(path: str) -> dict:
    p = Path(path)
    return {
        "exists": p.exists(),
        "is_file": p.is_file(),
        "is_dir": p.is_dir(),
        "path": str(p.resolve()),
    }


async def search_files(root: str, pattern: str) -> dict:
    """
    FIX (sweep-2): The depth-limit in the previous version only checked
    path component count — it did NOT detect circular symlinks (Windows junction
    loops have the same depth as normal paths). Now we also track visited
    (st_ino, st_dev) pairs to detect and break loops.
    """
    def _search():
        p = Path(root)
        if not p.exists():
            return {"error": f"Root not found: {root}"}
        matches = []
        root_depth = len(p.resolve().parts)
        visited_inodes: set = set()

        try:
            for f in p.rglob(pattern):
                # Depth check
                if len(f.parts) - root_depth > MAX_SEARCH_DEPTH:
                    continue
                # Symlink loop detection
                try:
                    st = os.stat(f)
                    inode_key = (st.st_ino, st.st_dev)
                    if inode_key in visited_inodes:
                        continue
                    visited_inodes.add(inode_key)
                except OSError:
                    continue
                matches.append(str(f))
                if len(matches) >= MAX_SEARCH_RESULTS:
                    break
        except PermissionError:
            pass
        return {"matches": matches, "count": len(matches), "capped": len(matches) == MAX_SEARCH_RESULTS}

    try:
        return await asyncio.to_thread(_search)
    except Exception as e:
        return {"error": str(e)}


def read_dir_tree(root: str, pattern: str = "**/*", max_files: int = 10) -> dict:
    """
    List all entries under `root` matching `pattern` and return the text
    contents of up to `max_files` matching files in one call.

    Use instead of list_dir + multiple read_file calls when you need to
    understand the full contents of a folder in one step.

    Returns:
      ok        : bool
      root      : resolved root path
      pattern   : glob pattern used
      tree      : list of {path, type, size?} for every entry
      contents  : {relative_path: text} for up to max_files readable files
      _hint     : model guidance hint

    FIX (sweep-4): truncation string used literal embedded newlines which
    caused a SyntaxError on import. Now uses proper escape sequences.
    """
    base = Path(root)
    if not base.exists():
        return {"ok": False, "error": f"Root not found: {root}"}

    entries = []
    contents = {}
    matched = []
    for p in sorted(base.glob(pattern)):
        try:
            rel = str(p.relative_to(base))
        except Exception:
            rel = str(p)
        if p.is_dir():
            entries.append({"path": rel, "type": "dir"})
        else:
            entries.append({"path": rel, "type": "file", "size": p.stat().st_size})
            matched.append(p)

    for p in matched[:max_files]:
        try:
            rel = str(p.relative_to(base))
        except Exception:
            rel = str(p)
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            contents[rel] = "<binary or non-utf8 file>"
            continue
        if len(text) > 12000:
            text = text[:6000] + "\n\n...[truncated]...\n\n" + text[-6000:]
        contents[rel] = text

    return {
        "ok": True,
        "root": str(base),
        "pattern": pattern,
        "max_files": max_files,
        "tree": entries,
        "contents": contents,
        "_hint": "Review contents and continue reading relevant files if the goal is not complete."
    }
