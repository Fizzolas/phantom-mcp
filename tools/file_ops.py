"""
File operations.
read_file caps output at MAX_READ_CHARS to prevent context overflow.
Agents can write/delete files they created freely; user files require auth_guard approval.

FIX: delete_file now uses asyncio.to_thread for shutil.rmtree so large directory
trees don't block the entire async event loop.
FIX: search_files now has a depth limit to prevent infinite loops on circular symlinks
(common in Windows AppData junctions).
"""
import asyncio
import os
from pathlib import Path

MAX_READ_CHARS = 12000
MAX_SEARCH_DEPTH = 20


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
            entries.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "size_bytes": item.stat().st_size if item.is_file() else None,
            })
        return {"path": str(p.resolve()), "entries": entries, "count": len(entries)}
    except Exception as e:
        return {"error": str(e)}


async def delete_file(path: str) -> dict:
    """
    FIX: shutil.rmtree is now run in asyncio.to_thread so it doesn't block
    the event loop on large directory trees.
    """
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
    FIX: depth-limited walk to avoid infinite loops on Windows symlink junctions
    (common in C:\\Users\\AppData and some node_modules trees).
    Depth capped at MAX_SEARCH_DEPTH=20 levels.
    """
    def _search():
        p = Path(root)
        if not p.exists():
            return {"error": f"Root not found: {root}"}
        matches = []
        root_depth = len(p.parts)
        try:
            for f in p.rglob(pattern):
                if len(f.parts) - root_depth > MAX_SEARCH_DEPTH:
                    continue
                matches.append(str(f))
                if len(matches) >= 200:
                    break
        except PermissionError:
            pass
        return {"matches": matches, "count": len(matches), "capped": len(matches) == 200}

    try:
        return await asyncio.to_thread(_search)
    except Exception as e:
        return {"error": str(e)}
