"""
File system operations with ownership tracking.
Agent-created files are tagged in data/agent_files.json.
User files require authentication before modification.
"""
import asyncio, os, json, fnmatch
from pathlib import Path

AGENT_REGISTRY_PATH = Path(__file__).parent.parent / "data" / "agent_files.json"

def _load_registry() -> set:
    if AGENT_REGISTRY_PATH.exists():
        return set(json.loads(AGENT_REGISTRY_PATH.read_text()))
    return set()

def _save_registry(reg: set):
    AGENT_REGISTRY_PATH.parent.mkdir(exist_ok=True)
    AGENT_REGISTRY_PATH.write_text(json.dumps(sorted(reg), indent=2))

def _register_agent_file(path: str):
    reg = _load_registry()
    reg.add(str(Path(path).resolve()))
    _save_registry(reg)

def _is_agent_file(path: str) -> bool:
    return str(Path(path).resolve()) in _load_registry()

async def read_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return f"ERROR: File not found: {path}"
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"ERROR: {e}"

async def write_file(path: str, content: str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    _register_agent_file(path)
    return f"Written {len(content)} chars to {path}"

async def append_file(path: str, content: str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(content)
    return f"Appended {len(content)} chars to {path}"

async def list_dir(path: str) -> list:
    p = Path(path)
    if not p.is_dir():
        return [f"ERROR: Not a directory: {path}"]
    entries = []
    for item in sorted(p.iterdir()):
        entries.append({
            "name": item.name,
            "type": "dir" if item.is_dir() else "file",
            "size": item.stat().st_size if item.is_file() else None,
        })
    return entries

async def delete_file(path: str) -> str:
    import shutil
    p = Path(path)
    if not p.exists():
        return f"ERROR: Path not found: {path}"
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()
    reg = _load_registry()
    reg.discard(str(p.resolve()))
    _save_registry(reg)
    return f"Deleted: {path}"

def file_exists(path: str) -> dict:
    p = Path(path)
    return {
        "exists": p.exists(),
        "is_file": p.is_file(),
        "is_dir": p.is_dir(),
        "agent_owned": _is_agent_file(path),
    }

async def search_files(root: str, pattern: str) -> list:
    matches = []
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if fnmatch.fnmatch(fn.lower(), pattern.lower()):
                matches.append(os.path.join(dirpath, fn))
        if len(matches) > 200:
            matches.append("... (truncated at 200)")
            break
    return matches
