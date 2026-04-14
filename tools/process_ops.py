"""
Process management — list, find, kill, launch.
Added: find_process by name, force-kill option, CPU% per process.
"""
import asyncio
import psutil
import subprocess

SYSTEM_PROCS = {
    "system", "registry", "smss.exe", "csrss.exe",
    "wininit.exe", "services.exe", "lsass.exe",
}
# svchost is dangerous to mass-kill but has legitimate instances
# — we warn instead of hard-block
WARN_PROCS = {"svchost.exe", "explorer.exe"}


async def list_processes(sort_by: str = "ram", limit: int = 50) -> list:
    """
    List running processes.
    sort_by: 'ram' | 'cpu' | 'name' | 'pid'
    limit: max results (1-200)
    """
    limit = max(1, min(limit, 200))

    def _get():
        procs = []
        for p in psutil.process_iter(["pid", "name", "memory_info", "status", "cpu_percent"]):
            try:
                procs.append({
                    "pid":     p.info["pid"],
                    "name":    p.info["name"],
                    "ram_mb":  round(p.info["memory_info"].rss / 1024**2, 1),
                    "cpu_%":   p.info["cpu_percent"],
                    "status":  p.info["status"],
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        sort_key = {
            "ram":  lambda x: x["ram_mb"],
            "cpu":  lambda x: x["cpu_%"],
            "name": lambda x: x["name"].lower(),
            "pid":  lambda x: x["pid"],
        }.get(sort_by, lambda x: x["ram_mb"])

        reverse = sort_by not in ("name",)
        return sorted(procs, key=sort_key, reverse=reverse)[:limit]

    return await asyncio.to_thread(_get)


async def find_process(name: str) -> list:
    """
    Find all processes whose name contains `name` (case-insensitive).
    Returns matching entries with pid, name, ram_mb, cpu_%, status.
    """
    def _find():
        matches = []
        q = name.lower()
        for p in psutil.process_iter(["pid", "name", "memory_info", "status", "cpu_percent", "exe"]):
            try:
                if q in (p.info["name"] or "").lower():
                    matches.append({
                        "pid":    p.info["pid"],
                        "name":   p.info["name"],
                        "exe":    p.info["exe"],
                        "ram_mb": round(p.info["memory_info"].rss / 1024**2, 1),
                        "cpu_%":  p.info["cpu_percent"],
                        "status": p.info["status"],
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return matches
    results = await asyncio.to_thread(_find)
    if not results:
        return [{"message": f"No process found matching: '{name}'"}]
    return results


async def kill_process(pid: int, force: bool = False) -> str:
    """
    Terminate a process by PID.
    force=True uses SIGKILL (taskkill /F) for processes that ignore normal termination.
    System-critical processes (lsass, csrss, etc.) are always blocked.
    """
    def _kill():
        try:
            p = psutil.Process(pid)
            pname = p.name().lower()

            if pname in SYSTEM_PROCS:
                return f"BLOCKED: '{p.name()}' (PID {pid}) is a protected system process."

            if pname in WARN_PROCS and not force:
                return (
                    f"WARNING: '{p.name()}' is critical. "
                    f"Call kill_process(pid={pid}, force=True) to confirm."
                )

            if force:
                p.kill()   # SIGKILL — immediate, no cleanup
                return f"Force-killed '{p.name()}' (PID {pid})"
            else:
                p.terminate()   # SIGTERM — graceful
                return f"Terminated '{p.name()}' (PID {pid})"

        except psutil.NoSuchProcess:
            return f"ERROR: PID {pid} not found"
        except psutil.AccessDenied:
            return f"ERROR: Access denied for PID {pid} (try running as admin)"

    return await asyncio.to_thread(_kill)


async def launch_app(target: str, wait: bool = False, timeout: int = 10) -> str:
    """
    Launch an application or open a file.
    target: exe path, URL, document path, or 'start notepad.exe'
    wait: if True, wait up to `timeout` seconds for the process to start
    """
    def _launch():
        try:
            proc = subprocess.Popen(
                target,
                shell=True,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            if wait:
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    pass
            return f"Launched: {target} (PID {proc.pid})"
        except Exception as e:
            return f"ERROR: {e}"
    return await asyncio.to_thread(_launch)
