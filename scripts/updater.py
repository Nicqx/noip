#!/usr/bin/env python3
"""Small No-IP client. Credentials are data, never shell code."""
from __future__ import annotations
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import time
import urllib.error
import urllib.parse
import urllib.request

UPDATE_URL = 'https://dynupdate.no-ip.com/nic/update'
IP_URL = 'https://api.ipify.org'
PERMANENT = {'badauth', 'nohost', 'badagent', '!donator', 'abuse', 'notfqdn', 'numhost'}


class ClientError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def assignments(text):
    result = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = re.fullmatch(r'\s*([A-Z_]+)\s*=\s*(.*?)\s*', line)
        if not match:
            raise ClientError('Invalid configuration syntax')
        value = shlex.split(match[2], comments=True, posix=True)
        if len(value) != 1 or match[1] in result:
            raise ClientError('Invalid or duplicate configuration value')
        result[match[1]] = value[0]
    return result


def credentials(text):
    if text.lstrip().startswith('{'):
        data = json.loads(text)
        user, password = data.get('username'), data.get('password')
    else:
        data = assignments(text)
        user, password = data.get('DDNS_USERNAME'), data.get('DDNS_PASSWORD')
    if not all(isinstance(v, str) and v and '\n' not in v and '\r' not in v for v in [user, password]):
        raise ClientError('Missing credentials')
    if ':' in user:
        raise ClientError('Invalid username')
    return user, password


def validate_config(data):
    host = data.get('hostname', '')
    if not isinstance(host, str) or len(host) > 253 or not re.fullmatch(
            r'(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}', host):
        raise ClientError('Invalid hostname')
    interval = data.get('interval_seconds', 300)
    if type(interval) is not int or not 300 <= interval <= 86400:
        raise ClientError('Interval must be 300..86400 seconds')
    return {'hostname': host.lower(), 'interval_seconds': interval}


def request(url, auth=None):
    headers = {'User-Agent': 'Nicqx-NoIP/2.0 (github.com/Nicqx/noip)'}
    if auth:
        headers['Authorization'] = 'Basic ' + base64.b64encode((':'.join(auth)).encode()).decode()
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=20) as response:
            return response.status, response.read(1025).decode('ascii').strip()
    except urllib.error.HTTPError as exc:
        return exc.code, ''
    except (OSError, urllib.error.URLError, UnicodeError):
        return 0, ''


def step(config, auth, state, now, http=request):
    """One iteration; all returned log messages exclude credentials/provider text."""
    fingerprint = hashlib.sha256(json.dumps([config, auth], sort_keys=True).encode()).hexdigest()
    if state.get('fingerprint') != fingerprint:
        state = {'fingerprint': fingerprint}
    if state.get('blocked'):
        return state, 'No-IP paused: correct configuration/credentials before retrying.'
    if now < state.get('retry_at', 0):
        return state, 'Waiting for provider retry interval.'
    status, value = http(IP_URL)
    try:
        address = ipaddress.IPv4Address(value)
        if status != 200 or not address.is_global:
            raise ValueError()
    except (ValueError, ipaddress.AddressValueError):
        return state, 'Public IPv4 lookup failed.'
    current = str(address)
    if state.get('last_ip') == current:
        return state, 'Public IPv4 unchanged.'
    query = urllib.parse.urlencode({'hostname': config['hostname'], 'myip': current})
    status, body = http(UPDATE_URL + '?' + query, auth)
    fields = body.split()
    code = fields[0] if fields else ''
    if status == 200 and code in {'good', 'nochg'} and len(fields) == 2 and fields[1] == current:
        state.update(last_ip=current, retry_at=0)
        return state, 'No-IP update accepted.'
    if status in {401, 403} or (status == 200 and code in PERMANENT):
        state['blocked'] = True
        return state, 'No-IP paused: provider rejected credentials or hostname.'
    # 911/5xx require at least 30 minutes. Also back off on all unknown failures.
    state['retry_at'] = now + 1800
    return state, 'No-IP update failed; retry in at least 30 minutes.'


def main():
    path = Path('/state/status.json')
    state = {}
    if path.exists():
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            state = {}
    while True:
        interval = 300
        try:
            config = validate_config(json.loads(Path('/opt/ddns/config.json').read_text()))
            secret = Path('/opt/secret/credentials.json')
            if not secret.exists():
                secret = Path('/opt/secret/credentials.conf')
            auth = credentials(secret.read_text())
            interval = config['interval_seconds']
            state, message = step(config, auth, state, time.time())
            tmp = path.with_suffix('.tmp')
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as handle:
                json.dump(state, handle)
            os.replace(tmp, path)
        except (OSError, ValueError, ClientError, TypeError):
            message = 'Configuration unreadable or invalid; no update sent.'
        print(message, flush=True)
        time.sleep(interval)


if __name__ == '__main__':
    main()
