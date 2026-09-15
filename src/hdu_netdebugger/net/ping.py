"""ICMP Ping.

对应 C# 原版的 ``Utils/MultiPing.cs``。原版直接用 ``System.Net.NetworkInformation.Ping``
（Windows 下底层是 ICMP 原始套接字，失败时才退回 ``ICMP.DLL``）。Python 标准库没有等价能力，
因此这里做三级后端：

1. :data:`Backend.ICMPLIB` —— `icmplib <https://github.com/ValentinBELYN/icmplib>`_ 的 ICMP 套接字。
   Windows 上走 ``SOCK_RAW``（与 .NET ``Ping`` 一致，时延读数与系统 ``ping`` 相符），
   Unix 上非特权模式走 ``SOCK_DGRAM``；缺少权限时抛 :class:`BackendUnavailable` 自动降级。
2. :data:`Backend.WINDOWS_ICMP` —— ctypes 调用 ``iphlpapi!IcmpSendEcho``，**无需任何权限**，
   因此是 Windows 上非管理员用户的保底路径（原始套接字需要管理员权限）。
3. :data:`Backend.SYSTEM` —— 兜底调用系统 ``ping`` 命令并解析输出，任何平台都能跑。

:data:`Backend.AUTO` 会按上面顺序依次尝试，并把探测成功的后端缓存下来，避免每次都走失败路径。
"""

from __future__ import annotations

import asyncio
import re
import socket
import sys
import time
from dataclasses import dataclass
from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import TYPE_CHECKING, Any

from hdu_netdebugger.models.enums import PingStatus
from hdu_netdebugger.models.ping import MultiPingResult, PingReply

if TYPE_CHECKING:
    from typing import Protocol, Self

    from icmplib import ICMPRequest

    class _IcmpSocket(Protocol):
        """icmplib 套接字的最小接口。"""

        def send(self, request: ICMPRequest) -> None: ...

        def receive(self, request: ICMPRequest, timeout: float) -> Any: ...

        def __enter__(self) -> Self: ...

        def __exit__(self, *exc: object) -> None: ...


__all__ = [
    "Backend",
    "BackendUnavailable",
    "PingOptions",
    "ping_multiple",
    "reset_backend_cache",
    "resolve_target",
]

Target = str | IPv4Address | IPv6Address


class Backend(StrEnum):
    """ICMP 发送后端。"""

    AUTO = "auto"
    ICMPLIB = "icmplib"
    WINDOWS_ICMP = "windows-icmp"
    SYSTEM = "system"


class BackendUnavailable(RuntimeError):
    """该后端在当前环境不可用（缺少权限 / 平台不支持 / 缺少可执行文件）。"""


@dataclass(frozen=True, slots=True)
class PingOptions:
    """单次多包 Ping 的参数。"""

    count: int = 4
    timeout_s: float = 1.0
    payload_size: int = 32
    ttl: int = 64
    interval_s: float = 0.2


#: AUTO 模式下探测成功的后端，避免每次都走一遍失败路径
_auto_backend_cache: Backend | None = None


def resolve_target(target: Target) -> str:
    """把主机名解析成 IP 字符串；已经是 IP 时原样返回。

    Raises:
        OSError: 域名无法解析。
    """
    if isinstance(target, (IPv4Address, IPv6Address)):
        return str(target)
    text = target.strip()
    try:
        return str(ip_address(text))
    except ValueError:
        pass
    infos = socket.getaddrinfo(text, None, proto=socket.IPPROTO_ICMP)
    if not infos:
        raise OSError(f"无法解析目标地址: {text}")
    return str(infos[0][4][0])


async def ping_multiple(
    target: Target,
    options: PingOptions | None = None,
    *,
    backend: Backend = Backend.AUTO,
) -> MultiPingResult:
    """对同一目标连续发送多次 ICMP Echo 并汇总。

    Args:
        target: IPv4 / IPv6 地址或主机名。
        options: Ping 参数，默认 4 次 / 1 秒超时 / 32 字节载荷。
        backend: 指定后端；``AUTO`` 会按平台顺序依次尝试。

    Returns:
        汇总结果。目标完全不可达时返回"全部超时"的结果，而不是抛异常。

    Raises:
        BackendUnavailable: 所有后端都不可用（例如系统缺少 ``ping`` 命令）。
    """
    opts = options or PingOptions()
    address = await asyncio.to_thread(resolve_target, target)
    return await _dispatch(address, opts, backend)


def reset_backend_cache() -> None:
    """清空 AUTO 模式的后端缓存（测试用）。"""
    global _auto_backend_cache  # noqa: PLW0603 - 与 _dispatch 中的写操作配对
    _auto_backend_cache = None


async def _dispatch(address: str, opts: PingOptions, backend: Backend) -> MultiPingResult:
    """按后端选择策略执行 Ping。"""
    if backend is not Backend.AUTO:
        return await _run_backend(backend, address, opts)

    global _auto_backend_cache  # noqa: PLW0603 - 进程级探测结果缓存
    if _auto_backend_cache is not None:
        try:
            return await _run_backend(_auto_backend_cache, address, opts)
        except BackendUnavailable:
            _auto_backend_cache = None

    last_error: Exception | None = None
    for candidate in _auto_order():
        try:
            result = await _run_backend(candidate, address, opts)
        except BackendUnavailable as exc:
            last_error = exc
            continue
        _auto_backend_cache = candidate
        return result

    raise BackendUnavailable(f"没有可用的 Ping 后端（最后错误: {last_error}）")


def _auto_order() -> tuple[Backend, ...]:
    """按平台给出后端尝试顺序。

    Windows 上把 ``icmplib`` 排在前面：它和 .NET ``Ping`` 一样走原始套接字；
    ``IcmpSendEcho`` 退居其次，只作为拿不到原始套接字（非管理员）时的保底。
    """
    if sys.platform == "win32":
        return (Backend.ICMPLIB, Backend.WINDOWS_ICMP, Backend.SYSTEM)
    return (Backend.ICMPLIB, Backend.SYSTEM)


async def _run_backend(backend: Backend, address: str, opts: PingOptions) -> MultiPingResult:
    """调用指定后端；CPU/阻塞型实现丢进线程池。"""
    if backend is Backend.WINDOWS_ICMP:
        return await asyncio.to_thread(_windows_icmp_ping, address, opts)
    if backend is Backend.ICMPLIB:
        return await asyncio.to_thread(_icmplib_ping, address, opts)
    return await _system_ping(address, opts)


# ---------------------------------------------------------------------------
# 后端 1：Windows IcmpSendEcho
# ---------------------------------------------------------------------------

#: ``IP_STATUS`` 取值（``ipexport.h``）
_IP_SUCCESS = 0
_IP_REQ_TIMED_OUT = 11010
_IP_UNREACHABLE = frozenset({11002, 11003, 11004, 11005, 11018})
_IP_TTL_EXPIRED = frozenset({11013, 11014})


def _ipaddr_network_order(address: str) -> int:
    """把点分十进制地址转成 ``IcmpSendEcho`` 要求的网络序 ``IPAddr``。

    ``IPAddr`` 是个 ``ULONG``，但它的**内存布局**必须是网络序字节 —— 也就是说小端机器上
    192.0.2.1 对应的数值是 ``0x012810AC``。若直接用 ``int(IPv4Address(address))``
    （把点分十进制当主机序整数解释）会得到字节反过来的 ``0xAC102801``，
    实际去 ping 的是另一个地址：可达目标会报超时，连回环地址都 ping 不到本机。
    """
    return int.from_bytes(IPv4Address(address).packed, "little")


def _windows_icmp_ping(address: str, opts: PingOptions) -> MultiPingResult:
    """用 ``iphlpapi!IcmpSendEcho`` 逐包发送 ICMP Echo。"""
    if sys.platform != "win32":
        raise BackendUnavailable("IcmpSendEcho 仅在 Windows 上可用")

    import ctypes  # noqa: PLC0415 - 仅 Windows 分支需要

    try:
        iphlpapi = ctypes.WinDLL("iphlpapi.dll", use_last_error=True)
    except OSError as exc:  # pragma: no cover - 理论上不会发生
        raise BackendUnavailable(f"无法加载 iphlpapi.dll: {exc}") from exc

    class IpOptionInformation(ctypes.Structure):
        # 使用显式定长类型，避免依赖平台对 ULONG / PVOID 的宽度定义
        _fields_ = (
            ("Ttl", ctypes.c_uint8),
            ("Tos", ctypes.c_uint8),
            ("Flags", ctypes.c_uint8),
            ("OptionsSize", ctypes.c_uint8),
            ("OptionsData", ctypes.c_void_p),
        )

    class IcmpEchoReply(ctypes.Structure):
        _fields_ = (
            ("Address", ctypes.c_uint32),
            ("Status", ctypes.c_uint32),
            ("RoundTripTime", ctypes.c_uint32),
            ("DataSize", ctypes.c_uint16),
            ("Reserved", ctypes.c_uint16),
            ("Data", ctypes.c_void_p),
            ("Options", IpOptionInformation),
        )

    iphlpapi.IcmpCreateFile.restype = ctypes.c_void_p
    iphlpapi.IcmpCreateFile.argtypes = ()
    iphlpapi.IcmpCloseHandle.restype = ctypes.c_int
    iphlpapi.IcmpCloseHandle.argtypes = (ctypes.c_void_p,)
    iphlpapi.IcmpSendEcho.restype = ctypes.c_uint32
    iphlpapi.IcmpSendEcho.argtypes = (
        ctypes.c_void_p,  # IcmpHandle
        ctypes.c_uint32,  # DestinationAddress
        ctypes.c_char_p,  # RequestData
        ctypes.c_uint16,  # RequestSize
        ctypes.POINTER(IpOptionInformation),  # RequestOptions
        ctypes.c_void_p,  # ReplyBuffer
        ctypes.c_uint32,  # ReplySize
        ctypes.c_uint32,  # Timeout
    )

    handle = iphlpapi.IcmpCreateFile()
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise BackendUnavailable("IcmpCreateFile 失败（可能缺少 ICMP 权限）")

    try:
        payload = _build_payload(opts.payload_size)
        # ReplyBuffer 至少需要 sizeof(ICMP_ECHO_REPLY) + RequestSize + 8
        reply_capacity = ctypes.sizeof(IcmpEchoReply) + len(payload) + 8
        reply_buffer = ctypes.create_string_buffer(reply_capacity)
        request_options = IpOptionInformation(
            Ttl=opts.ttl, Tos=0, Flags=0, OptionsSize=0, OptionsData=None
        )
        timeout_ms = max(1, round(opts.timeout_s * 1000))
        destination = _ipaddr_network_order(address)

        replies: list[PingReply] = []
        for sequence in range(1, opts.count + 1):
            if sequence > 1 and opts.interval_s > 0:
                time.sleep(opts.interval_s)
            sent = iphlpapi.IcmpSendEcho(
                handle,
                destination,
                payload,
                len(payload),
                ctypes.byref(request_options),
                reply_buffer,
                reply_capacity,
                timeout_ms,
            )
            replies.append(_classify_windows_reply(sent, reply_buffer, IcmpEchoReply, sequence))
        return MultiPingResult(target=address, replies=tuple(replies))
    finally:
        iphlpapi.IcmpCloseHandle(handle)


def _build_payload(size: int) -> bytes:
    """构造 ICMP 载荷。"""
    return (b"hdu-netdebugger" * 4)[:size].ljust(size, b"\x00")


def _classify_windows_reply(
    sent: int,
    reply_buffer: Any,
    reply_type: Any,
    sequence: int,
) -> PingReply:
    """把 ``IcmpSendEcho`` 的返回值翻译成 :class:`PingReply`。"""
    import ctypes  # noqa: PLC0415 - 与 _windows_icmp_ping 的延迟导入保持一致

    if sent == 0:
        error = ctypes.get_last_error()
        if error == _IP_REQ_TIMED_OUT:
            return PingReply(sequence=sequence, status=PingStatus.TIMEOUT)
        return PingReply(sequence=sequence, status=PingStatus.ERROR, error=f"Win32 错误码 {error}")

    status = int(reply_type.from_buffer(reply_buffer).Status)
    if status == _IP_SUCCESS:
        rtt = float(reply_type.from_buffer(reply_buffer).RoundTripTime)
        return PingReply(sequence=sequence, status=PingStatus.SUCCESS, rtt_ms=rtt)
    if status == _IP_REQ_TIMED_OUT:
        return PingReply(sequence=sequence, status=PingStatus.TIMEOUT)
    if status in _IP_UNREACHABLE or status in _IP_TTL_EXPIRED:
        return PingReply(
            sequence=sequence, status=PingStatus.UNREACHABLE, error=f"IP_STATUS={status}"
        )
    return PingReply(sequence=sequence, status=PingStatus.ERROR, error=f"IP_STATUS={status}")


# ---------------------------------------------------------------------------
# 后端 2：icmplib
# ---------------------------------------------------------------------------


def _icmplib_ping(address: str, opts: PingOptions) -> MultiPingResult:
    """借助 icmplib 的低层套接字逐包发送。"""
    try:
        from icmplib import (  # noqa: PLC0415 - 可选后端，延迟导入
            ICMPRequest,
            ICMPv4Socket,
            ICMPv6Socket,
        )
        from icmplib.exceptions import (  # noqa: PLC0415
            ICMPLibError,
            SocketPermissionError,
        )
        from icmplib.utils import unique_identifier  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - icmplib 是必装依赖
        raise BackendUnavailable(f"icmplib 不可用: {exc}") from exc

    try:
        version = ip_address(address).version
    except ValueError as exc:
        raise BackendUnavailable(f"非法地址: {address}") from exc

    socket_cls = ICMPv6Socket if version == 6 else ICMPv4Socket
    identifier = unique_identifier()
    replies: list[PingReply] = []

    try:
        with socket_cls(address=None, privileged=False) as sock:
            for sequence in range(1, opts.count + 1):
                if sequence > 1 and opts.interval_s > 0:
                    time.sleep(opts.interval_s)
                request = ICMPRequest(
                    destination=address,
                    id=identifier,
                    sequence=sequence,
                    payload_size=opts.payload_size,
                    ttl=opts.ttl,
                )
                replies.append(_icmplib_send_one(sock, request, opts.timeout_s, sequence))
    except SocketPermissionError as exc:
        raise BackendUnavailable(f"创建 ICMP 套接字需要更高权限: {exc}") from exc
    except ICMPLibError as exc:
        raise BackendUnavailable(f"ICMP 套接字错误: {exc}") from exc
    except PermissionError as exc:
        raise BackendUnavailable(f"创建 ICMP 套接字需要更高权限: {exc}") from exc
    except OSError as exc:
        raise BackendUnavailable(f"ICMP 套接字错误: {exc}") from exc

    return MultiPingResult(target=address, replies=tuple(replies))


def _icmplib_send_one(
    sock: _IcmpSocket, request: ICMPRequest, timeout_s: float, sequence: int
) -> PingReply:
    """发送单包并归类结果。"""
    from icmplib.exceptions import (  # noqa: PLC0415 - 与 _icmplib_ping 的延迟导入保持一致
        DestinationUnreachable,
        ICMPLibError,
        TimeExceeded,
        TimeoutExceeded,
    )

    try:
        sock.send(request)
        reply = sock.receive(request, timeout_s)
        reply.raise_for_status()
    except TimeoutExceeded:
        return PingReply(sequence=sequence, status=PingStatus.TIMEOUT)
    except (DestinationUnreachable, TimeExceeded) as exc:
        return PingReply(sequence=sequence, status=PingStatus.UNREACHABLE, error=str(exc))
    except ICMPLibError as exc:
        return PingReply(sequence=sequence, status=PingStatus.ERROR, error=str(exc))

    elapsed = (reply.time - request.time) * 1000
    return PingReply(sequence=sequence, status=PingStatus.SUCCESS, rtt_ms=max(0.0, float(elapsed)))


# ---------------------------------------------------------------------------
# 后端 3：系统 ping 命令
# ---------------------------------------------------------------------------

#: 匹配 ``time=12ms`` / ``时间=1ms`` / ``time<1 ms`` / ``time=1.234 ms``
_RTT_PATTERN = re.compile(r"[=<]\s*(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)


async def _system_ping(address: str, opts: PingOptions) -> MultiPingResult:
    """调用系统 ``ping`` 命令并解析输出。"""
    args = ["ping", *_system_ping_args(address, opts)]
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (FileNotFoundError, NotImplementedError) as exc:
        raise BackendUnavailable(f"系统中没有可用的 ping 命令: {exc}") from exc

    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(),
            timeout=opts.timeout_s * opts.count + 5.0,
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise BackendUnavailable("系统 ping 命令超时") from exc

    return _parse_system_ping(address, stdout.decode(errors="replace"), opts)


def _system_ping_args(address: str, opts: PingOptions) -> list[str]:
    """按平台拼装 ``ping`` 参数。"""
    timeout_ms = max(1, round(opts.timeout_s * 1000))
    if sys.platform == "win32":
        return [
            "-n",
            str(opts.count),
            "-w",
            str(timeout_ms),
            "-l",
            str(opts.payload_size),
            "-i",
            str(opts.ttl),
            address,
        ]
    if sys.platform == "darwin":
        return [
            "-c",
            str(opts.count),
            "-W",
            str(timeout_ms),
            "-s",
            str(opts.payload_size),
            "-m",
            str(opts.ttl),
            "-i",
            str(opts.interval_s),
            address,
        ]
    return [
        "-c",
        str(opts.count),
        "-W",
        str(max(1, round(opts.timeout_s))),
        "-s",
        str(opts.payload_size),
        "-t",
        str(opts.ttl),
        "-i",
        str(opts.interval_s),
        address,
    ]


def _parse_system_ping(address: str, output: str, opts: PingOptions) -> MultiPingResult:
    """把 ``ping`` 的文本输出还原成逐包结果。

    各平台 / 各语言环境下唯一稳定出现的是 ``TTL=`` 与 ``<数字> ms``，
    因此只能识别"收到回复"的行：成功包按出现顺序排列，其余位置补成超时。
    这是兜底后端，精度低于前两个后端。
    """
    rtts: list[float] = []
    for line in output.splitlines():
        if "ttl=" not in line.lower():
            continue
        match = _RTT_PATTERN.search(line)
        rtts.append(float(match.group(1)) if match else 0.0)

    replies = [
        PingReply(sequence=index + 1, status=PingStatus.SUCCESS, rtt_ms=rtt)
        for index, rtt in enumerate(rtts[: opts.count])
    ]
    replies.extend(
        PingReply(sequence=index + 1, status=PingStatus.TIMEOUT)
        for index in range(len(replies), opts.count)
    )
    return MultiPingResult(target=address, replies=tuple(replies))
