"""Keep one GUI process and forward later launches to the existing window."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtNetwork import QLocalServer, QLocalSocket


def default_server_name() -> str:
    identity = str(Path.home().resolve()).casefold().encode("utf-8")
    suffix = hashlib.sha256(identity).hexdigest()[:12]
    return f"paper-organizer-{suffix}"


class SingleInstanceGuard(QObject):
    """Own a local server or notify the process that already owns it."""

    activation_requested = pyqtSignal()

    def __init__(self, server_name: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self._server_name = server_name or default_server_name()
        self._server: QLocalServer | None = None

    def acquire(self) -> bool:
        if self._server is not None and self._server.isListening():
            return True
        if self._notify_existing():
            return False

        QLocalServer.removeServer(self._server_name)
        server = QLocalServer(self)
        server.newConnection.connect(self._accept_connections)
        if not server.listen(self._server_name):
            if self._notify_existing():
                server.deleteLater()
                return False
            message = server.errorString() or "알 수 없는 오류"
            server.deleteLater()
            raise RuntimeError(f"단일 실행 잠금을 만들 수 없습니다: {message}")
        self._server = server
        return True

    def close(self) -> None:
        server = self._server
        self._server = None
        if server is None:
            return
        server.close()
        QLocalServer.removeServer(self._server_name)
        server.deleteLater()

    def _notify_existing(self) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self._server_name)
        if not socket.waitForConnected(500):
            socket.abort()
            return False
        socket.write(b"activate")
        socket.flush()
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()
        return True

    def _accept_connections(self) -> None:
        server = self._server
        if server is None:
            return
        accepted = False
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            if socket is None:
                break
            accepted = True
            socket.disconnectFromServer()
            socket.deleteLater()
        if accepted:
            self.activation_requested.emit()
