# GUÍA DE CONTROL DE VERSIONES Y CARGUE EN REPOSITORIO GIT

**Proyecto:** Sistema IoT de Riego Automático Inteligente  
**Repositorio Remoto:** `https://github.com/juan-marin192/sistema_riego_iot.git`  
**Rama Principal:** `main`  
**Fecha:** Noviembre 2026  

---

## 1. Principios de Seguridad y Control de Versiones

En proyectos de Internet de las Cosas (IoT) y sistemas embebidos conectados, la seguridad del repositorio es crítica. Un repositorio público o compartido que contenga tokens o claves privadas expone de inmediato los actuadores físicos a ataques maliciosos remotos.

### Matriz de Exclusión Obligatoria (Git Hygiene):

| Tipo de Archivo | Ejemplos | ¿Se sube a Git? | Razón Técnica |
|---|---|:---:|---|
| **Credenciales y Secretos** | `.env`, `.env.local` | ❌ **NUNCA** | Contiene tokens reales de Telegram y credenciales del broker. |
| **Claves Privadas TLS** | `certs/*.key`, `ca.key`, `server.key` | ❌ **NUNCA** | Rompe la confidencialidad de la infraestructura PKI. |
| **Plantilla de Configuración** | `.env.example` | ✅ **SÍ** | Sirve de modelo a otros desarrolladores sin exponer secretos. |
| **Certificados Públicos** | `certs/*.crt`, `ca.crt`, `server.crt` | ✅ **SÍ** | Son públicos por diseño; permiten a los clientes validar la CA. |
| **Bases de Datos Locales** | `riego_iot.db`, `*.db-wal` | ❌ **NO** | Contiene datos dinámicos locales y bloqueos de escritura SQLite. |
| **Archivos de Registro** | `logs/*.log`, `riego_iot.log` | ❌ **NO** | Datos volátiles de depuración. |
| **Entornos Virtuales** | `venv/`, `.venv/` | ❌ **NO** | Pesado y dependiente del sistema operativo local. |
| **Código Fuente** | `*.py`, `acl_config.txt`, `requirements.txt` | ✅ **SÍ** | Es la lógica del sistema. |
| **Documentación e Informes** | `*.md`, `docs/`, `registros/*.png` | ✅ **SÍ** | Manuales, reportes y evidencias de pruebas. |

---

## 2. Anatomía de `.gitignore`

El archivo [.gitignore](file:///c:/Users/USUARIO/Downloads/Proyecto-Wowki/.gitignore) en la raíz del proyecto asegura de forma automática que los archivos sensibles no sean rastreados por Git:

```gitignore
# Credenciales y variables de entorno REALES (NUNCA subir a Git)
.env
*.env.local
*.env.production

# Claves privadas TLS
certs/*.key
certs/ca.srl

# Base de datos SQLite
*.db
*.db-journal
*.db-wal
*.db-shm

# Logs
*.log
logs/

# Python
__pycache__/
*.py[cod]

# Entorno Virtual
venv/
.venv/
```

---

## 3. Paso a Paso para el Cargue Limpio y Seguro

Abre una terminal de PowerShell o Git Bash en el directorio raíz del proyecto (`c:\Users\USUARIO\Downloads\Proyecto-Wowki`):

---

### PASO 1: Comprobar el Estado Actual del Repositorio
```powershell
git status
```
Este comando te mostrará los archivos modificados y los archivos no rastreados (*Untracked files*).

---

### PASO 2: Des-rastrear la Base de Datos SQLite (Importante)
En el commit inicial `a9d1aee`, la base de datos `riego_iot.db` fue rastreada por Git. Como ahora está en `.gitignore`, debemos indicarle a Git que deje de seguirla, **sin borrarla de tu disco duro**:

```powershell
git rm --cached riego_iot.db
```
*(Verás que Git marca `rm 'riego_iot.db'` en verde para el commit, pero tu archivo físico en disco sigue intacto).*

---

### PASO 3: Añadir los Archivos del Proyecto por Grupos

#### 3.1: Reglas de Ignorado y Plantillas de Entorno
```powershell
git add .gitignore .env.example
```

#### 3.2: Módulos del Backend y de Seguridad
```powershell
git add config.py mqtt_security.py requirements.txt
git add listener_iot_riego.py listener_iot_riego_BACKUP.py
```

#### 3.3: Configuración del Broker y Reglas ACL
```powershell
git add acl_config.txt config/acl_config.txt
```

#### 3.4: Certificados Públicos y Scripts Generadores
```powershell
git add certs/README.md certs/generate_certs.py certs/generate_certs.sh
git add certs/ca.crt certs/server.crt certs/client.crt
```
*(Nota cómo solo agregamos los `.crt` y `.py/.sh`; las claves `*.key` son ignoradas por `.gitignore`).*

#### 3.5: Servicios de Dashboard, Telegram y Webhooks
```powershell
git add dashboard_app.py telegram_bot.py webhook_alerts.py graficas.py
```

#### 3.6: Banco de Pruebas Automatizadas y Evidencias
```powershell
git add test_security_and_integration.py
git add registros/resultado_pruebas.json registros/reporte_telemetria.png
```

#### 3.7: Documentación Completa y Manuales
```powershell
git add README.md INFORME_TECNICO_FINAL.md INSTRUCCIONES_DESPLIEGUE.md INSTRUCCIONES_GIT.md Instrucciones-proyecto.md
git add docs/
```

---

### PASO 4: Auditoría de Seguridad Pre-Commit (¡Crucial!)
Antes de confirmar el commit, ejecuta:

```powershell
git status
```

#### 🔍 Lista de Verificación Visual:
- [ ] ¿Aparece `.env` en verde? ➡️ **¡NO DEBE APARECER!** (Si aparece, ejecuta `git reset .env`).
- [ ] ¿Aparece `certs/ca.key`, `server.key` o `client.key`? ➡️ **¡NO DEBEN APARECER!** (Si aparecen, ejecuta `git reset certs/*.key`).
- [ ] ¿Aparecen los scripts `.py`, los `.md` y las configuraciones? ➡️ **SÍ, deben estar en verde.**

---

### PASO 5: Crear el Commit Semántico
Crea el commit con un mensaje descriptivo siguiendo el estándar de *Conventional Commits*:

```powershell
git commit -m "feat(security): implementacion de arquitectura de seguridad IoT, MQTT TLS, ACL, dashboard en tiempo real, webhooks y bot telegram"
```

---

### PASO 6: Subir los Cambios al Repositorio Remoto en GitHub

```powershell
git push origin main
```

Si es la primera vez o tu repositorio solicita credenciales:
* Ingresa tu usuario de GitHub.
* Utiliza tu **Personal Access Token (PAT)** clásico o con permisos de `repo` como contraseña.

---

## 4. Verificación Post-Cargue en GitHub

1. Abre tu navegador y ve a:  
   👉 `https://github.com/juan-marin192/sistema_riego_iot`
2. **Comprueba el árbol de archivos:**
   - Debe visualizarse [.env.example](file:///c:/Users/USUARIO/Downloads/Proyecto-Wowki/.env.example), pero **NO** `.env`.
   - Dentro de la carpeta `certs/`, deben visualizarse `ca.crt`, `server.crt`, `client.crt` y los scripts generadores, pero **NINGUNA clave `.key`**.
   - El archivo `README.md` se renderizará automáticamente en la página principal con el mapa del proyecto y enlaces a la documentación.

---

## 5. Comandos Útiles para el Trabajo Diario con Git

### Ver el historial de commits recientes:
```powershell
git log --oneline -n 5 --graph
```

### Crear una rama para una nueva funcionalidad:
```powershell
git checkout -b feature/nuevo-sensor-suelo
```

### Descartar cambios locales no deseados en un archivo:
```powershell
git restore nombre_archivo.py
```

### Traer los cambios más recientes del repositorio remoto:
```powershell
git pull origin main
```
