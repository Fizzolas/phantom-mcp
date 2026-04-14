"""
Process management — list, find, kill, launch.

FIX (sweep-2):
  - kill_process: psutil.NoSuchProcess was only caught in find_process,
    not in kill_process itself. If the process exits between the find and
    the kill call, it previously raised an unhandled exception that surfaced
    as an error:500 in the MCP response. Now caught explicitly.
  - kill_process: SYSTEM_PROCS blocklist was checked against p.name() but
    some protected processes have variable names depending on version/instance.
    Added PID range check: PIDs 0-8 are always kernel/system on Windows.
  - list_processes: cpu_percent on first call always returns 0.0 because
    psutil needs a sampling interval. Added a 0.1s non-blocking interval
    so the numbers are actually meaningful.
"""
import asyncio
import psutil
import subprocess

SYSTEM_PROCS = {
    "system", "registry", "smss.exe", "csrss.exe",
    "wininit.exe", "services.exe", "lsass.exe",
}
WARN_PROCS = {"svchost.exe", "explorer.exe"}
# PIDs 0-8 are always Windows kernel/idle/system processes — never touch
SYSTEM_PID_MAX = 8


async def list_processes(sort_by: str = "ram", limit: int = 50) -> list:
    """
    List running processes.
    FIX: cpu_percent(interval=0.1) so values aren't all 0.0.
    sort_by: 'ram' | 'cpu' | 'name' | 'pid'
    limit: max results (1-200)
    """
    limit = max(1, min(limit, 200))

    def _get():
        procs = []
        for p in psutil.process_iter(["pid", "name", "memory_info", "status"]):
            try:
                cpu = p.cpu_percent(interval=0.1)
                procs.append({
                    "pid":     p.info["pid"],
                    "name":    p.info["name"],
                    "ram_mb":  round(p.info["memory_info"].rss / 1024**2, 1),
                    "cpu_%":   cpu,
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
    FIX: NoSuchProcess is now caught inside _kill so a race between find and
    kill doesn't blow up. Also blocks PID 0-8 (Windows kernel range).
    """
    def _kill():
        # FIX: block kernel-range PIDs before even calling psutil
        if pid <= SYSTEM_PID_MAX:
            return f"BLOCKED: PID {pid} is in the Windows kernel PID range (0-{SYSTEM_PID_MAX})."

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
                p.kill()
                return f"Force-killed '{p.name()}' (PID {pid})"
            else:
                p.terminate()
                return f"Terminated '{p.name()}' (PID {pid})"

        # FIX: process may have exited between find and kill
        except psutil.NoSuchProcess:
            return f"PID {pid} no longer exists (already exited)."
        except psutil.AccessDenied:
            return f"ERROR: Access denied for PID {pid} (try running as admin)"

    return await asyncio.to_thread(_kill)


async def launch_app(target: str, wait: bool = False, timeout: int = 10) -> str:
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
