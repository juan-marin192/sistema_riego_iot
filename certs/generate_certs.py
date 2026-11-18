#!/usr/bin/env python3
"""
generate_certs.py - Generador de Certificados TLS para Desarrollo Local
========================================================================
Genera una Autoridad Certificadora (CA) propia, certificado de servidor
(Broker MQTT Mosquitto) y certificado de cliente (Backend Python / ESP32).
Compatible con Windows, Linux y macOS sin requerir el binario externo openssl.
"""

import os
import datetime
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

CERTS_DIR = os.path.dirname(os.path.abspath(__file__))

def generate_key():
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

def save_key(key, filename):
    path = os.path.join(CERTS_DIR, filename)
    with open(path, "wb") as f:
        f.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    print(f"  [+] Clave privada guardada: {filename}")
    return path

def save_cert(cert, filename):
    path = os.path.join(CERTS_DIR, filename)
    with open(path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    print(f"  [+] Certificado guardado:   {filename}")
    return path

def main():
    print("=== Generador de Certificados TLS para Desarrollo IoT ===")
    now = datetime.datetime.now(datetime.timezone.utc)
    one_day = datetime.timedelta(days=1)
    ten_years = datetime.timedelta(days=3650)

    # 1. Generar CA Raíz
    print("\n[1/3] Generando CA Raiz (Certificate Authority)...")
    ca_key = generate_key()
    save_key(ca_key, "ca.key")

    ca_name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "ES"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Madrid"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "IoT Riego Automatico CA"),
        x509.NameAttribute(NameOID.COMMON_NAME, "IoT-Riego-Root-CA"),
    ])

    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - one_day)
        .not_valid_after(now + ten_years)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    save_cert(ca_cert, "ca.crt")

    # 2. Generar Certificado de Servidor (Mosquitto Broker)
    print("\n[2/3] Generando Certificado de Servidor (Mosquitto Broker)...")
    server_key = generate_key()
    save_key(server_key, "server.key")

    server_name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "ES"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Madrid"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "IoT Riego Broker"),
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])

    import ipaddress
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - one_day)
        .not_valid_after(now + ten_years)
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.DNSName("broker.local"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    save_cert(server_cert, "server.crt")

    # 3. Generar Certificado de Cliente (Backend Python / ESP32)
    print("\n[3/3] Generando Certificado de Cliente (Backend / ESP32)...")
    client_key = generate_key()
    save_key(client_key, "client.key")

    client_name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "ES"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Madrid"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "IoT Riego Client"),
        x509.NameAttribute(NameOID.COMMON_NAME, "backend_listener"),
    ])

    client_cert = (
        x509.CertificateBuilder()
        .subject_name(client_name)
        .issuer_name(ca_name)
        .public_key(client_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - one_day)
        .not_valid_after(now + ten_years)
        .sign(ca_key, hashes.SHA256())
    )
    save_cert(client_cert, "client.crt")

    print("\n[+] Todos los certificados fueron generados con exito en:", CERTS_DIR)

if __name__ == "__main__":
    main()
