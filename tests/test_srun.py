"""深澜认证响应解析。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from hdu_netdebugger.constants import SrunProductId
from hdu_netdebugger.models.srun import (
    DeviceItem,
    SrunAuthResponse,
    parse_auth_response,
    parse_srun_jsonp,
)

JsonObject = dict[str, Any]


class TestParseSrunJsonp:
    def test_strips_jquery_wrapper(self) -> None:
        assert parse_srun_jsonp('jQuery({"error":"ok"})') == {"error": "ok"}

    def test_accepts_plain_json(self) -> None:
        assert parse_srun_jsonp('{"error":"ok"}') == {"error": "ok"}

    def test_accepts_trailing_semicolon(self) -> None:
        assert parse_srun_jsonp('jQuery({"error":"ok"});') == {"error": "ok"}

    def test_accepts_custom_callback_name(self) -> None:
        assert parse_srun_jsonp('callback_123({"a":1})') == {"a": 1}

    def test_tolerates_surrounding_whitespace(self) -> None:
        assert parse_srun_jsonp('  jQuery({"a":1})  \n') == {"a": 1}

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "not json at all",
            "<html><body>portal</body></html>",
            "jQuery(broken",
            "jQuery([1,2,3])",
            "jQuery()",
        ],
    )
    def test_returns_none_for_unparsable_input(self, text: str) -> None:
        assert parse_srun_jsonp(text) is None


class TestSrunAuthResponse:
    def test_parses_full_payload(self, srun_ok_text: str) -> None:
        response = parse_auth_response(srun_ok_text)
        assert response is not None
        assert response.error == "ok"
        assert response.billing_name == "电信"
        assert response.domain == "mac-auth"
        assert response.user_name == "2025001"
        assert response.products_id == "4"
        assert response.online_device_total == "1"
        assert response.is_ok
        assert not response.is_not_online

    def test_keys_are_case_insensitive(self) -> None:
        """原版用 PropertyNameCaseInsensitive，这里必须等价。"""
        response = SrunAuthResponse.from_payload(
            {"ERROR": "ok", "Billing_Name": "联通", "Online_IP": "10.0.0.1"}
        )
        assert response.error == "ok"
        assert response.billing_name == "联通"
        assert response.online_ip == "10.0.0.1"

    def test_string_numbers_are_coerced(self) -> None:
        response = SrunAuthResponse.from_payload(
            {"all_bytes": "1024", "remain_seconds": "60.0", "user_balance": None}
        )
        assert response.all_bytes == 1024
        assert response.remain_seconds == 60
        assert response.user_balance == 0

    def test_unparsable_numbers_fall_back_to_zero(self) -> None:
        response = SrunAuthResponse.from_payload({"all_bytes": "unknown", "bytes_in": []})
        assert response.all_bytes == 0
        assert response.bytes_in == 0

    def test_missing_fields_use_defaults(self) -> None:
        response = SrunAuthResponse.from_payload({})
        assert response.error == ""
        assert response.products_id == ""
        assert response.online_device_detail == {}
        assert not response.is_ok
        assert not response.is_not_online

    def test_online_device_detail_double_encoded_string(
        self,
        srun_device_detail: JsonObject,
    ) -> None:
        """原版注释提到 online_device_detail 会被二次编码成字符串。"""
        response = SrunAuthResponse.from_payload(
            {"online_device_detail": json.dumps(srun_device_detail, ensure_ascii=False)}
        )
        assert set(response.online_device_detail) == {"63568314", "0"}
        device = response.online_device_detail["63568314"]
        assert device.ip == "10.150.184.118"
        assert device.os_name == "MAC auth"

    def test_online_device_detail_direct_object(self, srun_device_detail: JsonObject) -> None:
        """部分深澜版本直接给对象，也要能解析。"""
        response = SrunAuthResponse.from_payload({"online_device_detail": srun_device_detail})
        assert response.online_device_detail["63568314"].rad_online_id == "63568314"

    @pytest.mark.parametrize(
        "raw",
        ["", "   ", "not json", "[]", "null", None, 123, {"broken": True}],
    )
    def test_online_device_detail_garbage_is_tolerated(self, raw: Any) -> None:
        response = SrunAuthResponse.from_payload({"online_device_detail": raw})
        assert response.online_device_detail == {}

    def test_active_devices_filters_empty_ip(self, srun_device_detail: JsonObject) -> None:
        """空 IP 的占位设备要过滤掉，等价于原版 GetOnlineDeviceNotEmpty。"""
        response = SrunAuthResponse.from_payload(
            {"online_device_detail": json.dumps(srun_device_detail)}
        )
        assert set(response.active_devices) == {"63568314"}

    @pytest.mark.parametrize(
        ("products_id", "expected"),
        [
            ("3", SrunProductId.CHINA_UNICOM),
            ("4", SrunProductId.CHINA_TELECOM),
            ("5", SrunProductId.CHINA_MOBILE),
            (" 4 ", SrunProductId.CHINA_TELECOM),
        ],
    )
    def test_isp_id_known_values(
        self,
        products_id: str,
        expected: SrunProductId,
    ) -> None:
        response = SrunAuthResponse.from_payload({"products_id": products_id})
        assert response.isp_id is expected

    @pytest.mark.parametrize("products_id", ["", "0", "-1", "9", "abc", "999"])
    def test_isp_id_unknown_values(self, products_id: str) -> None:
        response = SrunAuthResponse.from_payload({"products_id": products_id})
        assert response.isp_id is None

    def test_not_online_state(self) -> None:
        response = SrunAuthResponse.from_payload({"error": "not_online_error"})
        assert response.is_not_online
        assert not response.is_ok

    def test_parse_auth_response_returns_none_on_garbage(self) -> None:
        assert parse_auth_response("<html>portal</html>") is None

    def test_parse_auth_response_roundtrip(self, srun_ok_text: str) -> None:
        response = parse_auth_response(srun_ok_text)
        assert response is not None
        assert response.isp_id is SrunProductId.CHINA_TELECOM
        assert list(response.active_devices) == ["63568314"]
        assert response.real_name == "张三"


class TestDeviceItem:
    def test_from_payload_maps_all_fields(self) -> None:
        device = DeviceItem.from_payload(
            {
                "class_name": "1",
                "ip": "10.0.0.2",
                "ip6": "::1",
                "os_name": "Windows",
                "rad_online_id": "42",
            }
        )
        assert (device.class_name, device.ip, device.ip6) == ("1", "10.0.0.2", "::1")
        assert device.os_name == "Windows"
        assert device.rad_online_id == "42"

    def test_from_payload_is_lenient(self) -> None:
        device = DeviceItem.from_payload({"ip": 12345, "extra": "ignored"})
        assert device.ip == "12345"
        assert device.os_name == ""
