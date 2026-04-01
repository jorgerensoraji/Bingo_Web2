# 🎱 BINGO PRO WEB v6.0

**Made by Renso Ramirez  |  Enhanced v6.0**

---

## 🆕 Novedades en v6.0

### 🔐 Seguridad
| # | Mejora |
|---|--------|
| 1 | Credenciales **solo por variables de entorno** — nunca hardcodeadas en el código |
| 2 | **Rate limiting** en endpoints públicos — anti-bot y anti-abuso |
| 3 | **Anti-brute force** en login — delay + límite de 10 intentos por minuto |
| 4 | **Anti-duplicado de comprobante** — un número de operación Yape/Plin no puede usarse dos veces |
| 5 | **Límite de cartillas por voucher** — configurable por tipo de bingo |

### 💰 Negocio
| # | Mejora |
|---|--------|
| 6 | **Panel de Caja** (`/admin/caja`) — ingresos, premios, ganancia y margen por sesión |
| 7 | **Exportar CSV** desde la Caja para llevar registros en Excel |
| 8 | **Premio de LÍNEA** con su propio porcentaje del pozo |
| 9 | **Vista previa del pozo** al crear una sesión — ves cuánto recibirás antes de empezar |
| 10 | **Alerta de pagos pendientes** en el panel admin — nunca te olvidas de aprobar |

### 🎮 Jugabilidad
| # | Mejora |
|---|--------|
| 11 | **Botón ¡BINGO!** — el jugador reclama directamente sin depender del admin |
| 12 | **Botón ¡LÍNEA!** — el jugador reclama su premio de línea |
| 13 | **Alerta "¡Falta 1!"** — aparece cuando falta un número, con animación y voz |
| 14 | **Polling automático de pago** — la UI del jugador se actualiza sola cada 5s |
| 15 | **Pozo visible en vivo** — los jugadores ven el premio durante el juego |
| 16 | **Historial de ganadores** en `/cartillas` — genera confianza |

### 🛠️ Técnico
| # | Mejora |
|---|--------|
| 17 | **Estado del juego persistente** — si el servidor reinicia, las bolillas se recuperan |
| 18 | Detección visual de **referencias duplicadas** en `/admin/payments` |
| 19 | Badge de pendientes en el menú de navegación del admin |
| 20 | Botón **📲 WA** en sesiones para compartir info directamente |

---

## 📁 Estructura del proyecto

```
bingo_pro/
├── app.py                          ← Backend principal v6.0
├── requirements.txt                ← Dependencias Python
├── .env.example                    ← Plantilla de variables de entorno
├── .env                            ← TU archivo de credenciales (no subir a git)
│
├── cartillas_data/                 ← Datos persistentes (se crea automáticamente)
│   ├── _vouchers.json              ← Códigos + estado de pagos
│   ├── _sessions.json              ← Sesiones programadas
│   ├── _payments.json              ← Log de pagos
│   ├── _config.json                ← Configuración (métodos de pago, etc.)
│   ├── _game_state.json            ← ✨ NUEVO: estado persistente del juego
│   └── *.json                      ← Una cartilla por archivo
│
├── templates/
│   ├── index.html                  ← Vista jugadores — juego en vivo (sin cambios)
│   ├── admin_login.html            ← Login admin (sin cambios)
│   ├── admin.html                  ← ✨ Panel principal — alerta pagos pendientes
│   ├── admin_game.html             ← Control del sorteo (sin cambios)
│   ├── admin_sessions.html         ← ✨ Sesiones — vista previa del pozo
│   ├── admin_payments.html         ← ✨ Pagos — detección de duplicados
│   ├── admin_caja.html             ← ✨ NUEVO: reporte financiero completo
│   ├── admin_config.html           ← Configuración (sin cambios)
│   ├── cartillas_admin.html        ← Cartillas admin (sin cambios)
│   └── cartillas_player.html       ← ✨ Cartillas jugador — polling de pago
│
└── static/
    ├── css/
    │   ├── game.css                ← Sin cambios
    │   └── cartillas.css           ← Sin cambios
    └── js/
        ├── game.js                 ← Sin cambios
        ├── game_player.js          ← ✨ Botón BINGO + Falta 1 + Línea
        ├── cartillas_admin.js      ← Sin cambios
        └── cartillas_player.js     ← Sin cambios (lógica ahora inline en el HTML)
```

---

## 🚀 Instalación desde cero

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. Configurar credenciales (MUY IMPORTANTE)
cp .env.example .env

# 3. Editar .env con tus datos reales
nano .env       # o usa cualquier editor de texto

# 4. Arrancar
python app.py
```

---

## 🔐 Configurar credenciales (obligatorio antes de usar)

Edita el archivo `.env` que creaste:

```env
SECRET_KEY=pega-aqui-tu-clave-generada
ADMIN_USER=el_usuario_que_quieras
ADMIN_PASS=UnaContraseñaFuerte123!
```

Para generar una `SECRET_KEY` segura:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 🗺️ Rutas del sistema

| URL | Quién accede | Descripción |
|-----|-------------|-------------|
| `http://IP:5000/` | Jugadores | Juego en vivo — bolillas en tiempo real |
| `http://IP:5000/cartillas` | Jugadores | Comprar entrada, ver estado del pago, generar cartilla |
| `http://IP:5000/admin` | Admin | Panel principal — generar códigos |
| `http://IP:5000/admin/game` | Admin | Control del sorteo |
| `http://IP:5000/admin/sessions` | Admin | Programar sesiones |
| `http://IP:5000/admin/payments` | Admin | Revisar y aprobar pagos |
| `http://IP:5000/admin/caja` | Admin | **NUEVO** Reporte financiero completo |
| `http://IP:5000/admin/config` | Admin | Configurar métodos de pago y contacto |

---

## 🔄 Flujo completo v6.0

```
ADMIN                                      JUGADOR
  │                                            │
  ├─ /admin/sessions                           │
  │   └─ Crea sesión con fecha y tipo          │
  │   └─ Ve PREVIEW del pozo antes de crear    │
  │                                       /cartillas
  │                                            └─ Ve próximas sesiones
  │                                            └─ Ve últimos ganadores
  │                                            └─ Ingresa su código
  │                                            └─ Envía comprobante Yape/Plin
  │                                            └─ [NUEVO] UI se actualiza sola
  │                                               cada 5 segundos
  │
  ├─ /admin/payments
  │   └─ Ve badge "2 pendientes" en el menú
  │   └─ Ve referencias duplicadas destacadas
  │   └─ Aprueba pago
  │                                            └─ UI muestra ✅ automáticamente
  │                                            └─ Genera su cartilla (hasta N)
  │                                            └─ Descarga PNG o PDF
  │
  ├─ /admin/sessions → ▶ Iniciar
  ├─ /admin/game → Sortear bolillas
  │                                       / (index)
  │                                            └─ Ve pozo en tiempo real
  │                                            └─ Cartilla se marca sola
  │                                            └─ [NUEVO] "¡Falta 1!" con voz
  │                                            └─ [NUEVO] Botón ¡LÍNEA! aparece
  │                                            └─ [NUEVO] Botón ¡BINGO! aparece
  │                                            └─ Reclama sin depender del admin
  │
  ├─ /admin/caja ← NUEVO
  │   └─ Total recaudado: S/. 150.00
  │   └─ Premios pagados: S/. 112.50
  │   └─ Ganancia neta:   S/.  37.50
  │   └─ Exportar CSV para Excel
```

---

## 💰 Tipos de bingo y distribución del pozo

| Tipo | Precio | Max cartillas/jugador | Premio BINGO | Premio LÍNEA | Casa |
|------|--------|-----------------------|-------------|-------------|------|
| 🟡 1 Sol    | S/. 1.00  | 3 cartillas | 70% | 10% | 20% |
| 🔵 5 Soles  | S/. 5.00  | 5 cartillas | 75% | 8%  | 17% |
| 💎 10 Soles | S/. 10.00 | 5 cartillas | 80% | 5%  | 15% |

### Ejemplo con 20 jugadores en Bingo 5 Soles:
```
Total recaudado:   S/. 100.00  (20 × S/.5)
Premio BINGO:      S/.  75.00  (75%)
Premio LÍNEA:      S/.   8.00  (8%)
Ganancia casa:     S/.  17.00  (17%)
```

---

## 🖥️ Producción (servidor público)

Si vas a publicar el servidor para que se acceda desde internet:

**1. Usa gunicorn (no el servidor de desarrollo de Flask):**
```bash
gunicorn -w 1 -b 0.0.0.0:5000 app:app
```
> ⚠️ Usa solo 1 worker porque el estado del juego vive en memoria.
> Si necesitas más workers, migra el estado a Redis o SQLite.

**2. Activa la cookie segura en `app.py`** (descomenta esta línea):
```python
app.config["SESSION_COOKIE_SECURE"] = True
```

**3. Pon nginx como proxy reverso** con un certificado SSL gratuito de Let's Encrypt.

**4. Agrega `.env` a tu `.gitignore`:**
```bash
echo ".env" >> .gitignore
```

---

## 🐛 Solución de problemas comunes

| Problema | Solución |
|----------|----------|
| `ADMIN_USER y ADMIN_PASS no configurados` | Crea el archivo `.env` y configura ambas variables |
| El servidor reinicia y pierde el juego | Normal en v5. En v6 el estado se recupera de `_game_state.json` |
| Jugador no puede generar cartilla | Verificar que el pago esté aprobado en `/admin/payments` |
| El código Yape es rechazado como duplicado | El jugador ya registró ese número antes. Verificar en `/admin/payments` |
| Audio no suena en el jugador | El jugador debe hacer click en "Activar sonido" antes de que empiece el juego |
| `rate_limited` en la API | Demasiadas peticiones desde la misma IP. Esperar el tiempo indicado |
