
import sqlite3
import json
import requests
import threading
import time
from datetime import datetime
import paho.mqtt.client as mqtt

# ============================================================================
# PARÁMETROS DE CONFIGURACIÓN DE PROTOCOLO MQTT, BASE DE DATOS Y NOTIFICACIONES
# ============================================================================

MQTT_BROKER = "broker.emqx.io"
MQTT_PORT = 1883
MQTT_TOPIC_DATA = "hogar/planta01/telemetria"
MQTT_TOPIC_CTRL = "hogar/planta01/control"

# Credenciales de autenticación para Telegram
# Configurar aquí el token nuevo generado mediante BotFather
TELEGRAM_BOT_TOKEN = "9159520153:AAHv5qeorAPrXTUOO7iJyz9ORVnGxlz3_Ko"
TELEGRAM_CHAT_ID = "9013582355"

# Base de datos SQLite
DB_NAME = "riego_iot.db"


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

    conn.commit()
    conn.close()

    print("Base de datos SQLite inicializada correctamente.")


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

        print(
            f"[{now_str}] Registro almacenado en BD | "
            f"Humedad Volumétrica: {data.get('humedad_vwc')}% | "
            f"Actuador: {data.get('bomba_estado')}"
        )

    except Exception as e:
        print(f"Excepción en la persistencia de datos SQLite: {e}")


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
        print(f"Error al consultar último registro: {e}")
        return None


def enviar_notificacion(mensaje):
    try:
        domain = "api.telegram.org"
        url = f"https://{domain}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": mensaje,
            "parse_mode": "Markdown"
        }

        res = requests.post(url, json=payload, timeout=5)

        if res.status_code != 200:
            print(f"Fallo en pasarela HTTP Telegram. Código: {res.status_code}")

    except Exception as e:
        print(f"Excepción al ejecutar petición HTTP Telegram: {e}")


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
        print(f"Conexión establecida con broker MQTT: {MQTT_BROKER}")

        client.subscribe(MQTT_TOPIC_DATA)

        print(f"Suscripción activa: {MQTT_TOPIC_DATA}")


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

        # Notificación de timeout
        if timeout_actual and not ultima_alerta_timeout:

            msg_notif = (
                "*ALERTA CRÍTICA DE SEGURIDAD*\n"
                "Actuador desactivado automáticamente tras "
                "alcanzar el tiempo máximo de operación "
                "(Timeout de 10 segundos)."
            )

            enviar_notificacion(msg_notif)

        # Actualización de estados globales
        ultimo_estado_bomba = bomba_actual
        ultima_alerta_timeout = timeout_actual

    except Exception as e:
        print(f"Excepción en procesamiento de telemetría JSON: {e}")


# ============================================================================
# ESCUCHA Y PROCESAMIENTO DE COMANDOS TELEGRAM
# ============================================================================

def procesar_comando_telegram(comando, mqtt_client):

    comando_clean = comando.strip().lower()

    # ------------------------------------------------------------------------
    # ACTIVACIÓN MANUAL DEL SISTEMA DE RIEGO
    # ------------------------------------------------------------------------

    if comando_clean == "/riego_on":

        print("Comando manual recibido desde Telegram: /riego_on")

        mqtt_client.publish(MQTT_TOPIC_CTRL, "ON")

        enviar_notificacion(
            "*SOLICITUD DE ACTIVACIÓN DEL SISTEMA DE RIEGO*\n"
            "Se ha transmitido la instrucción de encendido "
            "del actuador mediante el protocolo MQTT.\n"
            "Pendiente de confirmación del microcontrolador ESP32."
        )

    # ------------------------------------------------------------------------
    # DESACTIVACIÓN MANUAL DEL SISTEMA DE RIEGO
    # ------------------------------------------------------------------------

    elif comando_clean == "/riego_off":

        print("Comando manual recibido desde Telegram: /riego_off")

        mqtt_client.publish(MQTT_TOPIC_CTRL, "OFF")

        enviar_notificacion(
            "*SOLICITUD DE DESACTIVACIÓN DEL SISTEMA DE RIEGO*\n"
            "Se ha transmitido la instrucción de apagado "
            "del actuador mediante el protocolo MQTT.\n"
            "Pendiente de confirmación del microcontrolador ESP32."
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

        enviar_notificacion(estado_msg)

    # ------------------------------------------------------------------------
    # MENÚ DE AYUDA
    # ------------------------------------------------------------------------

    elif comando_clean in ["/start", "/ayuda", "/help"]:

        menu_msg = (
            "*MENÚ DE COMANDOS TELEMANDO IOT*\n\n"
            "• `/riego_on` : Activar la bomba manualmente vía MQTT\n"
            "• `/riego_off` : Desactivar la bomba manualmente vía MQTT\n"
            "• `/estado` : Consultar última lectura de telemetría\n"
            "• `/ayuda` : Mostrar menú de comandos"
        )

        enviar_notificacion(menu_msg)


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

                    sender_chat_id = str(
                        message.get("chat", {}).get("id", "")
                    )

                    if sender_chat_id == TELEGRAM_CHAT_ID:

                        procesar_comando_telegram(
                            text,
                            mqtt_client
                        )

        except Exception as e:

            print(f"Error en polling de Telegram: {e}")
            time.sleep(2)


# ============================================================================
# BLOQUE PRINCIPAL DE EJECUCIÓN (MAIN)
# ============================================================================

if __name__ == "__main__":

    init_db()

    client = mqtt.Client(
        client_id="VSCode_Backend_Listener"
    )

    client.on_connect = on_connect
    client.on_message = on_message

    print("Iniciando conexión con el broker MQTT...")

    client.connect(
        MQTT_BROKER,
        MQTT_PORT,
        60
    )

    t_telegram = threading.Thread(
        target=hilo_polling_telegram,
        args=(client,),
        daemon=True
    )

    t_telegram.start()

    print("Hilo secundario de telemando Telegram iniciado.")

    print(
        "Backend en escucha activa de telemetría "
        "e instrucciones IoT. (Ctrl + C para finalizar)"
    )

    client.loop_forever()
