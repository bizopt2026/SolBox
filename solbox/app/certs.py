"""HTTPS 인증서 만들기: 자체 발급자(ca.crt) 하나와 그것이 서명한 서버 인증서(server.crt)."""
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


SERVER_DAYS = 825  # 애플 기기(아이폰·맥)는 유효기간이 825일을 넘는 HTTPS 서버 인증서를 믿지 않는다
CA_DAYS = 3650  # 발급자(CA) 인증서는 10년. 기기에 한 번 "신뢰"로 설치하면 서버 인증서를 다시 만들어도 그대로 쓴다
CA_NAME = "SolBox Local CA"


def _ca(out_dir: Path, new_ca: bool):
    """`ca.crt`·`ca.key` (자체 발급자). 있으면 다시 쓰고(기기에 설치한 신뢰가 이어진다), 없거나 `new_ca` 면 새로 만든다."""
    ca_crt, ca_key = out_dir / "ca.crt", out_dir / "ca.key"
    if not new_ca and ca_crt.is_file() and ca_key.is_file():
        cert = x509.load_pem_x509_certificate(ca_crt.read_bytes())
        key = serialization.load_pem_private_key(ca_key.read_bytes(), password=None)
        return cert, key, False
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SolBox")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False, data_encipherment=False,
                                     key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return cert, key, True


def make_self_signed(
    out_dir: Path, hosts: list[str], days: int = SERVER_DAYS, overwrite: bool = False, new_ca: bool = False
) -> tuple[Path, Path]:
    """`server.crt`(서버 인증서 + 발급자, PEM), `server.key` 를 만든다. IP 는 IP SAN, 이름은 DNS SAN, 첫 항목이 CN 이다.
    서버 인증서는 같은 폴더의 자체 발급자 `ca.crt`·`ca.key` 로 서명한다 (없으면 만들고, 있으면 다시 쓴다): 휴대폰·PC 에는 `ca.crt` 를 한 번
    "신뢰"로 설치하면 된다. 아이폰은 발급자 인증서만 신뢰를 켤 수 있고, 825일을 넘거나 서버 용도(serverAuth)가 없는 서버 인증서는 믿지 않는다
    (2026-11-27 휴대폰 접속 준비: 전에는 10년짜리 자체 서명 인증서 하나였다)."""
    hosts = [h.strip() for h in hosts if h and h.strip()]
    if not hosts:
        raise ValueError("접속 주소(IP 또는 이름)가 하나 이상 필요합니다.")
    for h in hosts:
        _check_host(h)
    days = min(days, SERVER_DAYS)
    out_dir = Path(out_dir)
    crt_path, key_path = out_dir / "server.crt", out_dir / "server.key"
    if not overwrite:
        for p in (crt_path, key_path):
            if p.exists():
                raise FileExistsError(str(p))

    ca_cert, ca_key, ca_is_new = _ca(out_dir, new_ca)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(min(now + timedelta(days=days), ca_cert.not_valid_after_utc))
        .add_extension(x509.SubjectAlternativeName([_san_entry(h) for h in hosts]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=True, data_encipherment=False,
                                     key_agreement=False, key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    crt_pem = cert.public_bytes(serialization.Encoding.PEM) + ca_cert.public_bytes(serialization.Encoding.PEM)  # 서버가 발급자까지 함께 보낸다
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
    if ca_is_new:  # 새 발급자는 서버 인증서·키를 다 쓴 뒤에 저장한다 (중간에 실패하면 아무것도 남지 않게). ca.key 는 소유자만 읽는다
        for path, data, private in ((out_dir / "ca.key", ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                                                              serialization.NoEncryption()), True),
                                    (out_dir / "ca.crt", ca_cert.public_bytes(serialization.Encoding.PEM), False)):
            path.unlink(missing_ok=True)
            if private:
                _write_private(path, data)
            else:
                path.write_bytes(data)
    return crt_path, key_path
