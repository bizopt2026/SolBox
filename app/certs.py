"""자체 서명 HTTPS 인증서 만들기."""
from __future__ import annotations

import ipaddress
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


def make_self_signed(
    out_dir: Path, hosts: list[str], days: int = 3650, overwrite: bool = False
) -> tuple[Path, Path]:
    """`server.crt`, `server.key` (PEM, RSA 2048) 를 만든다. IP 는 IP SAN, 이름은 DNS SAN, 첫 항목이 CN 이다."""
    hosts = [h.strip() for h in hosts if h and h.strip()]
    if not hosts:
        raise ValueError("접속 주소(IP 또는 이름)가 하나 이상 필요합니다.")
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
    crt_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    return crt_path, key_path
