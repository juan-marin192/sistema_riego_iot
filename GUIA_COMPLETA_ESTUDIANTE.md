# 🌱 Guía Completa del Estudiante — Sistema de Riego IoT con ESP32

> **Para:** Juan Marín | **Proyecto:** `sistema_riego_iot` | **GitHub:** `juan-marin192`  
> **Propósito:** Ejecutar, probar y subir el proyecto completo paso a paso, desde cero.

---

## 📋 Tabla de Contenidos

1. [Entender el Proyecto](#1-entender-el-proyecto)
2. [Componentes del Circuito Wokwi](#2-componentes-del-circuito-wokwi)
3. [Preparar el Entorno Python](#3-preparar-el-entorno-python)
4. [Generar Certificados TLS](#4-generar-certificados-tls)
5. [Iniciar el Backend Listener — Terminal 1](#5-iniciar-el-backend-listener--terminal-1)
6. [Iniciar el Dashboard Web — Terminal 2](#6-iniciar-el-dashboard-web--terminal-2)
7. [Iniciar el Bot de Telegram — Terminal 3](#7-iniciar-el-bot-de-telegram--terminal-3)
8. [Ejecutar la Simulación en Wokwi](#8-ejecutar-la-simulación-en-wokwi)
9. [Observar el Flujo Completo de Datos](#9-observar-el-flujo-completo-de-datos)
10. [Probar Control Remoto desde Telegram](#10-probar-control-remoto-desde-telegram)
11. [Probar el Dashboard Web](#11-probar-el-dashboard-web)
12. [Ejecutar las Pruebas de Seguridad](#12-ejecutar-las-pruebas-de-seguridad)
13. [Generar Reporte de Gráficas](#13-generar-reporte-de-gráficas)
14. [Subir Todo al Repositorio Git](#14-subir-todo-al-repositorio-git)
15. [Verificación Final](#15-verificación-final)

---

## 1. Entender el Proyecto

### ¿Qué hace este sistema?

Este proyecto es un **sistema inteligente de riego automático para plantas de hogar**, basado en IoT (Internet de las Cosas). Funciona así:

```
[ESP32 en Wokwi] ─── WiFi/MQTT ───► [broker.emqx.io] ───► [Backend Python]
       ↑                                                          │
  [Potenciómetro]                                                 ├──► [Dashboard Web :5000]
  [DHT22 Sensor]                                                  ├──► [Bot Telegram]
  [LCD Display]                                                   └──► [Base de Datos SQLite]
  [Relay/Bomba]
  [LED Estado]
```

**El flujo es:**
1. El ESP32 (simulado en Wokwi) lee sensores cada 1 segundo
2. Calcula si la planta necesita agua usando el modelo matemático **MAD/VPD** (estándares FAO)
3. Publica datos JSON cada 5 segundos al broker MQTT público `broker.emqx.io`
4. El backend Python recibe esos datos, los guarda en SQLite y envía alertas
5. El Dashboard Web muestra gráficas en tiempo real
6. El Bot de Telegram envía notificaciones y acepta comandos de control

### Modelo Matemático Usado (FAO/OSU/UNAL)

| Parámetro | Valor | Significado |
|-----------|-------|-------------|
| `FC` (Capacidad de Campo) | 35% VWC | Suelo completamente húmedo |
| `PMP` (Punto Marchitez) | 15% VWC | Suelo peligrosamente seco |
| `TAW` (Agua Total Disponible) | 20% | FC - PMP |
| `MAD` (Depleción Máxima) | 40% | % de TAW que se puede consumir |
| `UMBRAL_TRIGGER` | **27% VWC** | Si baja de aquí → activa riego |

> **UMBRAL_TRIGGER = FC - (MAD × TAW) = 35 - (0.40 × 20) = 27%**

### Payload JSON que publica el ESP32

Cada 5 segundos, el ESP32 publica esto en el topic `hogar/planta01/telemetria`:

```json
{
  "dispositivo":    "ESP32_Planta_Hogar",
  "humedad_vwc":    18.5,
  "umbral_trigger": 27.0,
  "temperatura":    23.4,
  "humedad_aire":   65.2,
  "vpd_kpa":        0.87,
  "bomba_estado":   "ON",
  "alerta_timeout": false
}
```

### Topics MQTT

| Topic | Dirección | Uso |
|-------|-----------|-----|
| `hogar/planta01/telemetria` | ESP32 → Backend | Datos de sensores |
| `hogar/planta01/control` | Backend → ESP32 | Comandos `ON` / `OFF` |

---

## 2. Componentes del Circuito Wokwi

### Hardware Simulado

| Componente | ID Wokwi | Pin ESP32 | Función |
|-----------|----------|-----------|---------|
| ESP32 DevKit C v4 | `esp` | — | Microcontrolador principal |
| Potenciómetro | `pot_humedad` | GPIO 34 (ADC1) | Simula sensor de humedad de suelo |
| DHT22 | `dht22` | GPIO 15 | Temperatura y humedad del aire |
| Módulo Relay | `rele_bomba` | GPIO 26 | Control de bomba de agua |
| LCD 1602 I2C | `lcd` | SDA:21, SCL:22 | Pantalla de estado |
| LED | `led_estado` | GPIO 2 | Indicador visual de estado |
| Resistencia | `r1` | — | 220Ω para el LED |

### Cómo Leer la Pantalla LCD

```
┌────────────────┐
│ VWC:18% Trig:27│  ← Fila 1: Humedad actual vs umbral
│ VPD:0.87kPa OK │  ← Fila 2: Estado del sistema
└────────────────┘
```

**Mensajes posibles en fila 2:**
- `VPD:X.XXkPa OK` → Sistema normal, suelo húmedo
- `REGANDO: Xs    ` → Bomba activa, contando segundos
- `ALERTA: TIMEOUT!` → Bomba activa > 10 seg → apagada por seguridad
- `ERR: SENSOR OFF ` → Sensor desconectado

### Calibración del Potenciómetro

El potenciómetro simula el sensor capacitivo de humedad:

| Posición Potenciómetro | Valor ADC | % VWC | Estado |
|-----------------------|-----------|-------|--------|
| Girado al máximo (derecha) | ~3200 | 0% | Suelo muy seco → **ACTIVA BOMBA** |
| Centro | ~2200 | ~50% | Suelo moderado |
| Girado al mínimo (izquierda) | ~1200 | 100% | Suelo saturado |

> **Para probar el riego automático:** Gira el potenciómetro hacia la derecha (ADC > 2700) para simular suelo seco. El relay se activará automáticamente.

---

## 3. Preparar el Entorno Python

### Paso 3.1 — Clonar o Ubicarse en el Proyecto

Si ya tienes el proyecto descargado:
```powershell
# En Windows PowerShell
cd "C:\Users\USUARIO\Downloads\Proyecto-Wowki"
```

Si necesitas clonar desde GitHub (en un equipo nuevo):
```powershell
git clone https://github.com/juan-marin192/sistema_riego_iot.git
cd sistema_riego_iot
```

### Paso 3.2 — Verificar Python

```powershell
python --version
```
**Esperado:** `Python 3.9.x` o superior. Si no tienes Python, descárgalo de [python.org](https://www.python.org).

### Paso 3.3 — Crear y Activar el Entorno Virtual

```powershell
# Crear el entorno virtual (solo una vez)
python -m venv venv

# Activar en Windows PowerShell
.\venv\Scripts\Activate.ps1
```

**Verás en el prompt:** `(venv) PS C:\...\Proyecto-Wowki>`

> ⚠️ **Si PowerShell bloquea la ejecución de scripts**, ejecuta primero:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

### Paso 3.4 — Instalar Dependencias

```powershell
pip install -r requirements.txt
```

**Dependencias instaladas:**
- `paho-mqtt` — Conexión al broker MQTT
- `python-telegram-bot` — Bot de Telegram
- `python-dotenv` — Gestión de credenciales
- `flask` + `flask-socketio` — Dashboard web
- `matplotlib` — Gráficas
- `cryptography` — Certificados TLS

**Espera de 1-3 minutos.** Al finalizar verás algo como:
```
Successfully installed flask-3.x.x paho-mqtt-2.x.x python-telegram-bot-...
```

### Paso 3.5 — Configurar las Credenciales (.env)

Copia la plantilla de ejemplo:
```powershell
Copy-Item .env.example .env
```

Abre el archivo `.env` con cualquier editor:
```powershell
notepad .env
```

**Contenido que debes editar:**
```env
# ─── MQTT ───────────────────────────────────────────
MQTT_BROKER=broker.emqx.io
MQTT_PORT=1883
MQTT_TOPIC_DATA=hogar/planta01/telemetria
MQTT_TOPIC_CTRL=hogar/planta01/control
MQTT_USERNAME=
MQTT_PASSWORD=

# ─── TELEGRAM ────────────────────────────────────────
TELEGRAM_BOT_TOKEN=TU_TOKEN_AQUI
TELEGRAM_CHAT_ID=TU_CHAT_ID_AQUI
TELEGRAM_CHAT_IDS_PERMITIDOS=TU_CHAT_ID_AQUI

# ─── DASHBOARD ───────────────────────────────────────
FLASK_SECRET_KEY=una_clave_secreta_aleatoria_aqui
DASHBOARD_PORT=5000
```

> **¿Cómo obtener tu Token de Telegram?**
> 1. Abre Telegram → busca `@BotFather`
> 2. Envía `/newbot` → sigue las instrucciones
> 3. Copia el token que te da (formato: `123456789:AAHxxxxxx...`)
>
> **¿Cómo obtener tu Chat ID?**
> 1. Busca `@userinfobot` en Telegram
> 2. Envía `/start` → te responde con tu ID numérico

Guarda el archivo con **Ctrl+S**.

---

## 4. Generar Certificados TLS

Los certificados TLS agregan seguridad a la conexión MQTT (encriptación).

```powershell
# Asegúrate de tener el venv activado
.\venv\Scripts\Activate.ps1

# Generar certificados
python certs/generate_certs.py
```

**Salida esperada:**
```
✓ Generando CA (Autoridad Certificadora)...
✓ CA generada: certs/ca.crt
✓ Generando certificado del servidor...
✓ Servidor generado: certs/server.crt, certs/server.key
✓ Generando certificado del cliente...
✓ Cliente generado: certs/client.crt, certs/client.key
✓ Todos los certificados generados correctamente.
```

**Archivos generados en `certs/`:**
```
certs/
├── ca.crt          ← Autoridad Certificadora (pública)
├── ca.key          ← Clave privada CA (NO subir a Git)
├── server.crt      ← Certificado del servidor
├── server.key      ← Clave del servidor (NO subir a Git)
├── client.crt      ← Certificado del cliente ESP32
└── client.key      ← Clave del cliente (NO subir a Git)
```

> **Nota:** Los archivos `.key` están protegidos por `.gitignore` y NUNCA se subirán a GitHub.

---

## 5. Iniciar el Backend Listener — Terminal 1

El **listener** es el cerebro del sistema: recibe datos del ESP32 vía MQTT y los procesa.

### Abrir una nueva terminal PowerShell

```powershell
cd "C:\Users\USUARIO\Downloads\Proyecto-Wowki"
.\venv\Scripts\Activate.ps1
python listener_iot_riego.py
```

### Salida Esperada en la Terminal

```
============================================================
  SISTEMA DE RIEGO IOT - BACKEND v2.0
  Seguridad: Habilitada | TLS: Configurado
============================================================
[2026-11-17 22:50:01] INFO - Conectando al broker MQTT: broker.emqx.io:1883
[2026-11-17 22:50:02] INFO - Conexión MQTT establecida exitosamente
[2026-11-17 22:50:02] INFO - Subscrito a: hogar/planta01/telemetria
[2026-11-17 22:50:02] INFO - Base de datos inicializada: riego_iot.db
[2026-11-17 22:50:02] INFO - Bot de Telegram iniciado. Esperando mensajes...
Esperando mensajes MQTT...
```

> **Si ves "Conexión MQTT establecida"** → ✅ El backend está listo  
> **Si ves "Error de conexión"** → Verifica tu conexión a internet

### ¿Qué hace el Listener?

| Acción | Descripción |
|--------|-------------|
| Conecta al broker `broker.emqx.io` | Puerto 1883 (sin TLS) o 8883 (con TLS) |
| Recibe JSON del ESP32 | Cada 5 segundos |
| Guarda en SQLite | Base de datos `riego_iot.db` |
| Verifica alarmas | Si VWC < 15% → alerta crítica |
| Notifica por Telegram | Cuando la bomba se activa/desactiva |
| Registra en logs | Archivo `logs/backend.log` |

**Deja esta terminal abierta** — el listener debe correr continuamente.

---

## 6. Iniciar el Dashboard Web — Terminal 2

El dashboard muestra gráficas y permite controlar la bomba desde el navegador.

### Abrir una SEGUNDA terminal PowerShell (nueva ventana)

```powershell
cd "C:\Users\USUARIO\Downloads\Proyecto-Wowki"
.\venv\Scripts\Activate.ps1
python dashboard_app.py
```

### Salida Esperada

```
 * Dashboard de Riego IoT iniciando...
 * Flask + SocketIO en http://localhost:5000
 * Modo: desarrollo (debug=True)
 * Running on http://127.0.0.1:5000
```

### Abrir el Dashboard en el Navegador

Abre tu navegador (Chrome, Firefox o Edge) y ve a:
```
http://localhost:5000
```

### ¿Qué verás en el Dashboard?

```
╔══════════════════════════════════════════════════╗
║  🌱 Sistema de Riego IoT — Dashboard             ║
╠══════════════════════════════════════════════════╣
║  [💧 Humedad: 18%]  [🌡️ Temp: 23°C]  [💨 VPD: 0.87]  ║
╠══════════════════════════════════════════════════╣
║  📊 Gráfica de Telemetría en Tiempo Real         ║
║  [══════════════════════════════════]            ║
╠══════════════════════════════════════════════════╣
║  🎛️ Control Manual:  [▶ ENCENDER]  [⏹ APAGAR]  ║
╚══════════════════════════════════════════════════╝
```

**Endpoints disponibles del API REST:**

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/` | GET | Dashboard principal |
| `/api/telemetria` | GET | Últimos datos de sensores (JSON) |
| `/api/estado` | GET | Estado actual de la bomba |
| `/api/alarmas` | GET | Historial de alarmas |
| `/api/control` | POST | Enviar comando ON/OFF |

**Deja esta terminal abierta** — el dashboard debe correr continuamente.

---

## 7. Iniciar el Bot de Telegram — Terminal 3

El bot de Telegram permite recibir alertas y controlar el riego desde el celular.

### Abrir una TERCERA terminal PowerShell (nueva ventana)

```powershell
cd "C:\Users\USUARIO\Downloads\Proyecto-Wowki"
.\venv\Scripts\Activate.ps1
python telegram_bot.py
```

### Salida Esperada

```
Bot de Telegram iniciado correctamente.
Listening for messages...
```

### Conectar con el Bot desde Telegram

1. Abre la app de **Telegram** en tu celular o en [web.telegram.org](https://web.telegram.org)
2. Busca tu bot por su nombre de usuario (el que configuraste en BotFather)
3. Envía el comando: `/start`

### Respuesta del Bot al `/start`

```
🌱 Sistema de Riego IoT — v2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Comandos disponibles:

🌊 /riego_on    → Encender bomba manualmente
🛑 /riego_off   → Apagar bomba manualmente
📊 /estado      → Ver estado actual del sistema
📈 /grafica     → Generar gráfica de telemetría
🔔 /alarmas     → Ver historial de alarmas
ℹ️  /info        → Información del sistema
❓ /ayuda       → Mostrar este menú
```

### Comandos del Bot

| Comando | Acción |
|---------|--------|
| `/start` | Menú de bienvenida |
| `/estado` | Ver VWC, temperatura, estado bomba |
| `/riego_on` | Pide confirmación → activa bomba |
| `/riego_off` | Pide confirmación → apaga bomba |
| `/alarmas` | Lista alarmas recientes |
| `/grafica` | Genera y envía imagen PNG de telemetría |
| `/info` | Versión, uptime, configuración |
| `/ayuda` | Lista todos los comandos |

> **Confirmación doble:** Los comandos de control (`/riego_on`, `/riego_off`) piden confirmación con botones inline [✅ Confirmar] [❌ Cancelar] para evitar activaciones accidentales.

**Deja esta terminal abierta** — el bot debe correr continuamente.

---

## 8. Ejecutar la Simulación en Wokwi

### Paso 8.1 — Abrir el Proyecto en Wokwi

1. Abre tu navegador y ve a: **[https://wokwi.com](https://wokwi.com)**
2. Inicia sesión con tu cuenta de Wokwi (o GitHub)
3. Abre el proyecto del sistema de riego (busca en "Mis proyectos")

> **Alternativa:** Si tienes el código fuente en `Codigo_Wokwi-proyecto.txt`, crea un nuevo proyecto ESP32 en Wokwi y pega el código del archivo.

### Paso 8.2 — Verificar la Configuración del Sketch

Antes de ejecutar, verifica que el código del `sketch.ino` tenga estos valores:

```cpp
// Credenciales de red (Wokwi usa su propia red WiFi)
const char* WIFI_SSID       = "Wokwi-GUEST";  // ← Debe ser exactamente esto
const char* WIFI_PASSWORD   = "";               // ← Sin contraseña

// Broker MQTT público
const char* MQTT_BROKER     = "broker.emqx.io";
const int   MQTT_PORT       = 1883;

// Topics (deben coincidir con el backend Python)
const char* MQTT_TOPIC_DATA = "hogar/planta01/telemetria";
const char* MQTT_TOPIC_CTRL = "hogar/planta01/control";
```

> **¡IMPORTANTE!** El SSID `Wokwi-GUEST` es especial — solo funciona dentro de la simulación de Wokwi. No lo cambies.

### Paso 8.3 — Ejecutar la Simulación

1. Haz clic en el botón **▶ Play** (triángulo verde) en Wokwi
2. Espera unos segundos mientras el ESP32 simula el arranque

### Paso 8.4 — Observar la Consola Serial de Wokwi

En el panel de **Serial Monitor** de Wokwi verás:

```
==================================================
  SISTEMA DE RIEGO HOGAR CON MODELO MAD Y VPD
==================================================
Conectando a red WiFi: Wokwi-GUEST
..........
Conexión WiFi establecida exitosamente.
Intentando conexión al Broker MQTT...Conectado al Broker MQTT.
Publicado en MQTT [hogar/planta01/telemetria]: {"dispositivo":"ESP32_Planta_Hogar","humedad_vwc":0,"umbral_trigger":27,"temperatura":25.3,"humedad_aire":60.1,"vpd_kpa":0.94,"bomba_estado":"OFF","alerta_timeout":false}
Publicado en MQTT [hogar/planta01/telemetria]: {"dispositivo":"ESP32_Planta_Hogar"...}
```

### Paso 8.5 — Observar la Pantalla LCD en Wokwi

La pantalla LCD del circuito mostrará:
```
VWC:0% Trig:27%
VPD:0.94kPa OK
```

### Paso 8.6 — Simular Suelo Seco (Activar Riego Automático)

1. En el circuito de Wokwi, localiza el **potenciómetro** (componente con perilla)
2. Haz clic en la perilla y gírala hacia la **derecha** (valor máximo)
3. El valor ADC subirá a ~3200 → VWC calculado ≈ 0%

**Lo que ocurrirá:**
- La LCD mostrará: `VWC:0% Trig:27%` / `REGANDO: 1s`
- El relay (bomba) se activa → el LED de estado cambia de color en Wokwi
- El Serial Monitor mostrará: `INICIO RIEGO AUTOMATICO: VWC (0%) <= Trigger MAD (27%)`
- Después de 10 segundos: `ALERTA: TIMEOUT!` (protección contra inundación)

---

## 9. Observar el Flujo Completo de Datos

Con los 3 servicios Python activos y Wokwi ejecutándose, observa el flujo:

### En la Terminal 1 (Backend Listener)

Deberías ver mensajes entrantes cada 5 segundos:
```
[22:55:10] INFO - Telemetría recibida de ESP32_Planta_Hogar
[22:55:10] INFO - VWC: 0.0% | Temp: 25.3°C | Bomba: ON
[22:55:10] INFO - 💾 Guardado en base de datos (ID: 42)
[22:55:10] INFO - 🔔 Telegram: Notificación enviada - Bomba activada
[22:55:15] INFO - Telemetría recibida de ESP32_Planta_Hogar
```

> **Si NO ves mensajes después de 10 segundos:** Verifica que Wokwi esté ejecutándose (▶) y que el ESP32 en Wokwi diga "Conectado al Broker MQTT".

### En el Dashboard Web (http://localhost:5000)

- Las **tarjetas de información** se actualizan con los nuevos datos
- La **gráfica** muestra una línea nueva cada 5 segundos
- El **indicador de bomba** cambia de color (rojo = ON, gris = OFF)

### En Telegram

Cuando la bomba se activa automáticamente recibirás:
```
🌊 ALERTA: Bomba de riego ACTIVADA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📍 Dispositivo: ESP32_Planta_Hogar
💧 Humedad suelo: 0% VWC
🎯 Umbral MAD: 27% VWC
⏰ Hora: 22:55:10
Motivo: Humedad por debajo del umbral MAD
```

---

## 10. Probar Control Remoto desde Telegram

Este es el flujo de control inverso: **Telegram → Backend → MQTT → ESP32**.

### Enviar Comando de Riego ON

1. En Telegram, envía a tu bot: `/riego_on`

2. El bot responderá con confirmación:
```
⚠️ ¿Confirmas activar el riego?
Dispositivo: ESP32_Planta_Hogar
Estado actual: Bomba OFF

[✅ Confirmar]  [❌ Cancelar]
```

3. Haz clic en **[✅ Confirmar]**

4. El bot confirma:
```
✅ Comando ON enviado al sistema
⏱️ Latencia: 45ms
```

5. En Wokwi verás:
   - El relay se activa (cambia de estado visual)
   - En Serial Monitor: `Comando MQTT recibido en [hogar/planta01/control]: ON` / `INICIO RIEGO MANUAL VIA MQTT`
   - En la LCD: `REGANDO: 1s`

### Enviar Comando de Riego OFF

1. Envía: `/riego_off`
2. Confirma con **[✅ Confirmar]**
3. En Wokwi el relay se desactiva → `SEGURIDAD: Bomba APAGADA -> Comando Remoto OFF por MQTT`

---

## 11. Probar el Dashboard Web

### Verificar las Tarjetas de Datos

Ve a `http://localhost:5000` y verifica que las tarjetas muestren datos actualizados:

| Tarjeta | Valor esperado | Rango normal |
|---------|---------------|--------------|
| 💧 Humedad Suelo | X% VWC | 0% - 100% |
| 🌡️ Temperatura | XX.X°C | 15°C - 40°C |
| 💨 Humedad Aire | XX% | 20% - 90% |
| 🌬️ VPD | X.XX kPa | 0.2 - 2.0 kPa |
| ⚙️ Estado Bomba | ON / OFF | — |

### Probar los Botones de Control

1. Haz clic en **[▶ ENCENDER]** en el dashboard
2. Observa en Wokwi que el relay se activa
3. Haz clic en **[⏹ APAGAR]**
4. Observa que el relay se desactiva

### Probar los Endpoints de la API

Puedes probar los endpoints directamente en el navegador o con `curl`:

```powershell
# En una nueva terminal (con el venv activado)

# Ver último estado de telemetría
Invoke-WebRequest -Uri "http://localhost:5000/api/telemetria" -Method GET | Select-Object -Expand Content

# Ver estado de la bomba
Invoke-WebRequest -Uri "http://localhost:5000/api/estado" -Method GET | Select-Object -Expand Content

# Ver alarmas
Invoke-WebRequest -Uri "http://localhost:5000/api/alarmas" -Method GET | Select-Object -Expand Content
```

**Respuesta esperada de `/api/telemetria`:**
```json
{
  "humedad_vwc": 18.5,
  "temperatura": 23.4,
  "humedad_aire": 65.2,
  "vpd_kpa": 0.87,
  "bomba_estado": "OFF",
  "timestamp": "2026-11-17T22:55:10"
}
```

### Ver el Log del Backend

```powershell
# Ver las últimas 50 líneas del log
Get-Content logs\backend.log -Tail 50
```

---

## 12. Ejecutar las Pruebas de Seguridad

Las pruebas verifican que todos los módulos de seguridad funcionen correctamente.

### Abrir una nueva terminal (con venv activado)

```powershell
cd "C:\Users\USUARIO\Downloads\Proyecto-Wowki"
.\venv\Scripts\Activate.ps1
python test_security_and_integration.py
```

### Salida Esperada (13/13 pruebas exitosas)

```
============================================================
EJECUTANDO SUITE DE PRUEBAS DE SEGURIDAD E INTEGRACIÓN
============================================================

✅ [1/13] Carga de configuración desde .env
✅ [2/13] Validación de variables de entorno críticas
✅ [3/13] Validación de comandos MQTT seguros
✅ [4/13] Reglas ACL para control de acceso MQTT
✅ [5/13] Validación de identidad de dispositivo
✅ [6/13] Endpoints del dashboard Flask
✅ [7/13] Rechazo de comandos maliciosos (inyección)
✅ [8/13] Generación y verificación de HMAC-SHA256 (webhooks)
✅ [9/13] Deduplicación de eventos de alarma
✅ [10/13] Tabla 'operaciones' en base de datos
✅ [11/13] Tabla 'alarmas' en base de datos
✅ [12/13] Menú de confirmación del bot Telegram
✅ [13/13] Certificados TLS generados correctamente

============================================================
RESULTADO FINAL: 13/13 PRUEBAS EXITOSAS (100.0%)
============================================================
✅ Todas las pruebas de seguridad pasaron correctamente.
```

> **Si alguna prueba falla** → Lee el mensaje de error, verifica que el `.env` esté configurado correctamente y que `certs/` exista.

### Ver el Reporte de Pruebas

```powershell
Get-Content registros\resultado_pruebas.json | ConvertFrom-Json | Format-List
```

---

## 13. Generar Reporte de Gráficas

```powershell
cd "C:\Users\USUARIO\Downloads\Proyecto-Wowki"
.\venv\Scripts\Activate.ps1
python graficas.py
```

**Salida esperada:**
```
Generando gráficas de telemetría...
Datos cargados: 42 registros desde riego_iot.db
✓ Gráfica guardada en: registros/reporte_telemetria.png
```

Abre la imagen generada:
```powershell
Start-Process "registros\reporte_telemetria.png"
```

Verás una gráfica con:
- Línea de humedad del suelo (% VWC) en el tiempo
- Línea de temperatura del aire
- Línea roja punteada = umbral MAD (27%)
- Barras grises = períodos de riego activo

---

## 14. Subir Todo al Repositorio Git

> **¡IMPORTANTE!** Nunca subas el archivo `.env` ni las claves privadas (`*.key`). El `.gitignore` los protege automáticamente.

### Paso 14.1 — Verificar que el .gitignore Funciona

```powershell
cd "C:\Users\USUARIO\Downloads\Proyecto-Wowki"
git status
```

**Verifica que ESTOS archivos NO aparezcan en la lista:**
- ❌ `.env` (con tus credenciales reales)
- ❌ `certs/ca.key`
- ❌ `certs/server.key`
- ❌ `certs/client.key`
- ❌ `riego_iot.db` (base de datos con datos locales)
- ❌ `venv/` (entorno virtual)
- ❌ `logs/` (logs locales)

> Si aparece el `.env` en la lista, **DETENTE** y verifica que el `.gitignore` contenga la línea `.env`.

### Paso 14.2 — Remover Archivos Que No Deben Estar en Git

La base de datos `riego_iot.db` fue commiteada anteriormente. Hay que removerla del índice de Git:

```powershell
git rm --cached riego_iot.db
```

### Paso 14.3 — Agregar los Nuevos Archivos

```powershell
# Agregar los archivos de configuración y seguridad
git add .gitignore
git add .env.example
git add README.md
git add requirements.txt

# Agregar los módulos Python nuevos
git add config.py
git add mqtt_security.py
git add listener_iot_riego.py
git add listener_iot_riego_BACKUP.py
git add dashboard_app.py
git add telegram_bot.py
git add webhook_alerts.py
git add graficas.py

# Agregar pruebas
git add test_security_and_integration.py

# Agregar configuración ACL y config/
git add acl_config.txt
git add config/

# Agregar certificados (solo los públicos, NO las claves privadas)
git add certs/README.md
git add certs/generate_certs.py
git add certs/generate_certs.sh
git add certs/ca.crt
git add certs/server.crt
git add certs/client.crt

# Agregar el código de Wokwi
git add Codigo_Wokwi-proyecto.txt

# Agregar documentación
git add docs/
git add INFORME_TECNICO_FINAL.md
git add INSTRUCCIONES_DESPLIEGUE.md
git add INSTRUCCIONES_GIT.md
git add Instrucciones-proyecto.md

# Agregar registros (resultados de pruebas y gráficas)
git add registros/resultado_pruebas.json
git add registros/reporte_telemetria.png
```

### Paso 14.4 — Verificar Qué Se Va a Commitear

```powershell
git status
```

**Deberías ver en "Changes to be committed":**
```
modified:   listener_iot_riego.py
modified:   requirements.txt
deleted:    riego_iot.db       ← ¡Bien! Se remueve del repositorio
new file:   .env.example
new file:   .gitignore
new file:   README.md
new file:   Codigo_Wokwi-proyecto.txt
new file:   config.py
new file:   mqtt_security.py
new file:   dashboard_app.py
new file:   telegram_bot.py
new file:   webhook_alerts.py
...
```

**Y en "Untracked files" (NO staged):**
```
  .env                    ← ✅ CORRECTO: no se subirá
  certs/ca.key            ← ✅ CORRECTO: no se subirá
  certs/server.key        ← ✅ CORRECTO: no se subirá
  certs/client.key        ← ✅ CORRECTO: no se subirá
  riego_iot.db            ← ✅ CORRECTO: no se subirá
  venv/                   ← ✅ CORRECTO: no se subirá
  logs/                   ← ✅ CORRECTO: no se subirá
```

### Paso 14.5 — Hacer el Commit

```powershell
git commit -m "feat(security): Implementacion completa de seguridad IoT

- Modulo mqtt_security.py: validacion, TLS, ACL, identidad de dispositivo
- Modulo config.py: gestion segura de credenciales via python-dotenv
- Certificados TLS: CA, servidor y cliente (claves privadas excluidas de Git)
- dashboard_app.py: Dashboard web Flask+SocketIO con API REST y Chart.js
- telegram_bot.py: Bot avanzado con menu inline, confirmacion doble, multi-usuario
- webhook_alerts.py: Sistema HMAC-SHA256, deduplicacion, reintento exponencial
- listener_iot_riego.py: Mejorado con alarmas, operaciones, reconexion MQTT
- test_security_and_integration.py: 13 pruebas de seguridad (100% exitosas)
- README.md: Documentacion completa con diagrama Mermaid y quickstart
- docs/: Informe tecnico, guia de despliegue, guia Git, guia estudiante
- .gitignore: Proteccion de .env, *.key, *.db, logs/, venv/
- .env.example: Plantilla publica de configuracion sin credenciales reales"
```

**Salida esperada:**
```
[main abc1234] feat(security): Implementacion completa de seguridad IoT
 32 files changed, 2847 insertions(+), 45 deletions(-)
```

### Paso 14.6 — Subir al Repositorio Remoto

```powershell
git push origin main
```

**Se te pedirá autenticación de GitHub.** Tienes dos opciones:

**Opción A — Token Personal (recomendado):**
1. Ve a GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Genera un token con permisos `repo`
3. Cuando Git pida contraseña, usa el token (no tu contraseña de GitHub)

**Opción B — GitHub CLI:**
```powershell
# Si tienes gh instalado
gh auth login
git push origin main
```

**Salida esperada:**
```
Enumerating objects: 45, done.
Counting objects: 100% (45/45), done.
Delta compression using up to 8 threads
Compressing objects: 100% (38/38), done.
Writing objects: 100% (43/43), 48.52 KiB | 2.86 MiB/s, done.
Total 43 (delta 8), reused 0 (delta 0), pack-reused 0
remote: Resolving deltas: 100% (8/8), done.
To https://github.com/juan-marin192/sistema_riego_iot.git
   a9d1aee..abc1234  main -> main
```

### Paso 14.7 — Verificar en GitHub

1. Abre el navegador y ve a: `https://github.com/juan-marin192/sistema_riego_iot`
2. Verifica que aparezcan los nuevos archivos
3. **CONFIRMA que NO existe** el archivo `.env` ni ningún `*.key` en el repositorio
4. Haz clic en el `README.md` para ver la documentación renderizada

---

## 15. Verificación Final

### Checklist Completo ✅

#### Simulación Wokwi
- [ ] El ESP32 se conecta a WiFi `Wokwi-GUEST`
- [ ] El ESP32 se conecta al broker `broker.emqx.io`
- [ ] La LCD muestra VWC y estado del sistema
- [ ] Al girar el potenciómetro, el riego se activa automáticamente
- [ ] El riego se detiene a los 10 segundos (timeout de seguridad)
- [ ] La LCD muestra `ALERTA: TIMEOUT!` tras el timeout

#### Backend Python
- [ ] `listener_iot_riego.py` recibe telemetría cada 5 segundos
- [ ] Los datos se guardan en `riego_iot.db`
- [ ] Los logs se escriben en `logs/backend.log`
- [ ] Las alertas se envían correctamente por Telegram

#### Dashboard Web
- [ ] `http://localhost:5000` carga correctamente
- [ ] Las tarjetas muestran datos reales
- [ ] La gráfica se actualiza en tiempo real
- [ ] Los botones ON/OFF controlan el relay en Wokwi

#### Bot de Telegram
- [ ] El bot responde a `/start`
- [ ] `/estado` muestra datos actuales
- [ ] `/riego_on` pide confirmación y activa la bomba
- [ ] `/riego_off` apaga la bomba
- [ ] Se reciben notificaciones automáticas cuando el riego se activa

#### Pruebas de Seguridad
- [ ] `python test_security_and_integration.py` → 13/13 pruebas exitosas
- [ ] Los certificados TLS están generados en `certs/`

#### Git
- [ ] `.env` NO está en el repositorio
- [ ] `*.key` NO están en el repositorio
- [ ] `riego_iot.db` NO está en el repositorio
- [ ] El commit tiene todos los archivos nuevos
- [ ] `git push` completó sin errores
- [ ] Los archivos son visibles en `github.com/juan-marin192/sistema_riego_iot`

---

## 🛠️ Solución de Problemas Comunes

### Error: "No module named 'dotenv'"
```powershell
# Verifica que el venv esté activado
.\venv\Scripts\Activate.ps1
pip install python-dotenv
```

### Error: "HTTP 409 Conflict: terminated by other getUpdates request"
- Este error ocurre cuando dos scripts intentan escuchar comandos de Telegram simultáneamente con el mismo token.
- La solución es la arquitectura modular:
  - `listener_iot_riego.py` procesa la telemetría MQTT y envía notificaciones automáticas (con `ENABLE_TELEGRAM_POLLING=false`).
  - `telegram_bot.py` es el único encargado del polling de comandos interactivos (`/start`, `/riego_on`, botones).
- Si ejecutas ambos scripts al mismo tiempo, asegúrate de tener `ENABLE_TELEGRAM_POLLING=false` en tu archivo `.env`.

### Error: "Connection refused" al conectar MQTT
- Verifica tu conexión a internet
- El broker `broker.emqx.io` es público y gratuito, pero requiere internet
- En Wokwi, usa `Wokwi-GUEST` como SSID (no cambies esto)

### Error: "Unauthorized" en Telegram
- Verifica que `TELEGRAM_CHAT_IDS_PERMITIDOS` en `.env` tenga tu Chat ID correcto
- Obtén tu Chat ID enviando un mensaje a `@userinfobot` en Telegram

### El Dashboard muestra datos vacíos
- Verifica que `listener_iot_riego.py` esté corriendo (Terminal 1)
- Verifica que Wokwi esté ejecutándose y conectado al broker
- Recarga la página del dashboard

### Error en git push: "Authentication failed"
```powershell
# Configurar credenciales
git config --global user.email "tu_email@ejemplo.com"
git config --global user.name "juan-marin192"

# Usar token personal de GitHub como contraseña cuando se pida
```

### La bomba no se activa desde Telegram
- Verifica que `listener_iot_riego.py` esté corriendo
- Verifica que el backend esté suscrito al topic `hogar/planta01/control`
- Verifica que Wokwi esté escuchando el mismo topic

---

## 📊 Resumen del Flujo de Datos

```
[Wokwi / ESP32]
      │
      │ PUBLICA JSON cada 5s
      │ Topic: hogar/planta01/telemetria
      ▼
[broker.emqx.io] ◄──────────────────────────────┐
      │                                          │
      │ RECIBE y procesa                         │ PUBLICA "ON"/"OFF"
      ▼                                          │ Topic: hogar/planta01/control
[listener_iot_riego.py]                          │
      │                                          │
      ├──► SQLite (riego_iot.db)                 │
      │                                          │
      ├──► Telegram Bot ──────► 📱 Tu celular ──►┘
      │         ▲
      │         └── /riego_on → confirmar → PUBLICA comando
      │
      └──► SocketIO ──► Dashboard Web (localhost:5000)
                                │
                                └──► 🌐 Navegador (gráficas, control)
```

---

*Guía generada para el proyecto `sistema_riego_iot` | Juan Marín — `juan-marin192` | Noviembre 2026*
