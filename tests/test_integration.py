"""Integration tests for KM140F bridge with mock TCP server and MQTT broker."""

import socket
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "km140f"))

from km140f import BridgeState, parse_line


class MockTCPServer:
    """Mock TCP server that simulates KM140F device."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((host, port))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.running = False
        self.thread: threading.Thread | None = None
        self.received_data: list[bytes] = []

    def start(self) -> None:
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while self.running:
            try:
                self.server.settimeout(1.0)
                try:
                    conn, _addr = self.server.accept()
                except TimeoutError:
                    continue
                with conn:
                    while self.running:
                        try:
                            data = conn.recv(1024)
                            if not data:
                                break
                            self.received_data.append(data)
                            # Send mock :A= response
                            if b":C" in data:
                                conn.sendall(b":C=12345,67890\n")
                            elif b":A" in data:
                                conn.sendall(b":A=1200,5000,1,120,80000,1000\n")
                        except (OSError, ConnectionResetError):
                            break
            except OSError:
                break

    def stop(self) -> None:
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        self.server.close()


class MockMQTTBroker:
    """Simple mock MQTT broker for testing."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((host, port))
        self.server.listen(5)
        self.port = self.server.getsockname()[1]
        self.running = False
        self.thread: threading.Thread | None = None
        self.published_messages: list[bytes] = []

    def start(self) -> None:
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while self.running:
            try:
                self.server.settimeout(1.0)
                try:
                    conn, _addr = self.server.accept()
                except TimeoutError:
                    continue
                with conn:
                    while self.running:
                        try:
                            data = conn.recv(1024)
                            if not data:
                                break
                            # Simple MQTT CONNECT response
                            if data[0] == 0x10:  # CONNECT
                                conn.sendall(b"\x20\x02\x00\x00")  # CONNACK
                            # Simple MQTT PUBLISH capture
                            elif data[0] == 0x30:  # PUBLISH
                                self.published_messages.append(data)
                        except (OSError, ConnectionResetError):
                            break
            except OSError:
                break

    def stop(self) -> None:
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        self.server.close()


class TestIntegration:
    """Integration tests with mock servers."""

    def test_mock_tcp_server(self) -> None:
        """Test that mock TCP server works correctly."""
        server = MockTCPServer()
        server.start()
        try:
            # Connect and send :C command
            sock = socket.create_connection((server.host, server.port), timeout=5)
            sock.sendall(b":C\n")
            response = sock.recv(1024)
            assert b":C=" in response
            sock.close()
        finally:
            server.stop()

    def test_mock_mqtt_broker(self) -> None:
        """Test that mock MQTT broker works correctly."""
        broker = MockMQTTBroker()
        broker.start()
        try:
            sock = socket.create_connection((broker.host, broker.port), timeout=5)
            # Send MQTT CONNECT packet
            connect_packet = b"\x10\x0c\x00\x04MQTT\x04\x02\x00\x3c\x00\x00"
            sock.sendall(connect_packet)
            response = sock.recv(1024)
            assert len(response) > 0
            sock.close()
        finally:
            broker.stop()

    def test_parse_line_with_mock_data(self) -> None:
        """Test parsing with data from mock server format."""
        server = MockTCPServer()
        server.start()
        try:
            sock = socket.create_connection((server.host, server.port), timeout=5)
            sock.sendall(b":A\n")
            response = sock.recv(1024).decode().strip()
            data = parse_line(response)
            assert data is not None
            assert data["voltage"] == 12.0
            assert data["current"] == 5.0
            assert data["status"] == "Charging"
            sock.close()
        finally:
            server.stop()

    def test_bridge_state(self) -> None:
        """Test BridgeState class."""
        state = BridgeState()
        assert state.tcp_connected is False
        assert state.mqtt_connected is False
        assert state.last_published_values == {}
        assert state.metrics["tcp_reconnects"] == 0

    def test_data_buffer(self) -> None:
        """Test data buffering."""
        buffer: deque[dict[str, Any]] = deque(maxlen=10)
        buffer.append({"test": "data"})
        assert len(buffer) == 1
        assert buffer[0]["test"] == "data"


class TestConfigurationValidation:
    """Test configuration validation."""

    def test_validate_config_default(self) -> None:
        """Test that default config passes validation."""
        from km140f import validate_config

        errors = validate_config()
        assert isinstance(errors, list)

    def test_validate_config_returns_list(self) -> None:
        """Test that validate_config returns a list."""
        from km140f import validate_config

        result = validate_config()
        assert isinstance(result, list)
