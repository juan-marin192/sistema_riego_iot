"""
mqtt_security.py - Módulo de seguridad MQTT para el sistema IoT de riego
=========================================================================
Proporciona:
  - Configuración TLS sobre paho-mqtt
  - Autenticación usuario/contraseña
  - Validación de comandos por allowlist y control de acceso
  - MQTTSecurityManager con reconexión automática
"""

import ssl
import time
import logging
import threading
from typing import Optional, Callable

import paho.mqtt.client as mqtt

from config import (
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_USER,
    MQTT_PASS,
    MQTT_CLIENT_ID,
    MQTT_KEEPALIVE,
    MQTT_USE_TLS,
    TLS_CONFIG,
    MQTT_TOPICS,
    AUTHORIZED_TELEGRAM_IDS,
    VALID_COMMANDS,
    LOG_LEVEL,
)

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mqtt_security")


# ============================================================================
# MAPA DE VERSIONES TLS
# ============================================================================

_TLS_VERSION_MAP = {
    "TLSv1_2": ssl.PROTOCOL_TLSv1_2,
    "TLSv1_3": ssl.PROTOCOL_TLS_CLIENT,   # Python >= 3.10
    "TLS":     ssl.PROTOCOL_TLS_CLIENT,
}


# ============================================================================
# FUNCIÓN: setup_mqtt_tls
# ============================================================================

def setup_mqtt_tls(client: mqtt.Client) -> None:
    """
    Configura TLS en el cliente paho-mqtt usando los certificados definidos
    en config.TLS_CONFIG.

    Args:
        client: Instancia de paho.mqtt.client.Client ya creada.

    Raises:
        FileNotFoundError: Si algún archivo de certificado no existe.
        ssl.SSLError: Si hay un error al configurar el contexto SSL.
    """
    tls_version_str = TLS_CONFIG.get("tls_version", "TLSv1_2")
    tls_version = _TLS_VERSION_MAP.get(tls_version_str, ssl.PROTOCOL_TLSv1_2)

    ca_certs   = TLS_CONFIG.get("ca_certs")
    certfile   = TLS_CONFIG.get("certfile")
    keyfile    = TLS_CONFIG.get("keyfile")
    insecure   = TLS_CONFIG.get("insecure", False)

    # Solo pasar certfile/keyfile si existen (autenticación mutua TLS opcional)
    _certfile = certfile if (certfile and _file_exists(certfile)) else None
    _keyfile  = keyfile  if (keyfile  and _file_exists(keyfile))  else None

    client.tls_set(
        ca_certs=ca_certs,
        certfile=_certfile,
        keyfile=_keyfile,
        tls_version=tls_version,
        cert_reqs=ssl.CERT_NONE if insecure else ssl.CERT_REQUIRED,
    )

    if insecure:
        client.tls_insecure_set(True)
        logger.warning(
            "TLS insecure mode ACTIVADO — solo para desarrollo con certs auto-firmados."
        )

    logger.info("TLS configurado correctamente (versión: %s)", tls_version_str)


def _file_exists(path: Optional[str]) -> bool:
    """Devuelve True si path es una cadena no vacía que apunta a un archivo."""
    if not path:
        return False
    import os
    return os.path.isfile(path)


# ============================================================================
# FUNCIÓN: setup_mqtt_auth
# ============================================================================

def setup_mqtt_auth(client: mqtt.Client) -> None:
    """
    Configura autenticación usuario/contraseña en el cliente paho-mqtt.
    Si MQTT_USER está vacío, registra una advertencia pero no falla
    (algunos brokers de desarrollo no requieren credenciales).

    Args:
        client: Instancia de paho.mqtt.client.Client ya creada.
    """
    if MQTT_USER and MQTT_PASS:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
        logger.info("Autenticación MQTT configurada para el usuario: %s", MQTT_USER)
    else:
        logger.warning(
            "MQTT_USER / MQTT_PASS no definidos — conexión sin autenticación. "
            "Configura las variables de entorno en producción."
        )


# ============================================================================
# FUNCIÓN: validate_command
# ============================================================================

def validate_command(command: str, user_id: str) -> bool:
    """
    Valida que un comando sea permitido y que el usuario esté autorizado.

    Reglas:
      1. user_id debe estar en AUTHORIZED_TELEGRAM_IDS.
      2. command (normalizado a minúsculas y sin espacios) debe estar en
         VALID_COMMANDS.

    Args:
        command: Texto del comando recibido desde Telegram.
        user_id: ID de chat/usuario de Telegram como string.

    Returns:
        True si el comando es válido y el usuario está autorizado.
    """
    # Control de acceso: verificar ID autorizado
    if str(user_id) not in AUTHORIZED_TELEGRAM_IDS:
        logger.warning(
            "Acceso denegado — ID no autorizado: %s intentó ejecutar '%s'",
            user_id,
            command,
        )
        return False

    # Validación del comando por allowlist
    command_normalized = command.strip().lower()
    if command_normalized not in VALID_COMMANDS:
        logger.warning(
            "Comando desconocido recibido de %s: '%s'", user_id, command
        )
        return False

    logger.info("Comando válido '%s' de usuario autorizado %s", command_normalized, user_id)
    return True


# ============================================================================
# FUNCIONES: VALIDACIÓN DE DISPOSITIVOS Y CREDENCIALES
# ============================================================================

KNOWN_DEVICES: list[str] = [
    "ESP32_Planta_Hogar",
    "ESP32_Planta_Invernadero",
]


def validate_device_identity(device_id: Optional[str]) -> tuple[bool, str]:
    """
    Valida la identidad del dispositivo IoT para evitar suplantación (spoofing).
    """
    if not device_id:
        logger.warning("Rechazo de telemetria: identificador de dispositivo vacio")
        return False, "Identificador de dispositivo vacío o ausente"

    if device_id not in KNOWN_DEVICES:
        logger.warning("Rechazo de dispositivo no registrado: '%s'", device_id)
        return False, f"Dispositivo no registrado o no autorizado: {device_id}"

    return True, f"Dispositivo autenticado: {device_id}"


def validate_mqtt_credentials(user: Optional[str], password: Optional[str]) -> tuple[bool, str]:
    """
    Valida las credenciales de conexión MQTT antes de establecer enlace.
    """
    if not user or not user.strip():
        return False, "Usuario MQTT vacío o ausente (conexión anónima no permitida)"
    if not password or len(password.strip()) < 6:
        return False, "Contraseña MQTT inválida o demasiado corta (mínimo 6 caracteres)"

    return True, "Credenciales MQTT sintácticamente válidas"


# ============================================================================
# CLASE: MQTTSecurityManager
# ============================================================================

class MQTTSecurityManager:
    """
    Gestiona conexiones MQTT seguras con soporte para:
      - TLS (opcional, controlado por config.MQTT_USE_TLS)
      - Autenticación usuario/contraseña
      - Reconexión automática con backoff exponencial
      - Registro de callbacks de conexión/mensaje
    """

    # Backoff: esperas en segundos entre intentos de reconexión
    _RECONNECT_DELAYS = [1, 2, 5, 10, 30, 60]

    def __init__(
        self,
        client_id: str = MQTT_CLIENT_ID,
        on_message_callback: Optional[Callable] = None,
        on_connect_callback: Optional[Callable] = None,
    ) -> None:
        self.client_id = client_id
        self._on_message_cb = on_message_callback
        self._on_connect_cb = on_connect_callback
        self._reconnect_attempt = 0
        self._running = False
        self._lock = threading.Lock()

        self.client = mqtt.Client(client_id=self.client_id)
        self._configure_client()

    # ------------------------------------------------------------------
    # CONFIGURACIÓN INTERNA
    # ------------------------------------------------------------------

    def _configure_client(self) -> None:
        """Aplica TLS, autenticación y registra callbacks internos."""
        # Autenticación
        setup_mqtt_auth(self.client)

        # TLS
        if MQTT_USE_TLS:
            setup_mqtt_tls(self.client)
        else:
            logger.warning(
                "TLS DESHABILITADO (MQTT_USE_TLS=false). "
                "Solo aceptable en entornos de desarrollo locales."
            )

        # Callbacks
        self.client.on_connect    = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message    = self._on_message

    # ------------------------------------------------------------------
    # CALLBACKS MQTT
    # ------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc: int) -> None:
        if rc == 0:
            self._reconnect_attempt = 0
            logger.info("Conexión MQTT establecida con %s", MQTT_BROKER)
            # Suscribirse a telemetría al conectar
            client.subscribe(MQTT_TOPICS["telemetria"])
            logger.info("Suscripción activa: %s", MQTT_TOPICS["telemetria"])
            if self._on_connect_cb:
                self._on_connect_cb(client, userdata, flags, rc)
        else:
            logger.error("Fallo en conexión MQTT, código de retorno: %d", rc)

    def _on_disconnect(self, client, userdata, rc: int) -> None:
        if rc != 0:
            logger.warning("Desconexión inesperada del broker MQTT (rc=%d)", rc)
            self._schedule_reconnect()

    def _on_message(self, client, userdata, msg) -> None:
        logger.debug("Mensaje recibido en tópico '%s'", msg.topic)
        if self._on_message_cb:
            self._on_message_cb(client, userdata, msg)

    # ------------------------------------------------------------------
    # RECONEXIÓN AUTOMÁTICA
    # ------------------------------------------------------------------

    def _schedule_reconnect(self) -> None:
        """Lanza hilo de reconexión con backoff exponencial."""
        t = threading.Thread(target=self._reconnect_loop, daemon=True)
        t.start()

    def _reconnect_loop(self) -> None:
        """Intenta reconectar con backoff hasta que lo logre o se detenga."""
        with self._lock:
            if not self._running:
                return

        while self._running:
            delay = self._RECONNECT_DELAYS[
                min(self._reconnect_attempt, len(self._RECONNECT_DELAYS) - 1)
            ]
            logger.info(
                "Reintento de conexión #%d en %ds...",
                self._reconnect_attempt + 1,
                delay,
            )
            time.sleep(delay)
            self._reconnect_attempt += 1
            try:
                self.client.reconnect()
                logger.info("Reconexión exitosa.")
                break
            except Exception as exc:
                logger.error("Error al reconectar: %s", exc)

    # ------------------------------------------------------------------
    # API PÚBLICA
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Establece la conexión con el broker MQTT."""
        self._running = True
        port = MQTT_PORT if MQTT_USE_TLS else 1883
        logger.info(
            "Conectando a %s:%d (TLS=%s)...", MQTT_BROKER, port, MQTT_USE_TLS
        )
        self.client.connect(MQTT_BROKER, port, MQTT_KEEPALIVE)

    def start_loop(self) -> None:
        """Inicia el loop de red MQTT en segundo plano."""
        self.client.loop_start()

    def stop(self) -> None:
        """Detiene el loop y desconecta limpiamente."""
        with self._lock:
            self._running = False
        self.client.loop_stop()
        self.client.disconnect()
        logger.info("MQTTSecurityManager detenido.")

    def publish(self, topic: str, payload: str, qos: int = 1) -> None:
        """
        Publica un mensaje en el broker.

        Args:
            topic:   Tópico MQTT de destino.
            payload: Contenido del mensaje.
            qos:     Nivel de calidad de servicio (0, 1 ó 2).
        """
        result = self.client.publish(topic, payload, qos=qos)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            logger.error(
                "Error al publicar en '%s': código %d", topic, result.rc
            )
        else:
            logger.debug("Mensaje publicado en '%s': %s", topic, payload)

    def get_client(self) -> mqtt.Client:
        """Devuelve la instancia interna de paho-mqtt Client."""
        return self.client
