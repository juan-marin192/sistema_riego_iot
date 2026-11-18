#!/usr/bin/env bash
# =============================================================================
# generate_certs.sh
# Genera certificados TLS auto-firmados para desarrollo local
# Sistema IoT de Riego Automático
# =============================================================================
#
# REQUISITOS:
#   - OpenSSL >= 1.1.1 instalado y en el PATH
#
# USO:
#   chmod +x certs/generate_certs.sh
#   cd certs/
#   bash generate_certs.sh
#
# ADVERTENCIA:
#   Los certificados generados por este script son SOLO para desarrollo.
#   En producción usa certificados firmados por una CA confiable
#   (Let's Encrypt, DigiCert, etc.) o el CA de tu organización.
#
# ARCHIVOS GENERADOS:
#   ca.key        - Clave privada de la CA (guárdala con cuidado)
#   ca.crt        - Certificado raíz de la CA (distribúyelo al broker y clientes)
#   server.key    - Clave privada del servidor Mosquitto
#   server.csr    - Solicitud de firma del servidor
#   server.crt    - Certificado del servidor firmado por la CA
#   client.key    - Clave privada del cliente Python (backend)
#   client.csr    - Solicitud de firma del cliente
#   client.crt    - Certificado del cliente firmado por la CA
# =============================================================================

set -euo pipefail

# --- Parámetros configurables ------------------------------------------------

DAYS_VALID=3650          # Validez de los certificados (10 años para desarrollo)
KEY_BITS=2048            # Tamaño de clave RSA
COUNTRY="ES"
STATE="Madrid"
LOCALITY="Madrid"
ORG="IoT Riego Automatico"
CA_CN="IoT-Riego-CA"
SERVER_CN="broker.local"     # Debe coincidir con el hostname del broker en producción
CLIENT_CN="backend_listener"

# --- Colores para output -----------------------------------------------------

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Generador de Certificados TLS para Desarrollo ===${NC}"
echo ""

# --- Verificar OpenSSL -------------------------------------------------------

if ! command -v openssl &>/dev/null; then
    echo -e "${RED}ERROR: OpenSSL no está instalado o no está en el PATH.${NC}"
    echo "Instálalo con:"
    echo "  Ubuntu/Debian: sudo apt install openssl"
    echo "  macOS:         brew install openssl"
    echo "  Windows:       https://slproweb.com/products/Win32OpenSSL.html"
    exit 1
fi

OPENSSL_VER=$(openssl version)
echo -e "OpenSSL detectado: ${GREEN}${OPENSSL_VER}${NC}"
echo ""

# --- 1. Generar CA (Certificate Authority) -----------------------------------

echo -e "${YELLOW}[1/6] Generando clave privada de la CA...${NC}"
openssl genrsa -out ca.key ${KEY_BITS}

echo -e "${YELLOW}[2/6] Generando certificado raíz de la CA...${NC}"
openssl req -new -x509 -days ${DAYS_VALID} \
    -key ca.key \
    -out ca.crt \
    -subj "/C=${COUNTRY}/ST=${STATE}/L=${LOCALITY}/O=${ORG}/CN=${CA_CN}"

echo -e "${GREEN}  ✓ ca.key y ca.crt generados${NC}"

# --- 2. Generar certificado del Servidor (Mosquitto) -------------------------

echo -e "${YELLOW}[3/6] Generando clave y CSR del servidor Mosquitto...${NC}"
openssl genrsa -out server.key ${KEY_BITS}
openssl req -new \
    -key server.key \
    -out server.csr \
    -subj "/C=${COUNTRY}/ST=${STATE}/L=${LOCALITY}/O=${ORG}/CN=${SERVER_CN}"

echo -e "${YELLOW}[4/6] Firmando certificado del servidor con la CA...${NC}"
openssl x509 -req -days ${DAYS_VALID} \
    -in server.csr \
    -CA ca.crt \
    -CAkey ca.key \
    -CAcreateserial \
    -out server.crt

echo -e "${GREEN}  ✓ server.key y server.crt generados${NC}"

# --- 3. Generar certificado del Cliente (Backend Python) ---------------------

echo -e "${YELLOW}[5/6] Generando clave y CSR del cliente (backend)...${NC}"
openssl genrsa -out client.key ${KEY_BITS}
openssl req -new \
    -key client.key \
    -out client.csr \
    -subj "/C=${COUNTRY}/ST=${STATE}/L=${LOCALITY}/O=${ORG}/CN=${CLIENT_CN}"

echo -e "${YELLOW}[6/6] Firmando certificado del cliente con la CA...${NC}"
openssl x509 -req -days ${DAYS_VALID} \
    -in client.csr \
    -CA ca.crt \
    -CAkey ca.key \
    -CAcreateserial \
    -out client.crt

echo -e "${GREEN}  ✓ client.key y client.crt generados${NC}"

# --- Resumen -----------------------------------------------------------------

echo ""
echo -e "${GREEN}=== Certificados generados correctamente ===${NC}"
echo ""
echo "Archivos creados en $(pwd):"
ls -la ca.crt ca.key server.crt server.key client.crt client.key 2>/dev/null
echo ""
echo -e "${YELLOW}PRÓXIMOS PASOS:${NC}"
echo "1. Copia ca.crt, server.crt y server.key al directorio de configuración de Mosquitto."
echo "2. Añade a mosquitto.conf:"
echo "     listener 8883"
echo "     cafile   /ruta/certs/ca.crt"
echo "     certfile /ruta/certs/server.crt"
echo "     keyfile  /ruta/certs/server.key"
echo "     require_certificate false   # true para mTLS (autenticación mutua)"
echo ""
echo "3. Actualiza tu .env con las rutas de los certificados del cliente:"
echo "     TLS_CA_CERT=./certs/ca.crt"
echo "     TLS_CLIENT_CERT=./certs/client.crt"
echo "     TLS_CLIENT_KEY=./certs/client.key"
echo "     TLS_INSECURE=true   # solo para certs auto-firmados en desarrollo"
echo ""
echo -e "${RED}ADVERTENCIA: Protege ca.key con permisos estrictos (chmod 600 ca.key).${NC}"
echo -e "${RED}NUNCA subas las claves privadas (*.key) a un repositorio Git.${NC}"
