
import os
import sqlite3
import json
import requests
import threading
import time
import logging
from datetime import datetime
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

try:
    from webhook_alerts import webhook_manager, WebhookEvent, init_webhook_tables
except Exception as _e_wh:
    webhook_manager = None
    WebhookEvent = None
    init_webhook_tables = None

load_dotenv()

# ============================================================================
# LOGGING MEJORADO
# ============================================================================

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('logs/backend.log')
    ]
)
logger = logging.getLogger(__name__)


# ============================================================================
# PARÁMETROS DE CONFIGURACIÓN (CARGADOS DESDE VARIABLES DE ENTORNO)
# ============================================================================

MQTT_BROKER = os.environ.get("MQTT_BROKER", "broker.emqx.io")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TOPIC_DATA = os.environ.get("MQTT_TOPIC_DATA", "hogar/planta01/telemetria")
MQTT_TOPIC_CTRL = os.environ.get("MQTT_TOPIC_CTRL", "hogar/planta01/control")
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
AUTHORIZED_CHAT_IDS_STR = os.environ.get("AUTHORIZED_CHAT_IDS", TELEGRAM_CHAT_ID)
AUTHORIZED_CHAT_IDS = [cid.strip() for cid in AUTHORIZED_CHAT_IDS_STR.split(",") if cid.strip()]
# Bandera para evitar conflicto HTTP 409 cuando se ejecuta telegram_bot.py en paralelo
ENABLE_TELEGRAM_POLLING = os.environ.get("ENABLE_TELEGRAM_POLLING", "false").lower() in ("true", "1", "yes")

DB_NAME = os.environ.get("DB_NAME", "riego_iot.db")


# ============================================================================
# ARQUITECTURA DE PERSISTENCIA Y ESTRUCTURA DE LA BASE DE DATOS SQLITE
# ============================================================================

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS telemetria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_hora TEXT,
            dispositivo TEXT,
            humedad_vwc REAL,
            umbral_trigger REAL,
            temperatura REAL,
            humedad_aire REAL,
            vpd_kpa REAL,
            bomba_estado TEXT,
            alerta_timeout INTEGER
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS operaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_hora TEXT,
            tipo TEXT,
            descripcion TEXT,
            usuario TEXT,
            resultado TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alarmas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_hora TEXT,
            tipo_alarma TEXT,
            valor REAL,
            umbral REAL,
            dispositivo TEXT,
            resuelta INTEGER DEFAULT 0
        )
    ''')

    conn.commit()
    conn.close()

    if init_webhook_tables:
        init_webhook_tables(DB_NAME)

    logger.info("Base de datos SQLite inicializada correctamente.")


def guardar_telemetria(data):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute('''
            INSERT INTO telemetria (
                fecha_hora, dispositivo, humedad_vwc, umbral_trigger,
                temperatura, humedad_aire, vpd_kpa, bomba_estado,
                alerta_timeout
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            now_str,
            data.get("dispositivo", "ESP32_Planta_Hogar"),
            float(data.get("humedad_vwc", 0.0)),
            float(data.get("umbral_trigger", 27.0)),
            float(data.get("temperatura", 0.0)),
            float(data.get("humedad_aire", 0.0)),
            float(data.get("vpd_kpa", 0.0)),
            str(data.get("bomba_estado", "OFF")),
            1 if data.get("alerta_timeout", False) else 0
        ))

        conn.commit()
        conn.close()

        logger.info(
            f"Registro almacenado en BD | "
            f"Humedad Volumétrica: {data.get('humedad_vwc')}% | "
            f"Actuador: {data.get('bomba_estado')}"
        )

    except Exception as e:
        logger.error(f"Excepción en la persistencia de datos SQLite: {e}")


def obtener_ultimo_estado_db():
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT fecha_hora, humedad_vwc, umbral_trigger,
                   temperatura, humedad_aire, vpd_kpa,
                   bomba_estado, alerta_timeout
            FROM telemetria
            ORDER BY id DESC LIMIT 1
        ''')

        row = cursor.fetchone()
        conn.close()

        return row

    except Exception as e:
        logger.error(f"Error al consultar último registro: {e}")
        return None


# ============================================================================
# REGISTRO DE OPERACIONES
# ============================================================================

def registrar_operacion(tipo, descripcion, usuario, resultado):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute('''
            INSERT INTO operaciones (fecha_hora, tipo, descripcion, usuario, resultado)
            VALUES (?, ?, ?, ?, ?)
        ''', (now_str, tipo, descripcion, usuario, resultado))
        conn.commit()
        conn.close()
        logger.info(f"[OPERACION] {tipo} | {descripcion} | usuario={usuario} | resultado={resultado}")
    except Exception as e:
        logger.error(f"Error al registrar operación: {e}")


# ============================================================================
# VERIFICACIÓN DE ALARMAS
# ============================================================================

def verificar_alarmas(data):
    dispositivo = data.get("dispositivo", "ESP32_Planta_Hogar")
    humedad_vwc = float(data.get("humedad_vwc", 0.0))
    temperatura = float(data.get("temperatura", 0.0))
    vpd_kpa = float(data.get("vpd_kpa", 0.0))
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    alarmas_detectadas = []

    if humedad_vwc < 15.0:
        alarmas_detectadas.append(("HUMEDAD_CRITICA_BAJA", humedad_vwc, 15.0))
    if humedad_vwc > 85.0:
        alarmas_detectadas.append(("HUMEDAD_EXCESO", humedad_vwc, 85.0))
    if temperatura > 38.0:
        alarmas_detectadas.append(("TEMPERATURA_ALTA", temperatura, 38.0))
    if vpd_kpa > 2.5:
        alarmas_detectadas.append(("VPD_CRITICO", vpd_kpa, 2.5))

    if not alarmas_detectadas:
        return

    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        for tipo_alarma, valor, umbral in alarmas_detectadas:
            cursor.execute('''
                INSERT INTO alarmas (fecha_hora, tipo_alarma, valor, umbral, dispositivo, resuelta)
                VALUES (?, ?, ?, ?, ?, 0)
            ''', (now_str, tipo_alarma, valor, umbral, dispositivo))
            logger.warning(f"[ALARMA] {tipo_alarma} | valor={valor} | umbral={umbral} | dispositivo={dispositivo}")

            msg_alarma = (
                f"⚠️ *ALARMA: {tipo_alarma}*\n"
                f"• Valor actual: {valor}\n"
                f"• Umbral: {umbral}\n"
                f"• Dispositivo: {dispositivo}\n"
                f"• Hora: {now_str}"
            )
            enviar_notificacion(msg_alarma)

            if webhook_manager and WebhookEvent:
                webhook_manager.emitir_evento(
                    evento=WebhookEvent.ALERTA_UMBRAL,
                    datos={"tipo": tipo_alarma, "valor": valor, "umbral": umbral},
                    dispositivo=dispositivo
                )

        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error al guardar alarma en BD: {e}")


# ============================================================================
# NOTIFICACIONES TELEGRAM
# ============================================================================

def enviar_notificacion(mensaje, chat_id=None, reply_markup=None):
    try:
        target_chat_id = chat_id if chat_id else TELEGRAM_CHAT_ID
        domain = "api.telegram.org"
        url = f"https://{domain}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

        payload = {
            "chat_id": target_chat_id,
            "text": mensaje,
            "parse_mode": "Markdown"
        }

        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)

        res = requests.post(url, json=payload, timeout=5)

        if res.status_code != 200:
            logger.warning(f"Fallo en pasarela HTTP Telegram. Código: {res.status_code}")

    except Exception as e:
        logger.error(f"Excepción al ejecutar petición HTTP Telegram: {e}")


def enviar_menu_principal(chat_id):
    """Envía el menú principal con teclado inline al chat_id especificado."""
    menu_msg = (
        "*MENÚ DE COMANDOS TELEMANDO IOT*\n\n"
        "• `/riego_on` : Activar la bomba manualmente vía MQTT\n"
        "• `/riego_off` : Desactivar la bomba manualmente vía MQTT\n"
        "• `/estado` : Consultar última lectura de telemetría\n"
        "• `/ayuda` : Mostrar menú de comandos"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "📊 Estado", "callback_data": "/estado"},
                {"text": "💧 Riego ON", "callback_data": "/riego_on"},
                {"text": "🛑 Riego OFF", "callback_data": "/riego_off"}
            ]
        ]
    }

    enviar_notificacion(menu_msg, chat_id=chat_id, reply_markup=reply_markup)


# ============================================================================
# VARIABLES GLOBALES DE CONTROL DE ESTADOS
# ============================================================================

ultimo_estado_bomba = "OFF"
ultima_alerta_timeout = False


# ============================================================================
# CALLBACK DE CONEXIÓN MQTT
# ============================================================================

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info(f"Conexión establecida con broker MQTT: {MQTT_BROKER}")
        client.subscribe(MQTT_TOPIC_DATA)
        logger.info(f"Suscripción activa: {MQTT_TOPIC_DATA}")
    else:
        logger.error(f"Error de conexión con broker MQTT. Código: {rc}")


# ============================================================================
# CALLBACK DE DESCONEXIÓN MQTT (RECONEXIÓN AUTOMÁTICA)
# ============================================================================

def on_disconnect(client, userdata, rc):
    if rc != 0:
        logger.warning(f"Desconexión inesperada del broker MQTT (rc={rc}). Intentando reconexión...")
        if webhook_manager and WebhookEvent:
            webhook_manager.emitir_evento(
                evento=WebhookEvent.BROKER_ERROR,
                datos={"codigo_rc": rc, "error": f"Desconexión inesperada rc={rc}"},
                dispositivo="Backend_Listener"
            )
        time.sleep(5)
        try:
            client.reconnect()
        except Exception as e:
            logger.error(f"Error en reconexión: {e}")


# ============================================================================
# CALLBACK DE RECEPCIÓN DE TELEMETRÍA MQTT
# ============================================================================

def on_message(client, userdata, msg):
    global ultimo_estado_bomba, ultima_alerta_timeout

    try:
        payload_str = msg.payload.decode('utf-8')
        data = json.loads(payload_str)

        guardar_telemetria(data)

        bomba_actual = str(data.get("bomba_estado", "OFF"))
        timeout_actual = bool(data.get("alerta_timeout", False))
        vwc = data.get("humedad_vwc", 0)

        # Notificación de inicio automático de riego
        if bomba_actual == "ON" and ultimo_estado_bomba == "OFF":

            msg_notif = (
                "*INICIO DE RIEGO AUTOMÁTICO*\n"
                f"Suelo seco detectado ({vwc}% VWC).\n"
                "Actuador energizado desde el microcontrolador."
            )

            enviar_notificacion(msg_notif)
            registrar_operacion(
                "BOMBA_ON_AUTO",
                f"Inicio automático de riego. VWC={vwc}%",
                "ESP32",
                "OK"
            )
            if webhook_manager and WebhookEvent:
                webhook_manager.emitir_evento(
                    evento=WebhookEvent.ACTUADOR_CAMBIO,
                    datos={"bomba_estado": "ON", "motivo": "inicio_automatico", "humedad_vwc": vwc},
                    dispositivo=data.get("dispositivo", "ESP32_Planta_Hogar")
                )

        # Notificación de finalización normal del riego
        elif (
            bomba_actual == "OFF"
            and ultimo_estado_bomba == "ON"
            and not timeout_actual
        ):

            msg_notif = (
                "*FINALIZACIÓN DE RIEGO*\n"
                f"Capacidad de campo restablecida ({vwc}% VWC).\n"
                "Actuador desenergizado."
            )

            enviar_notificacion(msg_notif)
            registrar_operacion(
                "BOMBA_OFF_AUTO",
                f"Finalización automática de riego. VWC={vwc}%",
                "ESP32",
                "OK"
            )
            if webhook_manager and WebhookEvent:
                webhook_manager.emitir_evento(
                    evento=WebhookEvent.ACTUADOR_CAMBIO,
                    datos={"bomba_estado": "OFF", "motivo": "finalizacion_normal", "humedad_vwc": vwc},
                    dispositivo=data.get("dispositivo", "ESP32_Planta_Hogar")
                )

        # Notificación de timeout
        if timeout_actual and not ultima_alerta_timeout:

            msg_notif = (
                "*ALERTA CRÍTICA DE SEGURIDAD*\n"
                "Actuador desactivado automáticamente tras "
                "alcanzar el tiempo máximo de operación "
                "(Timeout de 10 segundos)."
            )

            enviar_notificacion(msg_notif)
            registrar_operacion(
                "TIMEOUT_BOMBA",
                "Actuador desactivado por timeout de seguridad",
                "ESP32",
                "ALERTA"
            )
            if webhook_manager and WebhookEvent:
                webhook_manager.emitir_evento(
                    evento=WebhookEvent.TIMEOUT_SEGURIDAD,
                    datos={"mensaje": "Timeout de 10 segundos alcanzado", "bomba_estado": "OFF"},
                    dispositivo=data.get("dispositivo", "ESP32_Planta_Hogar")
                )

        # Verificar alarmas de umbrales
        verificar_alarmas(data)

        # Actualización de estados globales
        ultimo_estado_bomba = bomba_actual
        ultima_alerta_timeout = timeout_actual

    except Exception as e:
        logger.error(f"Excepción en procesamiento de telemetría JSON: {e}")


# ============================================================================
# ESCUCHA Y PROCESAMIENTO DE COMANDOS TELEGRAM
# ============================================================================

def procesar_comando_telegram(comando, mqtt_client, sender_chat_id=None):

    comando_clean = comando.strip().lower()
    chat_id_destino = sender_chat_id if sender_chat_id else TELEGRAM_CHAT_ID

    # ------------------------------------------------------------------------
    # ACTIVACIÓN MANUAL DEL SISTEMA DE RIEGO
    # ------------------------------------------------------------------------

    if comando_clean == "/riego_on":

        logger.info("Comando manual recibido desde Telegram: /riego_on")

        mqtt_client.publish(MQTT_TOPIC_CTRL, "ON")

        enviar_notificacion(
            "*SOLICITUD DE ACTIVACIÓN DEL SISTEMA DE RIEGO*\n"
            "Se ha transmitido la instrucción de encendido "
            "del actuador mediante el protocolo MQTT.\n"
            "Pendiente de confirmación del microcontrolador ESP32.",
            chat_id=chat_id_destino
        )

        registrar_operacion(
            "RIEGO_ON_MANUAL",
            "Activación manual de bomba vía Telegram",
            chat_id_destino,
            "ENVIADO_MQTT"
        )

    # ------------------------------------------------------------------------
    # DESACTIVACIÓN MANUAL DEL SISTEMA DE RIEGO
    # ------------------------------------------------------------------------

    elif comando_clean == "/riego_off":

        logger.info("Comando manual recibido desde Telegram: /riego_off")

        mqtt_client.publish(MQTT_TOPIC_CTRL, "OFF")

        enviar_notificacion(
            "*SOLICITUD DE DESACTIVACIÓN DEL SISTEMA DE RIEGO*\n"
            "Se ha transmitido la instrucción de apagado "
            "del actuador mediante el protocolo MQTT.\n"
            "Pendiente de confirmación del microcontrolador ESP32.",
            chat_id=chat_id_destino
        )

        registrar_operacion(
            "RIEGO_OFF_MANUAL",
            "Desactivación manual de bomba vía Telegram",
            chat_id_destino,
            "ENVIADO_MQTT"
        )

    # ------------------------------------------------------------------------
    # CONSULTA DEL ESTADO DEL SISTEMA
    # ------------------------------------------------------------------------

    elif comando_clean == "/estado":

        reg = obtener_ultimo_estado_db()

        if reg:

            fecha, vwc, trig, temp, hum_a, vpd, bomba, timeout = reg

            estado_msg = (
                "*ESTADO ACTUAL DEL SISTEMA IOT*\n"
                f"• Fecha y Hora: {fecha}\n"
                f"• Humedad Volumétrica (VWC): {vwc}%\n"
                f"• Umbral MAD Trigger: {trig}%\n"
                f"• Temperatura Aire: {temp} °C\n"
                f"• Humedad Relativa: {hum_a}%\n"
                f"• Déficit Presión Vapor (VPD): {vpd} kPa\n"
                f"• Estado Actuador (Bomba): *{bomba}*\n"
                f"• Alerta Timeout: {'SÍ' if timeout else 'NO'}"
            )

        else:
            estado_msg = "No hay registros disponibles en la base de datos."

        enviar_notificacion(estado_msg, chat_id=chat_id_destino)

    # ------------------------------------------------------------------------
    # MENÚ DE AYUDA CON TECLADO INLINE
    # ------------------------------------------------------------------------

    elif comando_clean in ["/start", "/ayuda", "/help"]:

        enviar_menu_principal(chat_id_destino)


# ============================================================================
# HILO DE RECEPCIÓN DE COMANDOS TELEGRAM (POLLING)
# ============================================================================

def hilo_polling_telegram(mqtt_client):

    offset = 0

    domain = "api.telegram.org"
    url_updates = f"https://{domain}/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

    while True:

        try:

            params = {
                "offset": offset,
                "timeout": 10
            }

            res = requests.get(
                url_updates,
                params=params,
                timeout=12
            )

            if res.status_code == 200:

                data = res.json()

                for update in data.get("result", []):

                    offset = update["update_id"] + 1

                    message = update.get("message", {})
                    text = message.get("text", "")

                    # Soporte para callback_query (botones inline)
                    if not message and "callback_query" in update:
                        callback = update["callback_query"]
                        text = callback.get("data", "")
                        message = callback.get("message", {})

                    sender_chat_id = str(
                        message.get("chat", {}).get("id", "")
                    )

                    if sender_chat_id in AUTHORIZED_CHAT_IDS:

                        procesar_comando_telegram(
                            text,
                            mqtt_client,
                            sender_chat_id=sender_chat_id
                        )

                    else:
                        # Log de acceso no autorizado
                        if sender_chat_id:
                            logger.warning(f"[SEGURIDAD] Acceso rechazado para chat_id: {sender_chat_id}")

            elif res.status_code == 409:
                logger.warning(
                    "[TELEGRAM] Conflicto HTTP 409 detectado: Otra instancia del bot (p. ej. telegram_bot.py) "
                    "está consultando getUpdates. Se desactiva la concurrencia en este hilo."
                )
                time.sleep(10)

        except Exception as e:

            logger.error(f"Error en polling de Telegram: {e}")
            time.sleep(2)


# ============================================================================
# BLOQUE PRINCIPAL DE EJECUCIÓN (MAIN)
# ============================================================================

if __name__ == "__main__":

    init_db()

    client_id = f"Backend_Listener_{int(time.time())}"
    client = mqtt.Client(client_id=client_id)

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    if MQTT_USER and MQTT_PASS:
        client.username_pw_set(MQTT_USER, MQTT_PASS)

    logger.info("Iniciando conexión con el broker MQTT...")

    client.connect(
        MQTT_BROKER,
        MQTT_PORT,
        60
    )

    if ENABLE_TELEGRAM_POLLING:
        t_telegram = threading.Thread(
            target=hilo_polling_telegram,
            args=(client,),
            daemon=True
        )
        t_telegram.start()
        logger.info("Hilo secundario de telemando Telegram iniciado.")
    else:
        logger.info(
            "Polling interactivo de Telegram desactivado en el listener. "
            "(Gestionado de forma dedicada por telegram_bot.py para evitar conflicto HTTP 409). "
            "Las notificaciones y alertas automáticas continúan activas."
        )

    logger.info(
        "Backend en escucha activa de telemetría "
        "e instrucciones IoT. (Ctrl + C para finalizar)"
    )

    client.loop_forever()
