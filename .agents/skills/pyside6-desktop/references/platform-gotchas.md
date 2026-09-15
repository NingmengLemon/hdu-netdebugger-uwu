# Platform-Specific Gotchas

Windows / macOS / Linux / ARM64 edge cases.

## Rule 16: PySide6 not available on ARM64 Linux

PySide6 wheels don't exist for ARM64 Linux (e.g., NVIDIA DGX Spark). Use `python -m py_compile` for syntax checking. The actual build MUST happen on Windows — PyInstaller builds for the platform it runs on. Cross-compilation is not supported.

## Rule 17: netifaces2 C extension memory leak on Windows

The netifaces2 C extension leaks memory unboundedly on Windows with certain hardware (AMD Radeon 780M, multiple virtual adapters). `netifaces.interfaces()` + `netifaces.ifaddresses()` invoke Windows `GetAdaptersAddresses` API and never return — consuming 8-22GB RAM.

Fix: Use `subprocess.run(["ipconfig", "/all"])` parsing on Windows. Keep netifaces for Linux/macOS.

```python
def get_network_interfaces() -> list[NetworkInterface]:
    if platform.system() == "Windows":
        return _parse_ipconfig()
    else:
        return _get_interfaces_netifaces()
```

## Rule 18: Windows ipconfig /all parsing gotchas

- Open with `capture_output=True, text=True, timeout=10`
- Adapters can have **multiple** IPv4 addresses (temp IP aliases)
- Emit each IP+mask pair as a separate interface entry
- Handle the "Preferred" suffix: `parts[1].strip().split("(")[0].strip()`

```python
# When a second IPv4 Address appears for same adapter, emit the previous one first
if "IPv4 Address" in stripped:
    _emit(current_name, current_ip, current_mask)  # emit previous
    current_ip = ""  # reset for new address
    current_mask = ""
```

## Rule 19: Health check timeouts for routed networks

Factory defaults (2s timeout, 5s interval) are too tight on routed networks. Devices on different subnets through routers can take 2-3s to respond.

Production settings:
- HTTP timeout: **5s** (not 2s)
- Check interval: **10s** (not 5s)
- Failure threshold: **3** consecutive failures before "connection lost"

## Rule 35: High DPI scaling breaks layouts on Windows

On Windows with 125%+ DPI scaling (common on 4K monitors), PySide6 auto-scales layouts unpredictably. A window designed for 800×600 renders as 1000×750 at 125% DPI, breaking grid layouts.

Test on both 100% and 150% DPI before shipping. Use minimum sizes rather than fixed sizes where possible:

```python
# FRAGILE — clips text at 150% DPI
btn.setFixedWidth(80)

# RESILIENT — accommodates DPI scaling
btn.setMinimumWidth(80)
```
