"""
telegram_bot.py — FASE 4: Integración avanzada de Telegram para el sistema IoT de riego.

Características:
  - Polling manual con requests (sin python-telegram-bot)
  - Menú con botones inline (InlineKeyboardMarkup)
  - Autorización multi-usuario con AUTHORIZED_CHAT_IDS
  - Confirmación para comandos críticos (riego_on / riego_off)
  - Historial de operaciones en memoria (máx. 200)
  - Notificaciones automáticas a todos los usuarios autorizados
  - Cliente MQTT integrado (paho-mqtt)
  - Consulta a la misma base de datos SQLite: riego_iot.db
"""

import os
import json
import time
import sqlite3
import threading
import logging
from datetime import datetime
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

import requests
import paho.mqtt.client as mqtt

# ==============================================================================
# CONFIGURACIÓN DE LOGGING
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

# ==============================================================================
# CONFIGURACIÓN SEGURA — Variables de entorno con fallback para MQTT
# ==============================================================================

TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_raw_chat_ids: str = (
    os.environ.get("AUTHORIZED_CHAT_IDS")
    or os.environ.get("AUTHORIZED_TELEGRAM_IDS")
    or os.environ.get("TELEGRAM_CHAT_ID", "")
)

# Lista de IDs autorizados (integers). Si la variable de entorno está vacía se
# usa una lista vacía y nadie podrá interactuar con el bot.
AUTHORIZED_CHAT_IDS: list[int] = [
    int(cid.strip())
    for cid in _raw_chat_ids.split(",")
    if cid.strip().lstrip("-").isdigit()
]

# MQTT: fallback a valores públicos del proyecto original
MQTT_BROKER: str = os.environ.get("MQTT_BROKER", "broker.emqx.io")
MQTT_PORT: int = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TOPIC_DATA: str = os.environ.get("MQTT_TOPIC_DATA", "hogar/planta01/telemetria")
MQTT_TOPIC_CTRL: str = os.environ.get("MQTT_TOPIC_CTRL", "hogar/planta01/control")

DB_NAME: str = os.environ.get("DB_NAME", "riego_iot.db")

# Ruta absoluta a la BD (misma carpeta que este script)
_script_dir = os.path.dirname(os.path.abspath(__file__))
DB_PATH: str = os.path.join(_script_dir, DB_NAME)

# Umbrales de alarma
ALARM_VWC_MIN: float = 20.0   # % VWC mínimo
ALARM_VWC_MAX: float = 80.0   # % VWC máximo
ALARM_TEMP_MAX: float = 35.0  # °C temperatura máxima

# ==============================================================================
# ESTADO GLOBAL MQTT
# ==============================================================================

mqtt_connected: bool = False
mqtt_client: mqtt.Client | None = None

# Última telemetría recibida por MQTT (para detectar cambio de bomba)
_last_telemetry: dict = {}

# ==============================================================================
# REGISTRO DE OPERACIONES EN MEMORIA
# ==============================================================================

operations_log: list[dict] = []
_ops_lock = threading.Lock()
MAX_OPS_LOG = 200


def log_operation(chat_id: int, command: str, result: str) -> None:
    """Registra una operación en el historial en memoria."""
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "chat_id": chat_id,
        "command": command,
        "result": result,
    }
    with _ops_lock:
        operations_log.append(entry)
        if len(operations_log) > MAX_OPS_LOG:
            operations_log.pop(0)
    log.info(f"[OPS] chat={chat_id} cmd={command} -> {result}")


# ==============================================================================
# BASE DE DATOS SQLite
# ==============================================================================

def init_db() -> None:
    """Crea la tabla telemetria si no existe (compatibilidad con listener_iot_riego.py)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS telemetria (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_hora       TEXT,
            dispositivo      TEXT,
            humedad_vwc      REAL,
            umbral_trigger   REAL,
            temperatura      REAL,
            humedad_aire     REAL,
            vpd_kpa          REAL,
            bomba_estado     TEXT,
            alerta_timeout   INTEGER
        )
    ''')
    conn.commit()
    conn.close()
    log.info("Base de datos SQLite verificada/inicializada.")


def get_last_reading() -> tuple | None:
    """Devuelve la última fila de telemetría o None."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT fecha_hora, dispositivo, humedad_vwc, umbral_trigger,
                   temperatura, humedad_aire, vpd_kpa, bomba_estado, alerta_timeout
            FROM telemetria
            ORDER BY id DESC LIMIT 1
        ''')
        row = cursor.fetchone()
        conn.close()
        return row
    except Exception as exc:
        log.error(f"Error leyendo última lectura: {exc}")
        return None


def get_last_n_readings(n: int = 10) -> list[tuple]:
    """Devuelve las últimas n filas de telemetría."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT fecha_hora, dispositivo, humedad_vwc, umbral_trigger,
                   temperatura, humedad_aire, vpd_kpa, bomba_estado, alerta_timeout
            FROM telemetria
            ORDER BY id DESC LIMIT ?
        ''', (n,))
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as exc:
        log.error(f"Error leyendo {n} lecturas: {exc}")
        return []


def check_alarms(data: dict) -> list[str]:
    """
    Evalúa umbrales y devuelve lista de cadenas de alarma.
    data: dict con claves humedad_vwc, temperatura.
    """
    alarms: list[str] = []
    vwc = data.get("humedad_vwc")
    temp = data.get("temperatura")

    if vwc is not None:
        if vwc < ALARM_VWC_MIN:
            alarms.append(
                f"⚠️ Humedad VWC muy baja: {vwc:.1f}% (mín {ALARM_VWC_MIN}%)"
            )
        elif vwc > ALARM_VWC_MAX:
            alarms.append(
                f"⚠️ Humedad VWC muy alta: {vwc:.1f}% (máx {ALARM_VWC_MAX}%)"
            )
    if temp is not None and temp > ALARM_TEMP_MAX:
        alarms.append(
            f"🌡️ Temperatura crítica: {temp:.1f}°C (máx {ALARM_TEMP_MAX}°C)"
        )
    return alarms


# ==============================================================================
# AUTORIZACIÓN
# ==============================================================================

def is_authorized(chat_id: int) -> bool:
    """Devuelve True si chat_id está en AUTHORIZED_CHAT_IDS."""
    return chat_id in AUTHORIZED_CHAT_IDS


def reject_unauthorized(chat_id: int) -> None:
    """Envía mensaje de rechazo y registra el intento."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.warning(f"[AUTH] Intento no autorizado | timestamp={ts} | chat_id={chat_id}")
    send_message(
        chat_id,
        "🚫 *Acceso denegado.*\n"
        "No tienes autorización para interactuar con este bot.\n"
        f"Tu ID: `{chat_id}`\n"
        "Contacta al administrador del sistema.",
    )


# ==============================================================================
# CLIENTE MQTT
# ==============================================================================

def publish_command(command: str) -> bool:
    """Publica 'ON' o 'OFF' en el topic de control MQTT."""
    global mqtt_client, mqtt_connected
    if mqtt_client is None or not mqtt_connected:
        log.warning("Intento de publicar con cliente MQTT no conectado.")
        return False
    result = mqtt_client.publish(MQTT_TOPIC_CTRL, command)
    success = result.rc == mqtt.MQTT_ERR_SUCCESS
    log.info(f"[MQTT] Publicado '{command}' en {MQTT_TOPIC_CTRL} -> {'OK' if success else 'ERROR'}")
    return success


def _on_connect(client, userdata, flags, rc):
    global mqtt_connected
    if rc == 0:
        mqtt_connected = True
        log.info(f"[MQTT] Conectado a {MQTT_BROKER}:{MQTT_PORT}")
        client.subscribe(MQTT_TOPIC_DATA)
        log.info(f"[MQTT] Suscrito a {MQTT_TOPIC_DATA}")
        notify_all_authorized("🟢 *Bot IoT conectado* al broker MQTT.\nSistema en línea.")
    else:
        mqtt_connected = False
        log.error(f"[MQTT] Fallo de conexión rc={rc}")


def _on_disconnect(client, userdata, rc):
    global mqtt_connected
    mqtt_connected = False
    log.warning(f"[MQTT] Desconectado rc={rc}")
    notify_all_authorized("🔴 *Desconexión del broker MQTT.*\nIntentando reconexión…")


def _on_message(client, userdata, msg):
    global _last_telemetry
    try:
        payload_str = msg.payload.decode("utf-8")
        data = json.loads(payload_str)

        prev_bomba = _last_telemetry.get("bomba_estado", "OFF")
        curr_bomba = str(data.get("bomba_estado", "OFF"))
        vwc = data.get("humedad_vwc", 0)

        # Cambio de estado de bomba
        if curr_bomba == "ON" and prev_bomba == "OFF":
            notify_all_authorized(
                f"💧 *RIEGO INICIADO*\nHumedad VWC: {vwc}%\n"
                "Actuador energizado automáticamente."
            )
        elif curr_bomba == "OFF" and prev_bomba == "ON":
            notify_all_authorized(
                f"✅ *RIEGO FINALIZADO*\nHumedad VWC: {vwc}%\n"
                "Actuador desenergizado."
            )

        # Alarmas
        alarms = check_alarms(data)
        if alarms:
            alarm_text = "\n".join(alarms)
            notify_all_authorized(f"🔔 *ALARMAS ACTIVAS*\n{alarm_text}")

        _last_telemetry = data

    except Exception as exc:
        log.error(f"[MQTT] Error procesando mensaje: {exc}")


def _init_mqtt_client() -> mqtt.Client:
    """Crea y configura el cliente MQTT."""
    global mqtt_client
    client_id = f"Telegram_Bot_{int(time.time())}"
    client = mqtt.Client(client_id=client_id)
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message
    mqtt_client = client
    return client


# ==============================================================================
# API TELEGRAM — Funciones de bajo nivel
# ==============================================================================

_TG_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def send_message(chat_id: int, text: str) -> dict | None:
    """Envía mensaje de texto simple (Markdown) con fallback automático ante error 400."""
    try:
        url = f"{_TG_BASE}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            return res.json()

        # Fallback si falla el parseo de Markdown (código 400: can't parse entities)
        if res.status_code == 400 and "can't parse entities" in res.text:
            log.warning(f"[TG] Error de entidades Markdown en chat {chat_id}. Reintentando sin parse_mode como fallback.")
            payload_plain = {
                "chat_id": chat_id,
                "text": text,
            }
            res_plain = requests.post(url, json=payload_plain, timeout=10)
            if res_plain.status_code == 200:
                log.info(f"[TG] Mensaje entregado con éxito usando fallback sin formato.")
                return res_plain.json()
            else:
                log.warning(f"[TG] Fallback sendMessage HTTP {res_plain.status_code}: {res_plain.text[:200]}")
                return res_plain.json()

        log.warning(f"[TG] sendMessage HTTP {res.status_code}: {res.text[:200]}")
        return res.json()
    except Exception as exc:
        log.error(f"[TG] Error en send_message: {exc}")
        return None


def send_message_with_menu(chat_id: int, text: str, keyboard: dict) -> dict | None:
    """Envía mensaje con InlineKeyboardMarkup y fallback automático ante error 400."""
    try:
        url = f"{_TG_BASE}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": json.dumps(keyboard),
        }
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            return res.json()

        # Fallback si falla el parseo de Markdown (código 400: can't parse entities)
        if res.status_code == 400 and "can't parse entities" in res.text:
            log.warning(f"[TG] Error de entidades Markdown en chat {chat_id}. Reintentando sin parse_mode como fallback.")
            payload_plain = {
                "chat_id": chat_id,
                "text": text,
                "reply_markup": json.dumps(keyboard),
            }
            res_plain = requests.post(url, json=payload_plain, timeout=10)
            if res_plain.status_code == 200:
                log.info(f"[TG] Mensaje con menú entregado con éxito usando fallback sin formato.")
                return res_plain.json()
            else:
                log.warning(f"[TG] Fallback sendMessage+menu HTTP {res_plain.status_code}: {res_plain.text[:200]}")
                return res_plain.json()

        log.warning(f"[TG] sendMessage+menu HTTP {res.status_code}: {res.text[:200]}")
        return res.json()
    except Exception as exc:
        log.error(f"[TG] Error en send_message_with_menu: {exc}")
        return None


def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    """Responde al callback query para quitar el 'cargando' del botón."""
    try:
        url = f"{_TG_BASE}/answerCallbackQuery"
        payload = {"callback_query_id": callback_query_id, "text": text}
        requests.post(url, json=payload, timeout=5)
    except Exception as exc:
        log.error(f"[TG] Error en answer_callback_query: {exc}")


def notify_all_authorized(message: str) -> None:
    """Envía un mensaje a todos los IDs autorizados."""
    for cid in AUTHORIZED_CHAT_IDS:
        send_message(cid, message)


# ==============================================================================
# CONSTRUCCIÓN DE TECLADOS INLINE
# ==============================================================================

def build_main_menu() -> dict:
    """Devuelve el InlineKeyboardMarkup del menú principal."""
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Estado del Sistema", "callback_data": "estado"},
                {"text": "🌡️ Telemetría Actual",  "callback_data": "telemetria"},
            ],
            [
                {"text": "🔌 Estado Broker MQTT", "callback_data": "broker"},
                {"text": "🔔 Alarmas Activas",    "callback_data": "alarmas"},
            ],
            [
                {"text": "💧 Encender Riego", "callback_data": "riego_on_confirm"},
                {"text": "🛑 Apagar Riego",   "callback_data": "riego_off_confirm"},
            ],
            [
                {"text": "📋 Historial", "callback_data": "historial"},
                {"text": "❓ Ayuda",     "callback_data": "ayuda"},
            ],
        ]
    }


def build_confirm_menu(action: str) -> dict:
    """Devuelve teclado de confirmación para encender/apagar."""
    confirm_cb = "confirm_on" if action == "on" else "confirm_off"
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Confirmar", "callback_data": confirm_cb},
                {"text": "❌ Cancelar", "callback_data": "cancel"},
            ]
        ]
    }


# ==============================================================================
# FORMATEO DE RESPUESTAS
# ==============================================================================

def _format_estado(row: tuple) -> str:
    fecha, disp, vwc, trig, temp, hum_a, vpd, bomba, timeout = row
    bomba_icon = "💧 ON" if bomba == "ON" else "⛔ OFF"
    return (
        f"*📊 ESTADO ACTUAL DEL SISTEMA IoT*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Fecha/Hora: `{fecha}`\n"
        f"📟 Dispositivo: `{disp}`\n"
        f"💦 Humedad VWC: `{vwc:.1f}%`\n"
        f"🎯 Umbral MAD: `{trig:.1f}%`\n"
        f"🌡️ Temperatura: `{temp:.1f}°C`\n"
        f"💨 Humedad Aire: `{hum_a:.1f}%`\n"
        f"📉 VPD: `{vpd:.2f} kPa`\n"
        f"🔌 Bomba: *{bomba_icon}*\n"
        f"⏱️ Alerta Timeout: {'⚠️ SÍ' if timeout else '✅ NO'}"
    )


def _format_telemetria_list(rows: list[tuple]) -> str:
    if not rows:
        return "⚠️ No hay registros en la base de datos."
    lines = ["*🌡️ ÚLTIMAS LECTURAS DE TELEMETRÍA*\n━━━━━━━━━━━━━━━━━━━━"]
    for i, row in enumerate(rows, 1):
        fecha, _, vwc, _, temp, hum_a, vpd, bomba, _ = row
        lines.append(
            f"\n*#{i}* `{fecha}`\n"
            f"  💦 VWC: `{vwc:.1f}%` | 🌡️ Temp: `{temp:.1f}°C` | "
            f"💨 HR: `{hum_a:.1f}%` | 🔌 Bomba: `{bomba}`"
        )
    return "\n".join(lines)


def _format_historial(rows: list[tuple]) -> str:
    if not rows:
        return "⚠️ No hay registros en la base de datos."
    lines = ["*📋 HISTORIAL — ÚLTIMOS 10 REGISTROS*\n━━━━━━━━━━━━━━━━━━━━"]
    for i, row in enumerate(rows, 1):
        fecha, _, vwc, trig, temp, hum_a, vpd, bomba, timeout = row
        lines.append(
            f"\n*#{i}* `{fecha}`\n"
            f"  VWC:`{vwc:.1f}%` Tmp:`{temp:.1f}°C` Bomba:`{bomba}` "
            f"{'⚠️' if timeout else ''}"
        )
    return "\n".join(lines)


# ==============================================================================
# MANEJADORES DE COMANDOS / CALLBACKS
# ==============================================================================

def handle_start(chat_id: int) -> None:
    text = (
        "🌱 *Sistema IoT de Riego — Telemando*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Selecciona una opción del menú:"
    )
    send_message_with_menu(chat_id, text, build_main_menu())
    log_operation(chat_id, "/start", "menú principal enviado")


def handle_estado(chat_id: int) -> None:
    row = get_last_reading()
    if row:
        text = _format_estado(row)
    else:
        text = "⚠️ No hay registros disponibles en la base de datos."
    send_message_with_menu(chat_id, text, build_main_menu())
    log_operation(chat_id, "/estado", "estado enviado")


def handle_telemetria(chat_id: int) -> None:
    rows = get_last_n_readings(5)
    text = _format_telemetria_list(rows)
    send_message_with_menu(chat_id, text, build_main_menu())
    log_operation(chat_id, "/telemetria", f"{len(rows)} lecturas enviadas")


def handle_broker(chat_id: int) -> None:
    if mqtt_connected:
        status = "🟢 *CONECTADO*"
        detail = f"Broker: `{MQTT_BROKER}:{MQTT_PORT}`"
    else:
        status = "🔴 *DESCONECTADO*"
        detail = "El cliente MQTT no tiene conexión activa."
    text = f"*🔌 Estado del Broker MQTT*\n{status}\n{detail}"
    send_message_with_menu(chat_id, text, build_main_menu())
    log_operation(chat_id, "/broker", "conectado" if mqtt_connected else "desconectado")


def handle_alarmas(chat_id: int) -> None:
    row = get_last_reading()
    if row:
        _, _, vwc, _, temp, _, _, _, _ = row
        data = {"humedad_vwc": vwc, "temperatura": temp}
        alarms = check_alarms(data)
        if alarms:
            text = "🔔 *ALARMAS ACTIVAS*\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(alarms)
        else:
            text = "✅ *Sin alarmas activas.*\nTodos los parámetros dentro de rangos normales."
    else:
        text = "⚠️ No hay datos suficientes para evaluar alarmas."
    send_message_with_menu(chat_id, text, build_main_menu())
    log_operation(chat_id, "/alarmas", "alarmas consultadas")


def handle_historial(chat_id: int) -> None:
    rows = get_last_n_readings(10)
    text = _format_historial(rows)
    send_message_with_menu(chat_id, text, build_main_menu())
    log_operation(chat_id, "/historial", f"{len(rows)} registros enviados")


def handle_ayuda(chat_id: int) -> None:
    text = (
        "*❓ AYUDA — COMANDOS DISPONIBLES*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• /start — Menú principal con botones\n"
        "• /estado — Último estado del sistema\n"
        "• /telemetria — Últimas 5 lecturas\n"
        "• /broker — Estado de conexión MQTT\n"
        "• /riego\\_on — Encender bomba (con confirmación)\n"
        "• /riego\\_off — Apagar bomba (con confirmación)\n"
        "• /alarmas — Alarmas activas\n"
        "• /historial — Últimos 10 registros\n\n"
        "_Umbrales de alarma:_\n"
        f"  💦 VWC < {ALARM_VWC_MIN}% o > {ALARM_VWC_MAX}%\n"
        f"  🌡️ Temperatura > {ALARM_TEMP_MAX}°C"
    )
    send_message_with_menu(chat_id, text, build_main_menu())
    log_operation(chat_id, "/ayuda", "ayuda enviada")


def handle_riego_on_confirm(chat_id: int) -> None:
    text = (
        "💧 *CONFIRMAR ENCENDIDO DE RIEGO*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "¿Deseas *activar la bomba* manualmente?\n"
        "Esta acción publicará `ON` en el broker MQTT."
    )
    send_message_with_menu(chat_id, text, build_confirm_menu("on"))
    log_operation(chat_id, "riego_on_confirm", "confirmación solicitada")


def handle_riego_off_confirm(chat_id: int) -> None:
    text = (
        "🛑 *CONFIRMAR APAGADO DE RIEGO*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "¿Deseas *desactivar la bomba* manualmente?\n"
        "Esta acción publicará `OFF` en el broker MQTT."
    )
    send_message_with_menu(chat_id, text, build_confirm_menu("off"))
    log_operation(chat_id, "riego_off_confirm", "confirmación solicitada")


def handle_confirm_on(chat_id: int) -> None:
    ok = publish_command("ON")
    if ok:
        text = (
            "✅ *Comando de ENCENDIDO enviado.*\n"
            "Publicado `ON` en `hogar/planta01/control`.\n"
            "Esperando confirmación del ESP32."
        )
        log_operation(chat_id, "confirm_on", "ON publicado OK")
        notify_all_authorized(
            f"💧 *RIEGO ACTIVADO MANUALMENTE*\n"
            f"Operador (Chat ID): `{chat_id}`\n"
            f"Hora: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`"
        )
    else:
        text = (
            "❌ *Error al enviar comando.*\n"
            "El broker MQTT no está disponible. Reintenta más tarde."
        )
        log_operation(chat_id, "confirm_on", "ERROR: MQTT no conectado")
    send_message_with_menu(chat_id, text, build_main_menu())


def handle_confirm_off(chat_id: int) -> None:
    ok = publish_command("OFF")
    if ok:
        text = (
            "✅ *Comando de APAGADO enviado.*\n"
            "Publicado `OFF` en `hogar/planta01/control`.\n"
            "Esperando confirmación del ESP32."
        )
        log_operation(chat_id, "confirm_off", "OFF publicado OK")
        notify_all_authorized(
            f"🛑 *RIEGO DESACTIVADO MANUALMENTE*\n"
            f"Operador (Chat ID): `{chat_id}`\n"
            f"Hora: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`"
        )
    else:
        text = (
            "❌ *Error al enviar comando.*\n"
            "El broker MQTT no está disponible. Reintenta más tarde."
        )
        log_operation(chat_id, "confirm_off", "ERROR: MQTT no conectado")
    send_message_with_menu(chat_id, text, build_main_menu())


def handle_cancel(chat_id: int) -> None:
    text = "❌ *Operación cancelada.*\nNo se realizó ningún cambio."
    send_message_with_menu(chat_id, text, build_main_menu())
    log_operation(chat_id, "cancel", "operación cancelada")


# Tabla de despacho (command/callback -> handler)
_COMMAND_DISPATCH = {
    "/start":      handle_start,
    "/estado":     handle_estado,
    "/telemetria": handle_telemetria,
    "/broker":     handle_broker,
    "/riego_on":   handle_riego_on_confirm,
    "/riego_off":  handle_riego_off_confirm,
    "/alarmas":    handle_alarmas,
    "/historial":  handle_historial,
    "/ayuda":      handle_ayuda,
    "/help":       handle_ayuda,
}

_CALLBACK_DISPATCH = {
    "estado":           handle_estado,
    "telemetria":       handle_telemetria,
    "broker":           handle_broker,
    "alarmas":          handle_alarmas,
    "historial":        handle_historial,
    "ayuda":            handle_ayuda,
    "riego_on_confirm": handle_riego_on_confirm,
    "riego_off_confirm": handle_riego_off_confirm,
    "confirm_on":       handle_confirm_on,
    "confirm_off":      handle_confirm_off,
    "cancel":           handle_cancel,
}


# ==============================================================================
# PROCESAMIENTO DE UPDATES DE TELEGRAM
# ==============================================================================

def _process_message(update: dict) -> None:
    """Procesa un update de tipo message."""
    message = update.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()

    if not chat_id or not text:
        return

    if not is_authorized(chat_id):
        reject_unauthorized(chat_id)
        return

    # Extrae el comando base (sin @botname ni argumentos)
    command = text.split("@")[0].split(" ")[0].lower()
    handler = _COMMAND_DISPATCH.get(command)
    if handler:
        try:
            handler(chat_id)
        except Exception as exc:
            log.error(f"Error en handler de '{command}': {exc}")
            send_message(chat_id, f"❌ Error interno: {exc}")
    else:
        send_message(
            chat_id,
            f"❓ Comando no reconocido: `{command}`\n"
            "Usa /ayuda para ver los comandos disponibles."
        )


def _process_callback_query(update: dict) -> None:
    """Procesa un update de tipo callback_query (botón inline)."""
    cq = update.get("callback_query", {})
    callback_query_id = cq.get("id", "")
    chat_id = cq.get("message", {}).get("chat", {}).get("id")
    data = cq.get("data", "").strip()

    if not chat_id:
        return

    # Responder al callback para quitar el indicador de carga
    answer_callback_query(callback_query_id, "⏳")

    if not is_authorized(chat_id):
        reject_unauthorized(chat_id)
        return

    handler = _CALLBACK_DISPATCH.get(data)
    if handler:
        try:
            handler(chat_id)
        except Exception as exc:
            log.error(f"Error en callback handler '{data}': {exc}")
            send_message(chat_id, f"❌ Error interno: {exc}")
    else:
        send_message(
            chat_id,
            f"❓ Acción no reconocida: `{data}`"
        )


def _process_update(update: dict) -> None:
    """Redirige el update al procesador correspondiente."""
    if "message" in update:
        _process_message(update)
    elif "callback_query" in update:
        _process_callback_query(update)


# ==============================================================================
# HILO DE POLLING DE TELEGRAM
# ==============================================================================

def hilo_polling_telegram() -> None:
    """
    Hilo daemon que hace long-polling contra la API de Telegram.
    Compatible con el patrón usado en listener_iot_riego.py.
    """
    if not TELEGRAM_BOT_TOKEN:
        log.error("[POLLING] TELEGRAM_BOT_TOKEN no configurado. Hilo de polling detenido.")
        return

    offset = 0
    url_updates = f"{_TG_BASE}/getUpdates"
    log.info("[POLLING] Hilo de polling Telegram iniciado.")

    while True:
        try:
            params = {"offset": offset, "timeout": 10}
            res = requests.get(url_updates, params=params, timeout=15)

            if res.status_code == 200:
                data = res.json()
                for update in data.get("result", []):
                    update_id = update["update_id"]
                    offset = update_id + 1
                    try:
                        _process_update(update)
                    except Exception as exc:
                        log.error(f"[POLLING] Error procesando update {update_id}: {exc}")
            elif res.status_code == 409:
                log.warning(
                    "[POLLING] HTTP 409 Conflict: Otra instancia del bot está haciendo getUpdates. "
                    "Asegúrate de no tener dos instancias activas. Reintentando en 5 segundos..."
                )
                time.sleep(5)
            else:
                log.warning(f"[POLLING] HTTP {res.status_code}: {res.text[:200]}")
                time.sleep(2)

        except requests.exceptions.Timeout:
            # Long-polling timeout normal, continuar
            pass
        except Exception as exc:
            log.error(f"[POLLING] Error inesperado: {exc}")
            time.sleep(5)


# ==============================================================================
# PUNTO DE ENTRADA PRINCIPAL
# ==============================================================================

if __name__ == "__main__":

    # --- Validación de configuración ---
    if not TELEGRAM_BOT_TOKEN:
        log.error(
            "TELEGRAM_BOT_TOKEN no está configurado.\n"
            "Exporta la variable de entorno antes de ejecutar:\n"
            "  export TELEGRAM_BOT_TOKEN='tu_token_aqui'\n"
            "  export AUTHORIZED_CHAT_IDS='chat_id1,chat_id2'"
        )
        raise SystemExit(1)

    if not AUTHORIZED_CHAT_IDS:
        log.warning(
            "AUTHORIZED_CHAT_IDS está vacío. "
            "Ningún usuario podrá interactuar con el bot.\n"
            "Exporta la variable: export AUTHORIZED_CHAT_IDS='tu_chat_id'"
        )

    log.info(f"IDs autorizados: {AUTHORIZED_CHAT_IDS}")
    log.info(f"MQTT broker: {MQTT_BROKER}:{MQTT_PORT}")
    log.info(f"BD: {DB_PATH}")

    # --- Inicializar base de datos ---
    init_db()

    # --- Inicializar y conectar cliente MQTT ---
    client = _init_mqtt_client()
    log.info(f"Conectando al broker MQTT {MQTT_BROKER}:{MQTT_PORT}...")
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    except Exception as exc:
        log.error(f"No se pudo conectar al broker MQTT: {exc}")
        # El bot continúa sin MQTT para al menos responder comandos de estado

    # --- Hilo MQTT loop ---
    t_mqtt = threading.Thread(target=client.loop_forever, daemon=True, name="mqtt-loop")
    t_mqtt.start()
    log.info("Hilo MQTT iniciado.")

    # --- Hilo de polling Telegram ---
    t_telegram = threading.Thread(
        target=hilo_polling_telegram,
        daemon=True,
        name="telegram-polling"
    )
    t_telegram.start()
    log.info("Hilo de polling Telegram iniciado.")

    # --- Mensaje de inicio a todos los autorizados ---
    # Esperar brevemente a que MQTT conecte
    time.sleep(2)
    broker_status = "🟢 CONECTADO" if mqtt_connected else "🔴 NO CONECTADO"
    start_msg = (
        "🌱 *Bot IoT de Riego — En Línea*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Inicio: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
        f"🔌 MQTT: {broker_status}\n"
        f"💾 BD: `{DB_NAME}`\n\n"
        "Usa /start para acceder al menú principal."
    )
    notify_all_authorized(start_msg)

    # --- Mantener hilo principal vivo ---
    log.info("Sistema iniciado. Ctrl+C para finalizar.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Interrupción del usuario. Finalizando…")
        notify_all_authorized("⛔ *Bot IoT detenido* por el operador.")
