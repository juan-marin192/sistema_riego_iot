"""
dashboard_app.py — FASE 4: Dashboard Web IoT de Riego Automático
Flask + Flask-SocketIO + MQTT integrado
"""

import sqlite3
import json
import threading
import time
import os
from datetime import datetime

import paho.mqtt.client as mqtt
from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit

# =============================================================================
# CONFIGURACIÓN (compatible con listener_iot_riego.py)
# =============================================================================

MQTT_BROKER      = "broker.emqx.io"
MQTT_PORT        = 1883
MQTT_TOPIC_DATA  = "hogar/planta01/telemetria"
MQTT_TOPIC_CTRL  = "hogar/planta01/control"
MQTT_CLIENT_ID   = "Dashboard_Web_IoT"

DB_PATH = os.path.join(os.path.dirname(__file__), "riego_iot.db")

FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000

# =============================================================================
# ESTADO GLOBAL EN MEMORIA
# =============================================================================

estado_actual = {
    "humedad_vwc": None,
    "temperatura": None,
    "humedad_aire": None,
    "vpd_kpa": None,
    "umbral_trigger": None,
    "bomba_estado": "DESCONOCIDO",
    "alerta_timeout": False,
    "dispositivo": "—",
    "fecha_hora": None,
    "mqtt_conectado": False,
    "latencia_ms": None,
    "ultima_actualizacion": None,
}

# Timestamp del último comando enviado (para medir latencia)
_ts_comando_enviado  = None
_bomba_antes_comando = None

registro_operaciones = []   # max 200
_lock_ops = threading.Lock()

# =============================================================================
# GESTOR DE ALARMAS
# =============================================================================

class AlarmManager:
    HUMEDAD_MIN = 20.0   # %
    HUMEDAD_MAX = 80.0   # %
    TEMP_MAX    = 35.0   # °C

    def __init__(self, max_alarmas: int = 100):
        self._alarmas: list[dict] = []
        self._max = max_alarmas
        self._lock = threading.Lock()

    def check_alarms(self, data: dict) -> list[dict]:
        nuevas: list[dict] = []
        ts = datetime.now().isoformat(timespec="seconds")

        vwc  = data.get("humedad_vwc")
        temp = data.get("temperatura")

        if vwc is not None:
            if vwc < self.HUMEDAD_MIN:
                nuevas.append({
                    "timestamp": ts,
                    "tipo": "HUMEDAD_BAJA",
                    "valor": vwc,
                    "umbral": self.HUMEDAD_MIN,
                    "mensaje": f"Humedad VWC {vwc}% por debajo del mínimo ({self.HUMEDAD_MIN}%)",
                })
            elif vwc > self.HUMEDAD_MAX:
                nuevas.append({
                    "timestamp": ts,
                    "tipo": "HUMEDAD_ALTA",
                    "valor": vwc,
                    "umbral": self.HUMEDAD_MAX,
                    "mensaje": f"Humedad VWC {vwc}% por encima del máximo ({self.HUMEDAD_MAX}%)",
                })

        if temp is not None and temp > self.TEMP_MAX:
            nuevas.append({
                "timestamp": ts,
                "tipo": "TEMPERATURA_ALTA",
                "valor": temp,
                "umbral": self.TEMP_MAX,
                "mensaje": f"Temperatura {temp}°C supera el máximo ({self.TEMP_MAX}°C)",
            })

        if data.get("alerta_timeout"):
            nuevas.append({
                "timestamp": ts,
                "tipo": "TIMEOUT_ACTUADOR",
                "valor": 1,
                "umbral": 1,
                "mensaje": "Actuador desactivado por timeout de seguridad",
            })

        if nuevas:
            with self._lock:
                self._alarmas.extend(nuevas)
                self._alarmas = self._alarmas[-self._max:]

        return nuevas

    def get_alarmas(self) -> list[dict]:
        with self._lock:
            return list(reversed(self._alarmas))


alarm_manager = AlarmManager()

# =============================================================================
# BASE DE DATOS — LECTURA
# =============================================================================

def _row_to_dict(row, columns):
    return dict(zip(columns, row))

def db_ultimos_registros(n: int = 50) -> list[dict]:
    cols = ["id", "fecha_hora", "dispositivo", "humedad_vwc",
            "umbral_trigger", "temperatura", "humedad_aire",
            "vpd_kpa", "bomba_estado", "alerta_timeout"]
    try:
        conn = sqlite3.connect(DB_PATH)
        cur  = conn.cursor()
        cur.execute(
            "SELECT * FROM telemetria ORDER BY id DESC LIMIT ?", (n,)
        )
        rows = cur.fetchall()
        conn.close()
        return [_row_to_dict(r, cols) for r in rows]
    except Exception as exc:
        print(f"[DB] Error lectura: {exc}")
        return []

# =============================================================================
# CLIENTE MQTT
# =============================================================================

_mqtt_client: mqtt.Client | None = None

def _registrar_operacion(comando: str, usuario: str, resultado: str):
    with _lock_ops:
        registro_operaciones.append({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "comando":   comando,
            "usuario":   usuario,
            "resultado": resultado,
        })
        if len(registro_operaciones) > 200:
            del registro_operaciones[0]

def _on_mqtt_connect(client, userdata, flags, rc):
    if rc == 0:
        estado_actual["mqtt_conectado"] = True
        print(f"[MQTT] Conectado a {MQTT_BROKER}")
        client.subscribe(MQTT_TOPIC_DATA)
        print(f"[MQTT] Suscripción activa: {MQTT_TOPIC_DATA}")
    else:
        estado_actual["mqtt_conectado"] = False
        print(f"[MQTT] Error de conexión: rc={rc}")

def _on_mqtt_disconnect(client, userdata, rc):
    estado_actual["mqtt_conectado"] = False
    print(f"[MQTT] Desconectado (rc={rc}). Reintentando…")

def _on_mqtt_message(client, userdata, msg):
    global _ts_comando_enviado, _bomba_antes_comando

    try:
        data = json.loads(msg.payload.decode("utf-8"))
    except Exception as exc:
        print(f"[MQTT] JSON inválido: {exc}")
        return

    ahora = datetime.now().isoformat(timespec="seconds")
    bomba_nueva = str(data.get("bomba_estado", "OFF"))

    # --- Medición de latencia ---
    if _ts_comando_enviado is not None and _bomba_antes_comando is not None:
        if bomba_nueva != _bomba_antes_comando:
            lat = (time.time() - _ts_comando_enviado) * 1000
            estado_actual["latencia_ms"] = round(lat, 1)
            print(f"[LATENCIA] {estado_actual['latencia_ms']} ms")
            _ts_comando_enviado  = None
            _bomba_antes_comando = None

    # --- Actualizar estado global ---
    estado_actual.update({
        "humedad_vwc":        data.get("humedad_vwc"),
        "temperatura":        data.get("temperatura"),
        "humedad_aire":       data.get("humedad_aire"),
        "vpd_kpa":            data.get("vpd_kpa"),
        "umbral_trigger":     data.get("umbral_trigger"),
        "bomba_estado":       bomba_nueva,
        "alerta_timeout":     bool(data.get("alerta_timeout", False)),
        "dispositivo":        data.get("dispositivo", "ESP32"),
        "fecha_hora":         data.get("fecha_hora", ahora),
        "ultima_actualizacion": ahora,
    })

    # --- Alarmas ---
    alarmas_nuevas = alarm_manager.check_alarms(estado_actual)

    # --- Emitir a clientes WebSocket ---
    payload_ws = {
        "telemetria":    estado_actual.copy(),
        "alarmas":       alarmas_nuevas,
    }
    socketio.emit("telemetria", payload_ws, namespace="/")


def iniciar_mqtt():
    global _mqtt_client
    _mqtt_client = mqtt.Client(client_id=MQTT_CLIENT_ID)
    _mqtt_client.on_connect    = _on_mqtt_connect
    _mqtt_client.on_disconnect = _on_mqtt_disconnect
    _mqtt_client.on_message    = _on_mqtt_message

    def _loop():
        while True:
            try:
                _mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
                _mqtt_client.loop_forever()
            except Exception as exc:
                estado_actual["mqtt_conectado"] = False
                print(f"[MQTT] Excepción: {exc}. Reintentando en 5 s…")
                time.sleep(5)

    t = threading.Thread(target=_loop, daemon=True, name="mqtt-loop")
    t.start()
    print("[MQTT] Hilo de cliente iniciado.")

# =============================================================================
# FLASK + SOCKETIO
# =============================================================================

app      = Flask(__name__)
app.config["SECRET_KEY"] = "iot-riego-dashboard-secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ---------------------------------------------------------------------------
# HTML EMBEBIDO
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>🌱 Sistema IoT de Riego</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Segoe UI',system-ui,sans-serif;background:#1a1a2e;color:#e0e0e0;min-height:100vh}
  header{background:linear-gradient(135deg,#0f3460,#16213e);padding:18px 32px;display:flex;align-items:center;gap:14px;border-bottom:2px solid #00b4d8;box-shadow:0 2px 16px #00b4d840}
  header h1{font-size:1.45rem;font-weight:700;color:#00b4d8;letter-spacing:.5px}
  header .badge{background:#0f3460;border:1px solid #00b4d8;border-radius:20px;padding:4px 14px;font-size:.78rem;color:#00b4d8}
  main{padding:24px 28px;display:grid;gap:22px}
  /* ===== SECTION TITLES ===== */
  .section-title{font-size:.7rem;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:#00b4d8;margin-bottom:12px;padding-bottom:6px;border-bottom:1px solid #0f3460}
  /* ===== CARDS ROW ===== */
  .cards-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:16px}
  .card{background:#16213e;border-radius:14px;padding:22px 20px;border:1px solid #0f3460;transition:border-color .3s,box-shadow .3s;position:relative;overflow:hidden}
  .card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#00b4d8,#0f3460)}
  .card:hover{border-color:#00b4d8;box-shadow:0 0 18px #00b4d820}
  .card.alerta{border-color:#ff4d6d!important;box-shadow:0 0 18px #ff4d6d30}
  .card.alerta::before{background:linear-gradient(90deg,#ff4d6d,#c9184a)}
  .card-label{font-size:.72rem;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:#8899bb;margin-bottom:10px}
  .card-value{font-size:2.6rem;font-weight:800;color:#e0e0e0;line-height:1;transition:color .3s}
  .card.alerta .card-value{color:#ff4d6d}
  .card-unit{font-size:.85rem;color:#8899bb;margin-top:4px}
  /* ===== GRID 2 COL ===== */
  .grid-2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:22px}
  /* ===== CONTROL PANEL ===== */
  .panel{background:#16213e;border-radius:14px;padding:22px;border:1px solid #0f3460}
  .pump-indicator{display:flex;align-items:center;gap:14px;margin-bottom:18px}
  .pump-dot{width:22px;height:22px;border-radius:50%;background:#333;transition:background .4s,box-shadow .4s;flex-shrink:0}
  .pump-dot.on{background:#00e676;box-shadow:0 0 14px #00e67680}
  .pump-dot.off{background:#ff4d6d;box-shadow:0 0 10px #ff4d6d50}
  .pump-status{font-size:1.2rem;font-weight:700}
  .btn-row{display:flex;gap:12px;flex-wrap:wrap}
  .btn{border:none;border-radius:9px;padding:12px 28px;font-size:.92rem;font-weight:700;cursor:pointer;transition:opacity .2s,transform .1s;letter-spacing:.5px}
  .btn:hover{opacity:.85}
  .btn:active{transform:scale(.96)}
  .btn-on{background:linear-gradient(135deg,#00b4d8,#0077b6);color:#fff}
  .btn-off{background:linear-gradient(135deg,#ff4d6d,#c9184a);color:#fff}
  .btn-disabled{opacity:.4;cursor:not-allowed}
  /* ===== CONECTIVIDAD ===== */
  .conn-row{display:flex;gap:16px;flex-wrap:wrap;align-items:center}
  .conn-item{background:#0f3460;border-radius:9px;padding:12px 18px;display:flex;flex-direction:column;gap:4px;min-width:140px}
  .conn-label{font-size:.68rem;text-transform:uppercase;letter-spacing:1px;color:#8899bb}
  .conn-value{font-size:1rem;font-weight:700;color:#00b4d8}
  .dot-mqtt{width:11px;height:11px;border-radius:50%;background:#ff4d6d;display:inline-block;margin-right:6px;transition:background .4s,box-shadow .4s}
  .dot-mqtt.on{background:#00e676;box-shadow:0 0 8px #00e67680}
  /* ===== TABLA ===== */
  .table-wrap{overflow-x:auto}
  table{width:100%;border-collapse:collapse;font-size:.82rem}
  th{background:#0f3460;color:#00b4d8;padding:9px 10px;text-align:left;font-weight:600;letter-spacing:.5px}
  td{padding:8px 10px;border-bottom:1px solid #0f3460;color:#ccc}
  tr:hover td{background:#1e2d4a}
  .badge-on{background:#00b4d820;color:#00e676;border:1px solid #00e676;border-radius:5px;padding:2px 8px;font-size:.75rem;font-weight:700}
  .badge-off{background:#ff4d6d20;color:#ff4d6d;border:1px solid #ff4d6d;border-radius:5px;padding:2px 8px;font-size:.75rem;font-weight:700}
  /* ===== ALARMAS ===== */
  .alarma-item{background:#1a0a0e;border-left:4px solid #ff4d6d;border-radius:0 9px 9px 0;padding:10px 14px;margin-bottom:8px;font-size:.82rem}
  .alarma-ts{color:#8899bb;font-size:.72rem}
  .alarma-msg{color:#ff8fa3;margin-top:3px}
  .no-alarmas{color:#8899bb;font-style:italic;font-size:.85rem}
  /* ===== CHART ===== */
  .chart-container{position:relative;height:220px}
  /* ===== LOGS ===== */
  .log-item{background:#0a1628;border-radius:7px;padding:8px 12px;margin-bottom:6px;font-size:.78rem;border-left:3px solid #0f3460}
  .log-ts{color:#8899bb}
  .log-cmd{color:#00b4d8;font-weight:700}
  /* ===== RESPONSIVE ===== */
  @media(max-width:600px){
    main{padding:14px 10px}
    header{padding:14px 16px}
    header h1{font-size:1.1rem}
    .card-value{font-size:1.9rem}
  }
</style>
</head>
<body>

<header>
  <span style="font-size:2rem">🌱</span>
  <h1>Sistema IoT de Riego — Dashboard de Control</h1>
  <span class="badge" id="badge-conn">⚡ Conectando…</span>
</header>

<main>

  <!-- TELEMETRÍA EN TIEMPO REAL -->
  <section>
    <div class="section-title">📡 Telemetría en Tiempo Real</div>
    <div class="cards-row">
      <div class="card" id="card-vwc">
        <div class="card-label">Humedad VWC</div>
        <div class="card-value" id="val-vwc">—</div>
        <div class="card-unit">%</div>
      </div>
      <div class="card" id="card-temp">
        <div class="card-label">Temperatura</div>
        <div class="card-value" id="val-temp">—</div>
        <div class="card-unit">°C</div>
      </div>
      <div class="card" id="card-hum">
        <div class="card-label">Humedad Aire</div>
        <div class="card-value" id="val-hum">—</div>
        <div class="card-unit">%</div>
      </div>
      <div class="card" id="card-vpd">
        <div class="card-label">VPD</div>
        <div class="card-value" id="val-vpd">—</div>
        <div class="card-unit">kPa</div>
      </div>
    </div>
  </section>

  <div class="grid-2">

    <!-- CONTROL DE ACTUADOR -->
    <section>
      <div class="section-title">🔧 Control de Actuador</div>
      <div class="panel">
        <div class="pump-indicator">
          <div class="pump-dot off" id="pump-dot"></div>
          <div>
            <div style="font-size:.72rem;text-transform:uppercase;letter-spacing:1px;color:#8899bb">Estado Bomba</div>
            <div class="pump-status" id="pump-status">DESCONOCIDO</div>
          </div>
        </div>
        <div class="btn-row">
          <button class="btn btn-on"  onclick="enviarComando('ON')">💧 ENCENDER</button>
          <button class="btn btn-off" onclick="enviarComando('OFF')">⛔ APAGAR</button>
        </div>
        <div style="margin-top:14px;font-size:.78rem;color:#8899bb" id="ctrl-feedback"></div>
      </div>
    </section>

    <!-- LATENCIA Y CONECTIVIDAD -->
    <section>
      <div class="section-title">📶 Latencia y Conectividad</div>
      <div class="panel">
        <div class="conn-row">
          <div class="conn-item">
            <div class="conn-label">Estado MQTT</div>
            <div class="conn-value"><span class="dot-mqtt" id="dot-mqtt"></span><span id="txt-mqtt">Offline</span></div>
          </div>
          <div class="conn-item">
            <div class="conn-label">Latencia</div>
            <div class="conn-value" id="txt-latencia">— ms</div>
          </div>
          <div class="conn-item">
            <div class="conn-label">Última lectura</div>
            <div class="conn-value" id="txt-ultima" style="font-size:.78rem">—</div>
          </div>
          <div class="conn-item">
            <div class="conn-label">Umbral Trigger</div>
            <div class="conn-value" id="txt-umbral">— %</div>
          </div>
        </div>
        <div style="margin-top:14px">
          <div class="section-title" style="margin-bottom:8px">Umbrales de Alarma</div>
          <div style="display:flex;gap:12px;flex-wrap:wrap;font-size:.82rem;color:#8899bb">
            <span>🌊 VWC mín: <b style="color:#00b4d8">20%</b></span>
            <span>🌊 VWC máx: <b style="color:#00b4d8">80%</b></span>
            <span>🌡️ Temp máx: <b style="color:#ff8c00">35°C</b></span>
          </div>
        </div>
      </div>
    </section>

  </div>

  <!-- GRÁFICA VWC -->
  <section>
    <div class="section-title">📈 Evolución Humedad VWC (últimas 20 lecturas)</div>
    <div class="panel">
      <div class="chart-container">
        <canvas id="chartVWC"></canvas>
      </div>
    </div>
  </section>

  <div class="grid-2">

    <!-- HISTORIAL -->
    <section>
      <div class="section-title">📋 Historial (últimas 10 lecturas)</div>
      <div class="panel">
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Hora</th>
                <th>VWC%</th>
                <th>Temp°C</th>
                <th>Hum%</th>
                <th>VPD</th>
                <th>Bomba</th>
              </tr>
            </thead>
            <tbody id="tabla-historial">
              <tr><td colspan="6" style="color:#8899bb;font-style:italic">Cargando…</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- ALARMAS -->
    <section>
      <div class="section-title">🚨 Alarmas Activas</div>
      <div class="panel" id="panel-alarmas">
        <div class="no-alarmas">Sin alarmas activas</div>
      </div>
    </section>

  </div>

  <!-- REGISTRO DE OPERACIONES -->
  <section>
    <div class="section-title">🗂️ Registro de Operaciones</div>
    <div class="panel" id="panel-ops" style="max-height:220px;overflow-y:auto">
      <div style="color:#8899bb;font-style:italic;font-size:.85rem">Sin operaciones registradas</div>
    </div>
  </section>

</main>

<script>
// ---- Socket.IO ----
const socket = io({transports:['websocket','polling']});

socket.on('connect', () => {
  document.getElementById('badge-conn').textContent = '✅ WebSocket OK';
  socket.emit('get_history');
});
socket.on('disconnect', () => {
  document.getElementById('badge-conn').textContent = '❌ Desconectado';
});

// ---- Chart.js ----
const ctxVWC = document.getElementById('chartVWC').getContext('2d');
const chartVWC = new Chart(ctxVWC, {
  type: 'line',
  data: {
    labels: [],
    datasets: [{
      label: 'Humedad VWC (%)',
      data: [],
      borderColor: '#00b4d8',
      backgroundColor: 'rgba(0,180,216,0.12)',
      borderWidth: 2.5,
      pointRadius: 4,
      pointBackgroundColor: '#00b4d8',
      fill: true,
      tension: 0.4
    },{
      label: 'Umbral MAD (%)',
      data: [],
      borderColor: '#ff8c00',
      borderWidth: 1.5,
      borderDash: [6,4],
      pointRadius: 0,
      fill: false
    }]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: {duration: 400},
    plugins: {legend:{labels:{color:'#aaa',font:{size:11}}}},
    scales: {
      x: {ticks:{color:'#8899bb',maxTicksLimit:8,maxRotation:0},grid:{color:'#1a2a4a'}},
      y: {ticks:{color:'#8899bb'},grid:{color:'#1a2a4a'},suggestedMin:0,suggestedMax:100}
    }
  }
});

// ---- Actualizar interfaz ----
function actualizarDatos(d) {
  if (!d) return;

  // Telemetría
  setCard('vwc',  d.humedad_vwc, [20,80],  true);
  setCard('temp', d.temperatura, [null,35], false);
  setCard('hum',  d.humedad_aire, [null,null], false);
  setCard('vpd',  d.vpd_kpa,     [null,null], false);

  // Bomba
  const on  = (d.bomba_estado === 'ON');
  const dot = document.getElementById('pump-dot');
  dot.className = 'pump-dot ' + (on ? 'on' : 'off');
  document.getElementById('pump-status').textContent = d.bomba_estado || '—';

  // Conectividad
  const mqtt = d.mqtt_conectado;
  const mdot = document.getElementById('dot-mqtt');
  mdot.className = 'dot-mqtt ' + (mqtt ? 'on' : '');
  document.getElementById('txt-mqtt').textContent = mqtt ? 'Online' : 'Offline';
  document.getElementById('txt-latencia').textContent =
    d.latencia_ms != null ? d.latencia_ms + ' ms' : '— ms';
  document.getElementById('txt-ultima').textContent =
    d.ultima_actualizacion ? d.ultima_actualizacion.substring(11) : '—';
  document.getElementById('txt-umbral').textContent =
    d.umbral_trigger != null ? d.umbral_trigger + ' %' : '— %';
}

function setCard(id, val, rango, bajoAlso) {
  const el  = document.getElementById('val-' + id);
  const card = document.getElementById('card-' + id);
  if (val == null) { el.textContent = '—'; card.classList.remove('alerta'); return; }
  el.textContent = typeof val === 'number' ? val.toFixed(1) : val;
  let alerta = false;
  if (rango[1] != null && val > rango[1]) alerta = true;
  if (bajoAlso && rango[0] != null && val < rango[0]) alerta = true;
  card.classList.toggle('alerta', alerta);
}

// ---- Historial ----
function renderTabla(rows) {
  const tbody = document.getElementById('tabla-historial');
  if (!rows || rows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" style="color:#8899bb;font-style:italic">Sin datos</td></tr>';
    return;
  }
  const slice = rows.slice(0, 10);
  tbody.innerHTML = slice.map(r => {
    const hora = r.fecha_hora ? r.fecha_hora.substring(11,19) : '—';
    const badge = r.bomba_estado === 'ON'
      ? '<span class="badge-on">ON</span>'
      : '<span class="badge-off">OFF</span>';
    return `<tr>
      <td>${hora}</td>
      <td>${r.humedad_vwc != null ? r.humedad_vwc.toFixed(1) : '—'}</td>
      <td>${r.temperatura  != null ? r.temperatura.toFixed(1)  : '—'}</td>
      <td>${r.humedad_aire != null ? r.humedad_aire.toFixed(1) : '—'}</td>
      <td>${r.vpd_kpa      != null ? r.vpd_kpa.toFixed(2)      : '—'}</td>
      <td>${badge}</td>
    </tr>`;
  }).join('');
}

// ---- Gráfica ----
function actualizarGrafica(rows) {
  if (!rows || rows.length === 0) return;
  const slice = [...rows].reverse().slice(-20);
  const labels  = slice.map(r => r.fecha_hora ? r.fecha_hora.substring(11,19) : '');
  const vwcData = slice.map(r => r.humedad_vwc != null ? r.humedad_vwc : null);
  const trgData = slice.map(r => r.umbral_trigger != null ? r.umbral_trigger : null);
  chartVWC.data.labels            = labels;
  chartVWC.data.datasets[0].data  = vwcData;
  chartVWC.data.datasets[1].data  = trgData;
  chartVWC.update();
}

// ---- Alarmas ----
function renderAlarmas(alarmas) {
  const p = document.getElementById('panel-alarmas');
  if (!alarmas || alarmas.length === 0) {
    p.innerHTML = '<div class="no-alarmas">✅ Sin alarmas activas</div>';
    return;
  }
  p.innerHTML = alarmas.slice(0,10).map(a =>
    `<div class="alarma-item">
      <div class="alarma-ts">${a.timestamp} · <b>${a.tipo}</b></div>
      <div class="alarma-msg">${a.mensaje}</div>
    </div>`
  ).join('');
}

// ---- Operaciones ----
function renderOps(ops) {
  const p = document.getElementById('panel-ops');
  if (!ops || ops.length === 0) {
    p.innerHTML = '<div style="color:#8899bb;font-style:italic;font-size:.85rem">Sin operaciones</div>';
    return;
  }
  p.innerHTML = ops.slice(-10).reverse().map(o =>
    `<div class="log-item">
      <span class="log-ts">${o.timestamp}</span>
      <span class="log-cmd"> [${o.comando}]</span>
      <span style="color:#ccc"> ${o.usuario} → ${o.resultado}</span>
    </div>`
  ).join('');
}

// ---- WebSocket events ----
socket.on('estado_actual', data => {
  actualizarDatos(data);
});

socket.on('telemetria', data => {
  if (data.telemetria) actualizarDatos(data.telemetria);
  if (data.alarmas && data.alarmas.length) renderAlarmas(data.alarmas);
});

socket.on('historial', data => {
  renderTabla(data.registros);
  actualizarGrafica(data.registros);
});

// ---- Auto-refresh cada 5 s via REST ----
function autoRefresh() {
  fetch('/api/telemetria').then(r => r.json()).then(d => {
    renderTabla(d.registros);
    actualizarGrafica(d.registros);
  }).catch(() => {});

  fetch('/api/estado').then(r => r.json()).then(d => {
    actualizarDatos(d);
  }).catch(() => {});

  fetch('/api/alarmas').then(r => r.json()).then(d => {
    renderAlarmas(d.alarmas);
  }).catch(() => {});

  fetch('/api/operaciones').then(r => r.json()).then(d => {
    renderOps(d.operaciones);
  }).catch(() => {});
}

setInterval(autoRefresh, 5000);
autoRefresh();  // primera carga inmediata

// ---- Enviar comando ----
function enviarComando(cmd) {
  const labels = {ON: 'ENCENDER', OFF: 'APAGAR'};
  if (!confirm(`¿Confirma ${labels[cmd]} la bomba de riego?`)) return;

  document.getElementById('ctrl-feedback').textContent = '⏳ Enviando comando…';

  fetch('/api/control', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({comando: cmd, user: 'dashboard'})
  })
  .then(r => r.json())
  .then(d => {
    document.getElementById('ctrl-feedback').textContent =
      d.ok ? `✅ Comando ${cmd} enviado (${d.timestamp})` : `❌ Error: ${d.error}`;
  })
  .catch(e => {
    document.getElementById('ctrl-feedback').textContent = '❌ Error de red: ' + e;
  });
}
</script>
</body>
</html>
"""

# =============================================================================
# RUTAS REST
# =============================================================================

@app.route("/")
def index():
    return DASHBOARD_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/telemetria")
def api_telemetria():
    registros = db_ultimos_registros(50)
    return jsonify({"ok": True, "registros": registros, "total": len(registros)})


@app.route("/api/estado")
def api_estado():
    return jsonify({**estado_actual, "ok": True})


@app.route("/api/control", methods=["POST"])
def api_control():
    global _ts_comando_enviado, _bomba_antes_comando

    body = request.get_json(silent=True) or {}
    cmd  = str(body.get("comando", "")).upper()
    user = str(body.get("user", "dashboard"))

    if cmd not in ("ON", "OFF"):
        return jsonify({"ok": False, "error": "Comando inválido. Use ON u OFF."}), 400

    if _mqtt_client is None or not estado_actual["mqtt_conectado"]:
        _registrar_operacion(cmd, user, "ERROR: MQTT desconectado")
        return jsonify({"ok": False, "error": "Broker MQTT no disponible"}), 503

    try:
        # Registrar timestamp para medir latencia
        _ts_comando_enviado  = time.time()
        _bomba_antes_comando = estado_actual.get("bomba_estado")

        result = _mqtt_client.publish(MQTT_TOPIC_CTRL, cmd, qos=1)
        ts = datetime.now().isoformat(timespec="seconds")

        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            _registrar_operacion(cmd, user, "OK")
            return jsonify({"ok": True, "comando": cmd, "timestamp": ts})
        else:
            _ts_comando_enviado  = None
            _bomba_antes_comando = None
            _registrar_operacion(cmd, user, f"ERROR rc={result.rc}")
            return jsonify({"ok": False, "error": f"Publish falló (rc={result.rc})"}), 500

    except Exception as exc:
        _registrar_operacion(cmd, user, f"EXCEPCIÓN: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/alarmas")
def api_alarmas():
    return jsonify({"ok": True, "alarmas": alarm_manager.get_alarmas()})


@app.route("/api/operaciones")
def api_operaciones():
    with _lock_ops:
        ops = list(reversed(registro_operaciones[-50:]))
    return jsonify({"ok": True, "operaciones": ops})


# =============================================================================
# EVENTOS WEBSOCKET
# =============================================================================

@socketio.on("connect")
def ws_connect():
    emit("estado_actual", estado_actual)
    print("[WS] Cliente conectado")


@socketio.on("get_history")
def ws_get_history():
    registros = db_ultimos_registros(50)
    emit("historial", {"registros": registros})


# =============================================================================
# PUNTO DE ENTRADA
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  🌱 Dashboard IoT Riego — Iniciando servidor")
    print(f"  URL: http://localhost:{FLASK_PORT}/")
    print(f"  MQTT Broker: {MQTT_BROKER}:{MQTT_PORT}")
    print(f"  DB: {DB_PATH}")
    print("=" * 60)

    iniciar_mqtt()

    socketio.run(
        app,
        host=FLASK_HOST,
        port=FLASK_PORT,
        debug=False,
        use_reloader=False,
        log_output=True,
    )
