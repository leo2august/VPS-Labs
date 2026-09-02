"""Labs account metadata, email login, and email password recovery."""
import hashlib, hmac, json, os, re, secrets, smtplib, ssl, time
from email.message import EmailMessage
from pathlib import Path

DATA = Path('/home/ubuntu/vps-audit/data/lab-account.json')
RESET = Path('/home/ubuntu/vps-audit/data/password-reset.json')
EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


def _read(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def summary():
    data = _read(DATA, {})
    return {'ok': True, 'username': os.environ.get('LABS_USER', ''),
            'email': data.get('email', ''), 'email_login': bool(data.get('email')),
            'recovery_ready': bool(data.get('email') and os.environ.get('LABS_SMTP_HOST'))}


def set_email(email, current_password):
    if not hmac.compare_digest(current_password, os.environ.get('LABS_PASSWORD', '')):
        return {'ok': False, 'error': 'password Labs salah'}
    email = email.strip().lower()
    if email and not EMAIL_RE.fullmatch(email):
        return {'ok': False, 'error': 'format email tidak valid'}
    _write(DATA, {'email': email, 'updated_at': int(time.time())})
    return summary()


def valid_identifier(identifier):
    username = os.environ.get('LABS_USER', '')
    email = _read(DATA, {}).get('email', '')
    return hmac.compare_digest(identifier, username) or bool(email and hmac.compare_digest(identifier.lower(), email.lower()))


def request_reset(identifier, base_url):
    # Same response for known/unknown identifiers prevents account discovery.
    if not valid_identifier(identifier) or not os.environ.get('LABS_SMTP_HOST'):
        return {'ok': True, 'message': 'Jika akun dan email pemulihan aktif, tautan reset sudah dikirim.'}
    token = secrets.token_urlsafe(32)
    _write(RESET, {'hash': hashlib.sha256(token.encode()).hexdigest(), 'expires': int(time.time()) + 900})
    recipient = _read(DATA, {}).get('email', '')
    msg = EmailMessage()
    msg['Subject'] = 'Reset password VPS Labs'
    msg['From'] = os.environ.get('LABS_SMTP_FROM', os.environ.get('LABS_SMTP_USER', ''))
    msg['To'] = recipient
    msg.set_content(f'Tautan reset berlaku 15 menit:\n{base_url.rstrip("/")}/reset/{token}\n\nAbaikan jika kamu tidak meminta reset.')
    host = os.environ['LABS_SMTP_HOST']; port = int(os.environ.get('LABS_SMTP_PORT', '465'))
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=15) as smtp:
        smtp.login(os.environ.get('LABS_SMTP_USER', ''), os.environ.get('LABS_SMTP_PASSWORD', ''))
        smtp.send_message(msg)
    return {'ok': True, 'message': 'Jika akun dan email pemulihan aktif, tautan reset sudah dikirim.'}


def consume_reset(token):
    data = _read(RESET, {})
    valid = data.get('expires', 0) >= int(time.time()) and hmac.compare_digest(
        data.get('hash', ''), hashlib.sha256(token.encode()).hexdigest())
    if valid:
        RESET.unlink(missing_ok=True)
    return valid


def change_unit_password(new_password):
    if len(new_password) < 10:
        return {'ok': False, 'error': 'password minimal 10 karakter'}
    if not re.search(r'[A-Za-z]', new_password) or not re.search(r'\d', new_password):
        return {'ok': False, 'error': 'password wajib punya huruf dan angka'}
    if re.search(r'[\s%"\\]', new_password):
        return {'ok': False, 'error': 'password tidak boleh berisi spasi, %, tanda kutip, atau backslash'}
    env_file = Path('/home/ubuntu/vps-audit/data/labs.env')
    text = env_file.read_text()
    text, count = re.subn(r'(?m)^LABS_PASSWORD=.*$', 'LABS_PASSWORD=' + new_password, text)
    if count != 1:
        return {'ok': False, 'error': 'konfigurasi password Labs tidak ditemukan'}
    tmp = env_file.with_suffix('.tmp'); tmp.write_text(text); os.chmod(tmp, 0o600); os.replace(tmp, env_file)
    return {'ok': True}


def reset_password(token, new_password):
    if len(new_password) < 10 or not re.search(r'[A-Za-z]', new_password) or not re.search(r'\d', new_password):
        return {'ok': False, 'error': 'password minimal 10 karakter, berisi huruf dan angka'}
    if re.search(r'[\s%"\\]', new_password):
        return {'ok': False, 'error': 'password tidak boleh berisi spasi, %, tanda kutip, atau backslash'}
    if not consume_reset(token):
        return {'ok': False, 'error': 'tautan reset tidak valid atau kedaluwarsa'}
    return change_unit_password(new_password)


if __name__ == '__main__':
    assert not valid_identifier('__unknown__')
    assert EMAIL_RE.fullmatch('operator@example.com')
    assert not EMAIL_RE.fullmatch('broken')
    print('lab_account self-check OK')
