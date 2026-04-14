"""
Live PC hardware snapshot — CPU, RAM, GPU, Disk, Network.
Tailored for FizzBeast (i7-13620H, RTX 4070 Laptop, 32GB).
"""
import asyncio, psutil, platform

async def get_pc_snapshot() -> dict:
    def _snap():
        cpu  = psutil.cpu_percent(interval=0.5)
        ram  = psutil.virtual_memory()
        disk_c = psutil.disk_usage("C:\\")
        import os
        disk_d = psutil.disk_usage("D:\\") if os.path.exists("D:\\") else None
        net  = psutil.net_io_counters()
        snap = {
            "hostname": platform.node(),
            "os":       f"{platform.system()} {platform.version()}",
            "cpu": {
                "brand":   "Intel Core i7-13620H",
                "cores":   psutil.cpu_count(logical=False),
                "threads": psutil.cpu_count(logical=True),
                "usage_%": cpu,
                "freq_mhz": psutil.cpu_freq().current if psutil.cpu_freq() else 2400,
            },
            "ram": {
                "total_gb":     round(ram.total / 1024**3, 2),
                "used_gb":      round(ram.used  / 1024**3, 2),
                "available_gb": round(ram.available / 1024**3, 2),
                "usage_%":      ram.percent,
            },
            "disk_C": {
                "total_gb": round(disk_c.total / 1024**3, 1),
                "used_gb":  round(disk_c.used  / 1024**3, 1),
                "free_gb":  round(disk_c.free  / 1024**3, 1),
            },
            "net": {
                "bytes_sent_mb": round(net.bytes_sent / 1024**2, 1),
                "bytes_recv_mb": round(net.bytes_recv / 1024**2, 1),
            },
        }
        if disk_d:
            snap["disk_D"] = {
                "total_gb": round(disk_d.total / 1024**3, 1),
                "used_gb":  round(disk_d.used  / 1024**3, 1),
                "free_gb":  round(disk_d.free  / 1024**3, 1),
            }
        try:
            import GPUtil
            gpus = GPUtil.getGPUs()
            snap["gpu"] = [
                {
                    "name":       g.name,
                    "vram_total": g.memoryTotal,
                    "vram_used":  g.memoryUsed,
                    "vram_free":  g.memoryFree,
                    "load_%":     g.load * 100,
                    "temp_c":     g.temperature,
                }
                for g in gpus
            ]
        except Exception:
            snap["gpu"] = [{"name": "RTX 4070 Laptop (GPUtil unavailable)"}]
        return snap
    return await asyncio.to_thread(_snap)
