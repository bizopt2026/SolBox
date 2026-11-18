"""자체 서명 HTTPS 인증서 만들기."""
from __future__ import annotations

import ipaddress
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _san_entry(host: str) -> x509.GeneralName:
    try:
        return x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        return x509.DNSName(host)


_DNS_NAME = re.compile(r"^[A-Za-z0-9.-]+$")


def _check_host(host: str) -> None:
    """IP 주소이거나 영문·숫자·점·하이픈으로 된 이름만 허용한다."""
    try:
        ipaddress.ip_address(host)
        return
    except ValueError:
        pass
    if not _DNS_NAME.match(host) or host.startswith(("-", ".")):
        raise ValueError(f"접속 주소가 올바르지 않습니다: {host!r} (IP 주소 또는 영문·숫자·점·하이픈으로 된 이름만 쓸 수 있습니다. 포트나 http:// 는 빼세요.)")


def _write_private(path: Path, data: bytes) -> None:
    """처음부터 소유자만 읽고 쓰는 권한(0o600)으로 만든다."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def make_self_signed(
    out_dir: Path, hosts: list[str], days: int = 3650, overwrite: bool = False
) -> tuple[Path, Path]:
    """`server.crt`, `server.key` (PEM, RSA 2048) 를 만든다. IP 는 IP SAN, 이름은 DNS SAN, 첫 항목이 CN 이다."""
    hosts = [h.strip() for h in hosts if h and h.strip()]
    if not hosts:
        raise ValueError("접속 주소(IP 또는 이름)가 하나 이상 필요합니다.")
    for h in hosts:
        _check_host(h)
    out_dir = Path(out_dir)
    crt_path, key_path = out_dir / "server.crt", out_dir / "server.key"
    if not overwrite:
        for p in (crt_path, key_path):
            if p.exists():
                raise FileExistsError(str(p))

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([_san_entry(h) for h in hosts]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    crt_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
    )
    # 임시 이름으로 먼저 쓰고 한꺼번에 바꿔 놓는다. 중간에 실패하면 임시 파일을 지워 반쪽 인증서가 남지 않게 한다.
    tmp_crt, tmp_key = out_dir / "server.crt.tmp", out_dir / "server.key.tmp"
    try:
        for tmp in (tmp_crt, tmp_key):
            tmp.unlink(missing_ok=True)
        _write_private(tmp_key, key_pem)
        tmp_crt.write_bytes(crt_pem)
        old_key = key_path.read_bytes() if key_path.is_file() else None
        os.replace(tmp_key, key_path)
        try:
            os.replace(tmp_crt, crt_path)
        except OSError:
            # 키만 새것이면 인증서와 짝이 맞지 않는다: 옛 키를 되돌리고, 되돌리지 못하면 둘 다 지워 알린다.
            try:
                if old_key is None:
                    key_path.unlink(missing_ok=True)
                else:
                    restore = out_dir / "server.key.tmp"
                    restore.unlink(missing_ok=True)
                    _write_private(restore, old_key)
                    os.replace(restore, key_path)
            except OSError:
                key_path.unlink(missing_ok=True)
                crt_path.unlink(missing_ok=True)
                raise OSError("인증서를 바꾸지 못했고 옛 키도 되돌리지 못해 두 파일을 모두 지웠습니다. 다시 만드세요.") from None
            raise
    finally:
        for tmp in (tmp_crt, tmp_key):
            tmp.unlink(missing_ok=True)
    return crt_path, key_path
