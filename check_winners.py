import sys
sys.path.insert(0, '/var/www/bingopro')
from app import enviar_whatsapp, TWILIO_WA_TEMPLATE_SID
phone = '+15142924169'
ok, err = enviar_whatsapp(phone, '', content_sid=TWILIO_WA_TEMPLATE_SID, content_variables={'1': 'Jorge', '2': '2026-04-20 21:00', '3': 'https://ganaperu.com/cartillas'})
print('OK - message sent!' if ok else f'ERROR: {err}')
print(f'Template SID used: {TWILIO_WA_TEMPLATE_SID}')
