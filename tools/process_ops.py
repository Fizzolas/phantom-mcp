"""
Process management — list, kill, launch.
"""
import asyncio, psutil, subprocess

SYSTEM_PROCESSES = {"system", "registry", "smss.exe", "csrss.exe",
                    "wininit.exe", "services.exe", "lsass.exe", "svchost.exe"}

async def list_processes() -> list:
    def _get():
        procs = []
        for p in psutil.process_iter(["pid", "name", "memory_info", "status"]):
            try:
                procs.append({
                    "pid":    p.info["pid"],
                    "name":   p.info["name"],
                    "ram_mb": round(p.info["memory_info"].rss / 1024**2, 1),
                    "status": p.info["status"],
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return sorted(procs, key=lambda x: x["ram_mb"], reverse=True)[:50]
    return await asyncio.to_thread(_get)

async def kill_process(pid: int) -> str:
    def _kill():
        try:
            p = psutil.Process(pid)
            if p.name().lower() in SYSTEM_PROCESSES:
                return f"BLOCKED: Cannot kill system process {p.name()} (PID {pid})"
            p.terminate()
            return f"Terminated PID {pid} ({p.name()})"
        except psutil.NoSuchProcess:
            return f"ERROR: PID {pid} not found"
        except psutil.AccessDenied:
            return f"ERROR: Access denied for PID {pid}"
    return await asyncio.to_thread(_kill)

async def launch_app(target: str) -> str:
    def _launch():
        try:
            subprocess.Popen(target, shell=True,
                             creationflags=subprocess.DETACHED_PROCESS)
            return f"Launched: {target}"
        except Exception as e:
            return f"ERROR: {e}"
    return await asyncio.to_thread(_launch)
