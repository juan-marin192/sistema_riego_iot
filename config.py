"""
config.py - Configuración segura del sistema IoT de riego automático
=====================================================================
Carga credenciales y parámetros desde variables de entorno o archivo .env
NUNCA hardcodear credenciales en este archivo.
"""

import os
import logging

# Intenta cargar python-dotenv si está disponible
try:
    from dotenv import load_dotenv
    load_dotenv()  # Lee el archivo .env en el directorio de trabajo
    _DOTENV_LOADED = True
except ImportError:
    _DOTENV_LOADED = False
    logging.warning(
        "python-dotenv no está instalado. "
        "Las variables deben exportarse manualmente en el entorno."
    )


# ============================================================================
# FUNCIÓN DE CARGA SEGURA DE VARIABLE DE ENTORNO
# ============================================================================

def _get_env(var_name: str, default: str = None, required: bool = False) -> str:
    """
    Obtiene una variable de entorno. Lanza ValueError si es requerida
    y no está definida.
    """
    value = os.environ.get(var_name, default)
    if required and not value:
        raise ValueError(
            f"Variable de entorno requerida '{var_name}' no está definida. "
            f"Revisa tu archivo .env o las variables del sistema."
        )
    return value


# ============================================================================
# CREDENCIALES MQTT
# ============================================================================

MQTT_BROKER: str = _get_env("MQTT_BROKER", "broker.emqx.io")
MQTT_PORT: int = int(_get_env("MQTT_PORT", "8883"))   # 8883 = TLS, 1883 = sin TLS
MQTT_PORT_TLS: int = 8883
MQTT_PORT_PLAIN: int = 1883
MQTT_USER: str = _get_env("MQTT_USER", "")            # Usuario MQTT (requerido en producción)
MQTT_PASS: str = _get_env("MQTT_PASS", "")            # Contraseña MQTT (requerida en producción)
MQTT_CLIENT_ID: str = _get_env("MQTT_CLIENT_ID", "VSCode_Backend_Listener")
MQTT_KEEPALIVE: int = int(_get_env("MQTT_KEEPALIVE", "60"))
MQTT_USE_TLS: bool = _get_env("MQTT_USE_TLS", "true").lower() in ("true", "1", "yes")

# ============================================================================
# CREDENCIALES TELEGRAM
# ============================================================================

TELEGRAM_BOT_TOKEN: str = _get_env("TELEGRAM_BOT_TOKEN", required=True)
TELEGRAM_CHAT_ID: str = _get_env("TELEGRAM_CHAT_ID", required=True)

# IDs de Telegram autorizados para enviar comandos al sistema
# Separados por coma en la variable de entorno AUTHORIZED_TELEGRAM_IDS
_raw_ids = _get_env("AUTHORIZED_TELEGRAM_IDS", TELEGRAM_CHAT_ID)
AUTHORIZED_TELEGRAM_IDS: list[str] = [
    uid.strip() for uid in _raw_ids.split(",") if uid.strip()
]

# ============================================================================
# CONFIGURACIÓN TLS
# ============================================================================

CERTS_DIR: str = _get_env("CERTS_DIR", os.path.join(os.path.dirname(__file__), "certs"))

TLS_CONFIG = {
    "ca_certs":    _get_env("TLS_CA_CERT",     os.path.join(CERTS_DIR, "ca.crt")),
    "certfile":    _get_env("TLS_CLIENT_CERT", os.path.join(CERTS_DIR, "client.crt")),
    "keyfile":     _get_env("TLS_CLIENT_KEY",  os.path.join(CERTS_DIR, "client.key")),
    # CERT_REQUIRED (ssl.CERT_REQUIRED) se aplica en mqtt_security.py
    "tls_version": _get_env("TLS_VERSION", "TLSv1_2"),
    # Deshabilitar verificación de hostname solo en desarrollo con certs auto-firmados
    "insecure":    _get_env("TLS_INSECURE", "false").lower() in ("true", "1", "yes"),
}

# ============================================================================
# TÓPICOS MQTT
# ============================================================================

MQTT_TOPIC_PREFIX: str = _get_env("MQTT_TOPIC_PREFIX", "hogar/planta01")

MQTT_TOPICS = {
    "telemetria": f"{MQTT_TOPIC_PREFIX}/telemetria",
    "control":    f"{MQTT_TOPIC_PREFIX}/control",
    "status":     f"{MQTT_TOPIC_PREFIX}/status",
    "alerts":     f"{MQTT_TOPIC_PREFIX}/alertas",
}

# Alias directos para compatibilidad con el código existente
MQTT_TOPIC_DATA: str = MQTT_TOPICS["telemetria"]
MQTT_TOPIC_CTRL: str = MQTT_TOPICS["control"]

# ============================================================================
# BASE DE DATOS
# ============================================================================

DB_NAME: str = _get_env("DB_NAME", "riego_iot.db")

# ============================================================================
# COMANDOS VÁLIDOS (ALLOWLIST)
# ============================================================================

VALID_COMMANDS: list[str] = [
    "/riego_on",
    "/riego_off",
    "/estado",
    "/start",
    "/ayuda",
    "/help",
]

# ============================================================================
# LOGGING
# ============================================================================

LOG_LEVEL: str = _get_env("LOG_LEVEL", "INFO").upper()
LOG_FILE: str = _get_env("LOG_FILE", "riego_iot.log")
