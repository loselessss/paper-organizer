"""GitHub Releases based update discovery, download and installer launch."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from paper_organizer.infra.redaction import redact_text
from paper_organizer.infra.secrets import sanitized_child_environment


GITHUB_REPOSITORY = "loselessss/paper-organizer"
GITHUB_API_URL = (
    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
)
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_EXPECTED_PUBLISHER = "SANGKYU SHIN"
_SHA256_RE = re.compile(r"^sha256:([0-9a-fA-F]{64})$")
_INSTALLER_RE = re.compile(
    r"^PaperOrganizer_Setup_(\d+\.\d+\.\d+)\.exe$", re.IGNORECASE
)
_MAX_RELEASE_JSON_BYTES = 2 * 1024 * 1024


class UpdateError(RuntimeError):
    pass


class UpdateCancelled(UpdateError):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class AvailableUpdate:
    version: str
    tag_name: str
    release_name: str
    release_notes: str
    release_url: str
    published_at: str
    asset: ReleaseAsset | None


@dataclass(frozen=True, slots=True)
class UpdateDownloadProgress:
    completed_bytes: int
    total_bytes: int
    bytes_per_second: float


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = _VERSION_RE.fullmatch(value.strip())
    if not match:
        raise UpdateError(f"지원하지 않는 버전 형식입니다: {value}")
    return tuple(int(part) for part in match.groups())


def _trusted_github_url(value: str, *, release_asset: bool = False) -> bool:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        return False
    expected = f"/{GITHUB_REPOSITORY}/releases/"
    if not parsed.path.casefold().startswith(expected.casefold()):
        return False
    return not release_asset or "/download/" in parsed.path.casefold()


class GitHubUpdateService:
    def __init__(
        self,
        current_version: str,
        *,
        opener: Callable[..., Any] = urlopen,
        download_root: Path | None = None,
        signature_verifier: Callable[[Path], bool] | None = None,
    ) -> None:
        self.current_version = current_version
        self._open = opener
        self._download_root = download_root
        self._signature_verifier = signature_verifier or _verify_authenticode_publisher

    def _downloads_directory(self) -> Path:
        return self._download_root or (
            Path(tempfile.gettempdir()) / "PaperOrganizer" / "updates"
        )

    def cleanup_downloads(self) -> tuple[Path, ...]:
        """Remove stale managed downloads while retaining one future installer."""

        root = self._downloads_directory()
        if not root.is_dir():
            return ()
        removed: list[Path] = []
        future_installers: list[tuple[tuple[int, int, int], Path]] = []
        current = _version_tuple(self.current_version)
        try:
            entries = tuple(root.iterdir())
        except OSError:
            return ()
        for path in entries:
            if not path.is_file():
                continue
            name = path.name
            if name.casefold().endswith(".exe.part"):
                installer_name = name[:-5]
                if _INSTALLER_RE.fullmatch(installer_name):
                    self._try_remove(path, removed)
                continue
            match = _INSTALLER_RE.fullmatch(name)
            if match is None:
                continue
            version = _version_tuple(match.group(1))
            if version <= current:
                self._try_remove(path, removed)
            else:
                future_installers.append((version, path))

        future_installers.sort(key=lambda item: item[0], reverse=True)
        for _version, path in future_installers[1:]:
            self._try_remove(path, removed)
        return tuple(removed)

    @staticmethod
    def _try_remove(path: Path, removed: list[Path]) -> None:
        try:
            path.unlink()
        except OSError:
            return
        removed.append(path)

    def check(self) -> AvailableUpdate | None:
        request = Request(
            GITHUB_API_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"PaperOrganizer/{self.current_version}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        try:
            with self._open(request, timeout=15) as response:
                payload = response.read(_MAX_RELEASE_JSON_BYTES + 1)
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise UpdateError(f"GitHub 릴리스 정보를 확인하지 못했습니다: {exc}") from None
        if len(payload) > _MAX_RELEASE_JSON_BYTES:
            raise UpdateError("GitHub 릴리스 응답이 허용 크기를 초과했습니다.")
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateError(f"GitHub 릴리스 응답을 읽지 못했습니다: {exc}") from None
        if not isinstance(data, dict):
            raise UpdateError("GitHub 릴리스 응답 형식이 올바르지 않습니다.")

        tag_name = str(data.get("tag_name", "")).strip()
        latest_version = tag_name.removeprefix("v")
        if _version_tuple(latest_version) <= _version_tuple(self.current_version):
            return None
        release_url = str(data.get("html_url", ""))
        if not _trusted_github_url(release_url):
            raise UpdateError("GitHub 릴리스 주소를 신뢰할 수 없습니다.")

        asset = self._select_installer(data.get("assets"), latest_version)
        return AvailableUpdate(
            version=latest_version,
            tag_name=tag_name,
            release_name=str(data.get("name") or tag_name),
            release_notes=str(data.get("body") or ""),
            release_url=release_url,
            published_at=str(data.get("published_at") or ""),
            asset=asset,
        )

    def _select_installer(
        self, raw_assets: object, version: str
    ) -> ReleaseAsset | None:
        if not isinstance(raw_assets, list):
            return None
        expected_name = f"PaperOrganizer_Setup_{version}.exe".casefold()
        candidates = [
            item
            for item in raw_assets
            if isinstance(item, dict)
            and str(item.get("name", "")).casefold() == expected_name
        ]
        if not candidates:
            return None
        item = candidates[0]
        name = str(item.get("name", ""))
        download_url = str(item.get("browser_download_url", ""))
        if (
            Path(name).name != name
            or not _INSTALLER_RE.fullmatch(name)
            or not _trusted_github_url(download_url, release_asset=True)
        ):
            raise UpdateError("릴리스 설치파일 정보가 안전하지 않습니다.")
        digest = str(item.get("digest") or "")
        match = _SHA256_RE.fullmatch(digest)
        return ReleaseAsset(
            name=name,
            download_url=download_url,
            size=max(0, int(item.get("size") or 0)),
            sha256=match.group(1).lower() if match else "",
        )

    def download(
        self,
        update: AvailableUpdate,
        *,
        progress: Callable[[UpdateDownloadProgress], None] | None = None,
        cancel: Event | None = None,
    ) -> Path:
        asset = update.asset
        if asset is None:
            raise UpdateError("이 릴리스에는 Windows 설치파일이 없습니다.")
        if not asset.sha256:
            raise UpdateError(
                "설치파일의 SHA-256 정보가 없어 자동 업데이트를 진행할 수 없습니다."
            )
        root = self._downloads_directory()
        root.mkdir(parents=True, exist_ok=True)
        destination = root / asset.name
        partial = destination.with_suffix(destination.suffix + ".part")
        if destination.is_file():
            if self._cached_installer_is_valid(destination, asset):
                if progress is not None:
                    progress(
                        UpdateDownloadProgress(
                            completed_bytes=destination.stat().st_size,
                            total_bytes=asset.size,
                            bytes_per_second=0.0,
                        )
                    )
                return destination
            try:
                destination.unlink()
            except OSError as exc:
                raise UpdateError(
                    f"손상된 업데이트 설치 파일을 교체하지 못했습니다: {exc}"
                ) from None
        digest = hashlib.sha256()
        completed = 0
        started = time.monotonic()
        request = Request(
            asset.download_url,
            headers={"User-Agent": f"PaperOrganizer/{self.current_version}"},
            method="GET",
        )
        try:
            with self._open(request, timeout=60) as response, partial.open("wb") as stream:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise UpdateCancelled("업데이트 다운로드를 취소했습니다.")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    stream.write(chunk)
                    digest.update(chunk)
                    completed += len(chunk)
                    if progress is not None:
                        elapsed = max(time.monotonic() - started, 0.001)
                        progress(
                            UpdateDownloadProgress(
                                completed_bytes=completed,
                                total_bytes=asset.size,
                                bytes_per_second=completed / elapsed,
                            )
                        )
                stream.flush()
                os.fsync(stream.fileno())
        except UpdateCancelled:
            partial.unlink(missing_ok=True)
            raise
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            partial.unlink(missing_ok=True)
            raise UpdateError(f"업데이트 다운로드에 실패했습니다: {exc}") from None
        if asset.size and completed != asset.size:
            partial.unlink(missing_ok=True)
            raise UpdateError(
                f"설치파일 크기가 다릅니다: {completed:,} / {asset.size:,} bytes"
            )
        if digest.hexdigest().lower() != asset.sha256:
            partial.unlink(missing_ok=True)
            raise UpdateError("설치파일 SHA-256 검증에 실패했습니다.")
        os.replace(partial, destination)
        return destination

    @staticmethod
    def _cached_installer_is_valid(
        destination: Path, asset: ReleaseAsset
    ) -> bool:
        try:
            if asset.size and destination.stat().st_size != asset.size:
                return False
            digest = hashlib.sha256()
            with destination.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        except OSError:
            return False
        return digest.hexdigest().lower() == asset.sha256

    def launch_installer(self, path: Path) -> None:
        installer = path.resolve()
        if (
            not installer.is_file()
            or installer.suffix.casefold() != ".exe"
            or not _INSTALLER_RE.fullmatch(installer.name)
        ):
            raise UpdateError("실행할 업데이트 설치파일이 올바르지 않습니다.")
        if not self._signature_verifier(installer):
            raise UpdateError(
                "업데이트 설치파일의 Authenticode 서명 또는 게시자를 확인할 수 없습니다."
            )
        try:
            subprocess.Popen(
                [str(installer), "/SP-", "/CLOSEAPPLICATIONS"],
                close_fds=True,
                env=sanitized_child_environment(),
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise UpdateError(
                f"업데이트 설치파일을 실행하지 못했습니다: {redact_text(exc)}"
            ) from None


def _verify_authenticode_publisher(path: Path) -> bool:
    """Require a valid Windows signature from the configured publisher."""

    if os.name != "nt":
        return False
    script = (
        "$s=Get-AuthenticodeSignature -LiteralPath $args[0];"
        "[pscustomobject]@{Status=[string]$s.Status;Subject=[string]$s.SignerCertificate.Subject}"
        "|ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script, str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            env=sanitized_child_environment(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        value = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError):
        return False
    return (
        completed.returncode == 0
        and str(value.get("Status")) == "Valid"
        and _EXPECTED_PUBLISHER.casefold() in str(value.get("Subject") or "").casefold()
    )
