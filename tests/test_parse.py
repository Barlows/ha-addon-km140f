"""Unit tests for KM140F protocol parsing functions."""

import sys
from pathlib import Path

# Add parent directory to path so we can import km140f
sys.path.insert(0, str(Path(__file__).parent.parent / "km140f"))

from km140f import parse_a, parse_c, parse_line


class TestParseA:
    """Tests for parse_a function."""

    def test_basic_charging(self) -> None:
        result = parse_a(["1200", "5000", "1", "120", "80000", "1000"])
        assert result is not None
        assert result["voltage"] == 12.0
        assert result["current"] == 5.0
        assert result["power"] == 60.0
        assert result["remaining_capacity"] == 80.0
        assert result["time_remaining"] == 120
        assert result["set_capacity"] == 100.0
        assert result["soc"] == 80.0
        assert result["status"] == "Charging"

    def test_basic_discharging(self) -> None:
        result = parse_a(["1200", "5000", "0", "120", "80000", "1000"])
        assert result is not None
        assert result["voltage"] == 12.0
        assert result["current"] == -5.0
        assert result["power"] == -60.0
        assert result["status"] == "Discharging"

    def test_zero_capacity(self) -> None:
        result = parse_a(["1200", "0", "1", "0", "0", "0"])
        assert result is not None
        assert result["soc"] == 0.0

    def test_soc_clamped_high(self) -> None:
        result = parse_a(["1200", "0", "1", "0", "150000", "1000"])
        assert result is not None
        assert result["soc"] == 100.0

    def test_soc_clamped_low(self) -> None:
        result = parse_a(["1200", "0", "0", "0", "0", "1000"])
        assert result is not None
        assert result["soc"] == 0.0

    def test_short_frame(self) -> None:
        result = parse_a(["1200", "5000"])
        assert result is None

    def test_invalid_values(self) -> None:
        result = parse_a(["abc", "def", "1", "120", "80000", "1000"])
        assert result is None

    def test_extra_fields_ignored(self) -> None:
        result = parse_a(
            ["1200", "5000", "1", "120", "80000", "1000", "extra", "fields"]
        )
        assert result is not None
        assert result["voltage"] == 12.0


class TestParseC:
    """Tests for parse_c function."""

    def test_basic(self) -> None:
        result = parse_c(["12345", "67890"])
        assert result is not None
        assert result["charge_kwh"] == 12.345
        assert result["discharge_kwh"] == 67.89

    def test_zero_values(self) -> None:
        result = parse_c(["0", "0"])
        assert result is not None
        assert result["charge_kwh"] == 0.0
        assert result["discharge_kwh"] == 0.0

    def test_short_frame(self) -> None:
        result = parse_c(["12345"])
        assert result is None

    def test_invalid_values(self) -> None:
        result = parse_c(["abc", "def"])
        assert result is None


class TestVoltageClassDetection:
    """Tests for automatic battery voltage class detection."""

    def test_12v_pack(self) -> None:
        from km140f import detect_voltage_class

        nominal, low, high = detect_voltage_class(13.2)
        assert nominal == 12.0
        assert low == 11.0
        assert high == 14.4

    def test_24v_pack(self) -> None:
        from km140f import detect_voltage_class

        nominal, low, high = detect_voltage_class(26.4)
        assert nominal == 24.0
        assert low == 22.0
        assert high == 28.8

    def test_48v_pack(self) -> None:
        from km140f import detect_voltage_class

        nominal, low, high = detect_voltage_class(52.7)
        assert nominal == 48.0
        assert low == 44.0
        assert high == 57.6

    def test_48v_pack_charging(self) -> None:
        """A charging 48 V pack sits near 56 V and must not be misread."""
        from km140f import detect_voltage_class

        nominal, _low, _high = detect_voltage_class(56.4)
        assert nominal == 48.0

    def test_12v_pack_is_not_judged_by_48v_limits(self) -> None:
        """Regression: a 12 V pack must not trigger high-voltage alerts."""
        from km140f import detect_voltage_class

        _nominal, _low, high = detect_voltage_class(13.8)
        assert 13.8 < high, "13.8 V must be within a 12 V pack's range"

    def test_48v_pack_does_not_trigger_low_alert(self) -> None:
        """Regression: the original bug — 52.7 V flagged against a 15 V limit."""
        from km140f import detect_voltage_class

        _nominal, low, _high = detect_voltage_class(52.7)
        assert 52.7 > low, "52.7 V must not be below a 48 V pack's minimum"


class TestParseLine:
    """Tests for parse_line function."""

    def test_a_line(self) -> None:
        result = parse_line(":A=1200,5000,1,120,80000,1000")
        assert result is not None
        assert result["voltage"] == 12.0

    def test_c_line(self) -> None:
        result = parse_line(":C=12345,67890")
        assert result is not None
        assert result["charge_kwh"] == 12.345

    def test_empty_line(self) -> None:
        result = parse_line("")
        assert result is None

    def test_whitespace_only(self) -> None:
        result = parse_line("   ")
        assert result is None

    def test_unknown_line(self) -> None:
        result = parse_line(":X=123,456")
        assert result is None

    def test_a_in_middle_not_matched(self) -> None:
        result = parse_line("prefix:A=1200,5000,1,120,80000,1000")
        assert result is None

    def test_c_in_middle_not_matched(self) -> None:
        result = parse_line("prefix:C=12345,67890")
        assert result is None

    def test_a_with_trailing_comma(self) -> None:
        result = parse_line(":A=1200,5000,1,120,80000,1000,")
        assert result is not None
        assert result["voltage"] == 12.0

    def test_c_with_trailing_comma(self) -> None:
        result = parse_line(":C=12345,67890,")
        assert result is not None
        assert result["charge_kwh"] == 12.345
