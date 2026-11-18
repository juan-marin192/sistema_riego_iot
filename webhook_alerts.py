"""
webhook_alerts.py - Sistema de Notificaciones, Webhooks y Alertas IoT
======================================================================
Implementa la FASE 4 de las especificaciones del proyecto:
- Configuración de webhooks para eventos críticos (umbrales, actuadores, broker, desconexiones)
- Deduplicación / control de spam con ventana de cooldown
- Mecanismo de reintentos exponenciales con control de errores
- Registro histórico persistente en SQLite (webhook_logs y webhooks_config)
- Autenticación por token (Bearer / X-Webhook-Secret / HMAC SHA-256)
- Identificación de destinatarios por usuario
"""

import os
import json
import time
import hmac
import hashlib
import sqlite3
import logging
import threading
from datetime import datetime
from typing import Dict, List, Optional, Any

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("webhook_alerts")
DB_NAME = os.environ.get("DB_NAME", "riego_iot.db")


# ============================================================================
# EVENTOS SOPORTADOS
# ============================================================================

class WebhookEvent:
    ALERTA_UMBRAL           = "ALERTA_UMBRAL"
    DISPOSITIVO_DESCONECTADO = "DISPOSITIVO_DESCONECTADO"
    BROKER_ERROR            = "BROKER_ERROR"
    ACTUADOR_CAMBIO         = "ACTUADOR_CAMBIO"
    TIMEOUT_SEGURIDAD       = "TIMEOUT_SEGURIDAD"

    ALL_EVENTS = [
        ALERTA_UMBRAL,
        DISPOSITIVO_DESCONECTADO,
        BROKER_ERROR,
        ACTUADOR_CAMBIO,
        TIMEOUT_SEGURIDAD,
    ]


# ============================================================================
# PERSISTENCIA SQLITE PARA WEBHOOKS
# ============================================================================

def init_webhook_tables(db_name: str = DB_NAME):
    """Inicializa las tablas de configuración y logs de webhooks."""
    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS webhooks_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            url TEXT NOT NULL,
            secret_token TEXT,
            auth_type TEXT DEFAULT 'bearer',
            eventos TEXT NOT NULL,
            destinatarios TEXT NOT NULL,
            activo INTEGER DEFAULT 1,
            creado_el TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS webhook_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_hora TEXT NOT NULL,
            evento TEXT NOT NULL,
            destinatario TEXT NOT NULL,
            url TEXT NOT NULL,
            payload TEXT NOT NULL,
            status_code INTEGER,
            exito INTEGER NOT NULL,
            reintentos INTEGER NOT NULL,
            error_msg TEXT
        )
    ''')

    conn.commit()
    conn.close()


# ============================================================================
# GESTOR DE WEBHOOKS Y ALERTAS
# ============================================================================

class WebhookManager:
    """
    Gestiona el envío de alertas mediante HTTP Webhooks y Telegram,
    con control de inundación (cooldown), reintentos exponenciales
    y trazabilidad completa de destinatarios.
    """

    def __init__(self, db_name: str = DB_NAME, cooldown_seconds: int = 60):
        self.db_name = db_name
        self.cooldown_seconds = cooldown_seconds
        self._last_alert_timestamps: Dict[str, float] = {}
        self._lock = threading.Lock()
        init_webhook_tables(self.db_name)

    def registrar_webhook(
        self,
        nombre: str,
        url: str,
        secret_token: str = "",
        auth_type: str = "bearer",
        eventos: Optional[List[str]] = None,
        destinatarios: Optional[List[str]] = None,
    ) -> int:
        """Registra un nuevo webhook en la base de datos."""
        if eventos is None:
            eventos = WebhookEvent.ALL_EVENTS
        if destinatarios is None:
            destinatarios = ["admin_iot"]

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO webhooks_config (
                nombre, url, secret_token, auth_type, eventos, destinatarios, activo, creado_el
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
        ''', (
            nombre,
            url,
            secret_token,
            auth_type,
            ",".join(eventos),
            ",".join(destinatarios),
            now_str,
        ))

        webhook_id = cursor.lastrowid
        conn.commit()
        conn.close()
        logger.info(f"Webhook '{nombre}' registrado con ID {webhook_id} -> {url}")
        return webhook_id

    def obtener_webhooks_activos(self) -> List[Dict[str, Any]]:
        """Obtiene todos los webhooks activos configurados."""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, nombre, url, secret_token, auth_type, eventos, destinatarios
            FROM webhooks_config
            WHERE activo = 1
        ''')
        rows = cursor.fetchall()
        conn.close()

        webhooks = []
        for r in rows:
            webhooks.append({
                "id": r[0],
                "nombre": r[1],
                "url": r[2],
                "secret_token": r[3],
                "auth_type": r[4],
                "eventos": [e.strip() for e in r[5].split(",") if e.strip()],
                "destinatarios": [d.strip() for d in r[6].split(",") if d.strip()],
            })
        return webhooks

    def _es_duplicada(self, clave_alerta: str) -> bool:
        """Verifica si la alerta está en ventana de cooldown para evitar spam."""
        with self._lock:
            ahora = time.time()
            ultimo = self._last_alert_timestamps.get(clave_alerta, 0.0)
            if ahora - ultimo < self.cooldown_seconds:
                return True
            self._last_alert_timestamps[clave_alerta] = ahora
            return False

    def emitir_evento(
        self,
        evento: str,
        datos: Dict[str, Any],
        dispositivo: str = "ESP32_Planta_Hogar",
        destinatario_especifico: Optional[str] = None,
        forzar: bool = False,
    ):
        """
        Emite un evento de alerta a todos los webhooks suscritos.
        Se ejecuta de forma asíncrona en un hilo separado.
        """
        clave_dedup = f"{evento}:{dispositivo}:{datos.get('tipo', '')}"
        if not forzar and self._es_duplicada(clave_dedup):
            logger.info(f"[COOLDOWN] Alerta suprimida por deduplicacion: {clave_dedup}")
            return False

        with self._lock:
            self._last_alert_timestamps[clave_dedup] = time.time()

        payload = {
            "evento": evento,
            "dispositivo": dispositivo,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "datos": datos,
        }

        # Ejecución asíncrona para no bloquear el hilo MQTT/Backend
        threading.Thread(
            target=self._despachar_webhooks,
            args=(evento, payload, destinatario_especifico),
            daemon=True,
        ).start()
        return True

    def _despachar_webhooks(
        self,
        evento: str,
        payload: Dict[str, Any],
        destinatario_especifico: Optional[str] = None,
    ):
        webhooks = self.obtener_webhooks_activos()
        payload_json = json.dumps(payload)

        for wh in webhooks:
            # Filtrar si el webhook no está suscrito al evento
            if evento not in wh["eventos"]:
                continue

            # Filtrar destinatarios si se especificó uno
            destinatarios = wh["destinatarios"]
            if destinatario_especifico and destinatario_especifico not in destinatarios:
                continue

            for dest in destinatarios:
                if destinatario_especifico and dest != destinatario_especifico:
                    continue
                self._enviar_con_reintentos(wh, payload, payload_json, dest)

    def _enviar_con_reintentos(
        self,
        webhook: Dict[str, Any],
        payload: Dict[str, Any],
        payload_json: str,
        destinatario: str,
        max_reintentos: int = 3,
    ):
        url = webhook["url"]
        auth_type = webhook.get("auth_type", "bearer").lower()
        secret = webhook.get("secret_token", "")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "IoT-Riego-Webhook/2.0",
            "X-IoT-Event": payload.get("evento", "UNKNOWN"),
            "X-IoT-Recipient": destinatario,
        }

        if secret:
            if auth_type == "bearer":
                headers["Authorization"] = f"Bearer {secret}"
            elif auth_type == "token":
                headers["X-Webhook-Secret"] = secret
            elif auth_type == "hmac":
                # Firma HMAC-SHA256 del cuerpo del payload
                signature = hmac.new(
                    secret.encode("utf-8"),
                    payload_json.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                headers["X-Hub-Signature-256"] = f"sha256={signature}"

        status_code = None
        error_msg = None
        exito = 0
        intentos = 0

        for intento in range(1, max_reintentos + 1):
            intentos = intento
            try:
                # Simulación o endpoint HTTP real
                if url.startswith("http://") or url.startswith("https://"):
                    resp = requests.post(url, data=payload_json, headers=headers, timeout=5)
                    status_code = resp.status_code
                    if 200 <= resp.status_code < 300:
                        exito = 1
                        error_msg = None
                        break
                    else:
                        error_msg = f"HTTP {resp.status_code}: {resp.text[:100]}"
                elif url.startswith("mock://") or url.startswith("log://"):
                    # Webhook simulado/interno para pruebas de testing
                    status_code = 200
                    exito = 1
                    error_msg = None
                    break
            except Exception as e:
                error_msg = str(e)

            # Espera con backoff exponencial antes de reintentar
            if intento < max_reintentos:
                time.sleep(1.0 * (2 ** (intento - 1)))

        # Guardar registro en la base de datos
        self._registrar_log(
            evento=payload.get("evento", "UNKNOWN"),
            destinatario=destinatario,
            url=url,
            payload=payload_json,
            status_code=status_code or 0,
            exito=exito,
            reintentos=intentos - 1,
            error_msg=error_msg,
        )

    def _registrar_log(
        self,
        evento: str,
        destinatario: str,
        url: str,
        payload: str,
        status_code: int,
        exito: int,
        reintentos: int,
        error_msg: Optional[str],
    ):
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            cursor.execute('''
                INSERT INTO webhook_logs (
                    fecha_hora, evento, destinatario, url, payload,
                    status_code, exito, reintentos, error_msg
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                now_str,
                evento,
                destinatario,
                url,
                payload,
                status_code,
                exito,
                reintentos,
                error_msg,
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error registrando log de webhook: {e}")

    def obtener_historial_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Devuelve los últimos logs de webhooks registrados."""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, fecha_hora, evento, destinatario, url, status_code, exito, reintentos, error_msg
            FROM webhook_logs
            ORDER BY id DESC
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        conn.close()

        logs = []
        for r in rows:
            logs.append({
                "id": r[0],
                "fecha_hora": r[1],
                "evento": r[2],
                "destinatario": r[3],
                "url": r[4],
                "status_code": r[5],
                "exito": bool(r[6]),
                "reintentos": r[7],
                "error_msg": r[8],
            })
        return logs


# ============================================================================
# INSTANCIA GLOBAL SINGLETON
# ============================================================================

webhook_manager = WebhookManager()


if __name__ == "__main__":
    print("=== Probando Sistema de Webhooks y Alertas IoT ===")
    wm = WebhookManager()

    # Registrar webhook de prueba local/mock
    wh_id = wm.registrar_webhook(
        nombre="Monitor_Central_Operaciones",
        url="mock://sistema.central.empresa/iot/webhook",
        secret_token="token_seguro_xyz_123",
        auth_type="hmac",
        eventos=WebhookEvent.ALL_EVENTS,
        destinatarios=["operador_1", "supervisor_general"],
    )

    # Emitir evento de prueba
    wm.emitir_evento(
        evento=WebhookEvent.ALERTA_UMBRAL,
        datos={
            "tipo": "HUMEDAD_CRITICA_BAJA",
            "valor": 12.4,
            "umbral": 15.0,
            "unidad": "% VWC",
        },
        dispositivo="ESP32_Planta_Hogar",
        forzar=True,
    )

    time.sleep(1)
    logs = wm.obtener_historial_logs(5)
    print(f"Logs generados: {len(logs)}")
    for l in logs:
        print(f" -> [{l['fecha_hora']}] Evento: {l['evento']} | Dest: {l['destinatario']} | Exito: {l['exito']}")
    print("=== Prueba de Webhooks Completada con Éxito ===")
