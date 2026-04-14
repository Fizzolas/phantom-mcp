"""
Live PC hardware snapshot — CPU, RAM, GPU, Disk (all drives), Network.
Tailored for FizzBeast (i7-13620H, RTX 4070 Laptop, 32GB DDR5).
"""
import asyncio
import psutil
import platform


async def get_pc_snapshot() -> dict:
    def _snap():
        cpu      = psutil.cpu_percent(interval=0.5)
        cpu_per  = psutil.cpu_percent(interval=None, percpu=True)
        ram      = psutil.virtual_memory()
        swap     = psutil.swap_memory()
        net      = psutil.net_io_counters()
        freq     = psutil.cpu_freq()

        # --- All mounted disks ---
        disks = {}
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks[part.device.replace("\\\\\\\\", "").rstrip("\\\\")]  = {
                    "mountpoint": part.mountpoint,
                    "fstype":     part.fstype,
                    "total_gb":   round(usage.total  / 1024**3, 1),
                    "used_gb":    round(usage.used   / 1024**3, 1),
                    "free_gb":    round(usage.free   / 1024**3, 1),
                    "usage_%":    usage.percent,
                }
            except (PermissionError, OSError):
                pass

        snap = {
            "hostname": platform.node(),
            "os":       f"{platform.system()} {platform.version()}",
            "cpu": {
                "brand":       "Intel Core i7-13620H",
                "cores":       psutil.cpu_count(logical=False),
                "threads":     psutil.cpu_count(logical=True),
                "usage_%":     cpu,
                "per_core_%":  cpu_per,
                "freq_mhz":    round(freq.current, 0) if freq else 2400,
                "freq_max_mhz": round(freq.max, 0) if freq else 4900,
            },
            "ram": {
                "total_gb":     round(ram.total     / 1024**3, 2),
                "used_gb":      round(ram.used      / 1024**3, 2),
                "available_gb": round(ram.available / 1024**3, 2),
                "usage_%":      ram.percent,
            },
            "swap": {
                "total_gb": round(swap.total / 1024**3, 2),
                "used_gb":  round(swap.used  / 1024**3, 2),
                "usage_%":  swap.percent,
            },
            "disks":  disks,
            "net": {
                "bytes_sent_mb": round(net.bytes_sent / 1024**2, 1),
                "bytes_recv_mb": round(net.bytes_recv / 1024**2, 1),
                "packets_sent":  net.packets_sent,
                "packets_recv":  net.packets_recv,
            },
        }

        # --- GPU (GPUtil) ---
        try:
            import GPUtil
            gpus = GPUtil.getGPUs()
            snap["gpu"] = [
                {
                    "name":        g.name,
                    "driver":      g.driver,
                    "vram_total_mb": g.memoryTotal,
                    "vram_used_mb":  g.memoryUsed,
                    "vram_free_mb":  g.memoryFree,
                    "load_%":      round(g.load * 100, 1),
                    "temp_c":      g.temperature,
                }
                for g in gpus
            ]
        except Exception:
            snap["gpu"] = [{"name": "RTX 4070 Laptop", "note": "GPUtil unavailable — run: pip install gputil"}]

        return snap

    return await asyncio.to_thread(_snap)
