# Guía de Deployment — Bingo Gana Peru

## Información del servidor
- **Plataforma:** DigitalOcean
- **IP del servidor:** 209.38.76.32
- **Ruta del proyecto:** `/var/www/bingopro/`
- **Usuario SSH:** root

---

## 1. Cómo conectarse al servidor

Abre una terminal y ejecuta:

```bash
ssh root@209.38.76.32
```

Ingresa tu contraseña cuando la pida. La encuentras en:
- El email que te envió DigitalOcean cuando creaste el droplet
- O en DigitalOcean Dashboard → Droplets → Reset Root Password

---

## 2. Cómo actualizar el código (cambios en app.py u otros archivos)

### En tu computadora local:

```bash
git add app.py
git commit -m "Descripción del cambio"
git push origin master
```

### En el servidor (después del push):

```bash
ssh root@209.38.76.32
cd /var/www/bingopro
git pull origin master
sudo systemctl restart bingopro
```

---

## 3. Cómo actualizar variables de entorno (.env)

El archivo `.env` **nunca se sube a git** (por seguridad). Debes editarlo manualmente en el servidor.

```bash
ssh root@209.38.76.32
nano /var/www/bingopro/.env
```

- Usa las flechas del teclado para moverte
- Edita el valor que necesites
- `Ctrl+X` → `Y` → `Enter` para guardar

Luego reinicia la app:

```bash
sudo systemctl restart bingopro
```

---

## 4. Variables de entorno importantes (.env)

```
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_WHATSAPP_FROM=whatsapp:+15559237702
TWILIO_DEFAULT_COUNTRY=+51
```

Para obtener nuevas credenciales de Twilio:
- Entra a twilio.com → Console → Account → API Keys → Create new key

---

## 5. Ver logs del servidor (para diagnosticar errores)

```bash
sudo journalctl -u bingopro -n 100 --no-pager
```

O en tiempo real:

```bash
sudo journalctl -u bingopro -f
```

---

## 6. Reiniciar / detener / ver estado de la app

```bash
sudo systemctl restart bingopro   # reiniciar
sudo systemctl stop bingopro      # detener
sudo systemctl start bingopro     # iniciar
sudo systemctl status bingopro    # ver estado
```

---

## 7. Dónde encontrar la IP del servidor

1. Entra a digitalocean.com
2. Inicia sesión
3. Click en **Droplets** en el menú izquierdo
4. Tu IP aparece debajo del nombre del droplet (ej. `209.38.76.32`)

---

## Proceso completo de deploy (resumen rápido)

```bash
# 1. En tu PC — guardar y subir cambios
git add app.py
git commit -m "descripción"
git push origin master

# 2. En el servidor — bajar cambios y reiniciar
ssh root@209.38.76.32
cd /var/www/bingopro && git pull origin master && sudo systemctl restart bingopro
```
