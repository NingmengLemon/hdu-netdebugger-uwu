"""深澜（Srun）认证接口 ``rad_user_info`` 的响应模型.

对应 C# 原版的 ``Models/SrunAuthResponse.cs``。与原版相比做了两处稳健性增强：

* ``online_device_detail`` 既可能是**被二次编码的 JSON 字符串**（原版注释里提到的转义问题），
  也可能直接是对象；两种形态都能解析。
* 所有字段按大小写不敏感方式读取，并且对"数字被写成字符串"的字段做宽松强制转换。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from hdu_netdebugger.constants import SRUN_PRODUCT_GATEWAYS, SrunErrCode, SrunProductId

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["DeviceItem", "SrunAuthResponse", "parse_auth_response", "parse_srun_jsonp"]


def _lowered(payload: Mapping[str, Any]) -> dict[str, Any]:
    """构造小写键索引，等价于 C# 的 ``PropertyNameCaseInsensitive``。"""
    return {str(key).lower(): value for key, value in payload.items()}


def _as_str(value: Any) -> str:
    """把任意 JSON 值转成字符串，``None`` 变为空串。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _as_int(value: Any) -> int:
    """把任意 JSON 值转成整数，无法转换时返回 ``0``。"""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            try:
                return int(float(value.strip()))
            except ValueError:
                return 0
    return 0


@dataclass(slots=True)
class DeviceItem:
    """一台在线设备。"""

    class_name: str = ""
    ip: str = ""
    ip6: str = ""
    os_name: str = ""
    rad_online_id: str = ""

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> DeviceItem:
        """从原始字典构造。"""
        data = _lowered(payload)
        return cls(
            class_name=_as_str(data.get("class_name")),
            ip=_as_str(data.get("ip")),
            ip6=_as_str(data.get("ip6")),
            os_name=_as_str(data.get("os_name")),
            rad_online_id=_as_str(data.get("rad_online_id")),
        )


def _parse_device_detail(value: Any) -> dict[str, DeviceItem]:
    """解析 ``online_device_detail``。

    支持三种形态：二次编码的 JSON 字符串、直接给出的对象、空值。
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except ValueError:
            return {}
    if not isinstance(value, dict):
        return {}

    devices: dict[str, DeviceItem] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            devices[str(key)] = DeviceItem.from_payload(item)
    return devices


@dataclass(slots=True)
class SrunAuthResponse:
    """``rad_user_info`` 的响应体。"""

    server_flag: int = 0
    add_time: int = 0
    all_bytes: int = 0
    billing_name: str = ""
    bytes_in: int = 0
    bytes_out: int = 0
    checkout_date: int = 0
    domain: str = ""
    email: str = ""
    error: str = ""
    group_id: str = ""
    keepalive_time: int = 0
    online_device_detail: dict[str, DeviceItem] = field(default_factory=dict)
    online_device_total: str = ""
    online_ip: str = ""
    online_ip6: str = ""
    package_id: str = ""
    phone: str = ""
    pppoe_dial: str = ""
    products_id: str = ""
    products_name: str = ""
    real_name: str = ""
    remain_bytes: int = 0
    remain_seconds: int = 0
    sum_bytes: int = 0
    sum_seconds: int = 0
    sysver: str = ""
    user_balance: int = 0
    user_charge: int = 0
    user_mac: str = ""
    user_name: str = ""
    wallet_balance: int = 0

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> SrunAuthResponse:
        """从原始字典构造（大小写不敏感）。"""
        data = _lowered(payload)
        return cls(
            server_flag=_as_int(data.get("serverflag")),
            add_time=_as_int(data.get("add_time")),
            all_bytes=_as_int(data.get("all_bytes")),
            billing_name=_as_str(data.get("billing_name")),
            bytes_in=_as_int(data.get("bytes_in")),
            bytes_out=_as_int(data.get("bytes_out")),
            checkout_date=_as_int(data.get("checkout_date")),
            domain=_as_str(data.get("domain")),
            email=_as_str(data.get("email")),
            error=_as_str(data.get("error")),
            group_id=_as_str(data.get("group_id")),
            keepalive_time=_as_int(data.get("keepalive_time")),
            online_device_detail=_parse_device_detail(data.get("online_device_detail")),
            online_device_total=_as_str(data.get("online_device_total")),
            online_ip=_as_str(data.get("online_ip")),
            online_ip6=_as_str(data.get("online_ip6")),
            package_id=_as_str(data.get("package_id")),
            phone=_as_str(data.get("phone")),
            pppoe_dial=_as_str(data.get("pppoe_dial")),
            products_id=_as_str(data.get("products_id")),
            products_name=_as_str(data.get("products_name")),
            real_name=_as_str(data.get("real_name")),
            remain_bytes=_as_int(data.get("remain_bytes")),
            remain_seconds=_as_int(data.get("remain_seconds")),
            sum_bytes=_as_int(data.get("sum_bytes")),
            sum_seconds=_as_int(data.get("sum_seconds")),
            sysver=_as_str(data.get("sysver")),
            user_balance=_as_int(data.get("user_balance")),
            user_charge=_as_int(data.get("user_charge")),
            user_mac=_as_str(data.get("user_mac")),
            user_name=_as_str(data.get("user_name")),
            wallet_balance=_as_int(data.get("wallet_balance")),
        )

    # -- 便捷读取 ---------------------------------------------------------

    @property
    def is_ok(self) -> bool:
        """认证接口是否返回 ``ok``。"""
        return self.error == SrunErrCode.OK

    @property
    def is_not_online(self) -> bool:
        """是否处于"未认证"状态。"""
        return self.error == SrunErrCode.NOT_ONLINE

    @property
    def isp_id(self) -> SrunProductId | None:
        """解析出的运营商 ID；无效或未知时返回 ``None``。"""
        try:
            isp = SrunProductId(int(self.products_id))
        except (TypeError, ValueError):
            return None
        return isp if isp in SRUN_PRODUCT_GATEWAYS else None

    @property
    def active_devices(self) -> dict[str, DeviceItem]:
        """过滤掉 IP 为空的占位设备，等价于原版 ``GetOnlineDeviceNotEmpty``。"""
        return {key: item for key, item in self.online_device_detail.items() if item.ip}


def parse_srun_jsonp(text: str) -> dict[str, Any] | None:
    """剥离 JSONP 包装并解析出字典。

    深澜接口以 ``callback=jQuery`` 调用时会返回 ``jQuery({...})`` 形态。
    这里同时兼容纯 JSON、以及结尾带 ``;`` 的变体。

    Returns:
        解析成功返回字典，否则返回 ``None``。
    """
    if not text:
        return None

    candidate = text.strip()
    start = candidate.find("(")
    end = candidate.rfind(")")
    if 0 <= start < end:
        candidate = candidate[start + 1 : end].strip()
    if candidate.endswith(";"):
        candidate = candidate[:-1].strip()
    if not candidate.startswith("{"):
        return None

    try:
        payload = json.loads(candidate)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def parse_auth_response(text: str) -> SrunAuthResponse | None:
    """把认证接口的原始响应文本解析为 :class:`SrunAuthResponse`。

    Returns:
        解析成功返回模型，无法解析时返回 ``None``。
    """
    payload = parse_srun_jsonp(text)
    if payload is None:
        return None
    return SrunAuthResponse.from_payload(payload)
