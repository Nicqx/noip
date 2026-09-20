#!/usr/bin/env python3
import base64
import json
from common import restore_snapshot
from pathlib import Path
from common import (ROOT, Failure, Kube, canonical, checksum, diagnose, entrypoint,
                    label, operation_lock, parser, rollout, snapshot)
from updater import ClientError, assignments, credentials, validate_config

ALLOWED = {('ConfigMap', 'noip-ddns-config'), ('Deployment', 'noip-ddns-updater')}
SECRET = 'noip-ddns-secret'


def configuration(kube=None):
    path = ROOT / 'config.local.json'
    if path.exists():
        return validate_config(json.loads(path.read_text()))
    old = kube.get('configmap', 'noip-ddns-config') if kube else None
    if old:
        data = old.get('data', {})
        if 'config.json' in data:
            return validate_config(json.loads(data['config.json']))
        if 'ddns.conf' in data:
            values = assignments(data['ddns.conf'])
            if values.get('DDNS_UPDATE_URL', 'https://dynupdate.no-ip.com/nic/update') != 'https://dynupdate.no-ip.com/nic/update':
                raise Failure('Egyedi DDNS endpoint; elobb kezi felmeres szukseges.')
            return validate_config({'hostname': values.get('DDNS_HOSTNAME'),
                                    'interval_seconds': int(values.get('CHECK_INTERVAL_SECONDS', '300'))})
        raise Failure('Ismeretlen elo No-IP konfiguracio; nincs automatikus csere.')
    return validate_config(json.loads((ROOT / 'config.example.json').read_text()))


def render(kube=None):
    items = json.loads((ROOT / 'k8s/resources.json').read_text())
    cfg, deployment = items
    cfg['data'] = {'config.json': json.dumps(configuration(kube)),
                   'updater.py': (ROOT / 'scripts/updater.py').read_text()}
    old = kube.get('deployment', 'noip-ddns-updater') if kube else None
    # A new installation is stopped until explicitly enabled. Preserve an existing updater.
    deployment['spec']['replicas'] = old['spec'].get('replicas', 1) if old else 0
    if deployment['spec']['replicas'] not in (0, 1):
        raise Failure('Egynel tobb DDNS replika nem engedelyezett.')
    deployment['spec']['template']['metadata']['annotations'] = {'nicqx.dev/config-sha256': checksum(cfg['data'])}
    if kube and deployment['spec']['replicas']:
        check_secret(kube)
    return label(items, 'noip')


def check_secret(kube):
    obj = kube.get('secret', SECRET)
    try:
        data = obj['data']
        encoded = data.get('credentials.json') or data['credentials.conf']
        credentials(base64.b64decode(encoded, validate=True).decode())
    except (KeyError, TypeError, ValueError, ClientError):
        raise Failure('Hianyzo/ervenytelen noip-ddns-secret. Hasznald a set-secret parancsot; erzekeny adatot nem naplozunk.') from None


def main():
    p = parser('No-IP biztonsagos telepitese', ['update', 'rollback', 'render', 'diagnose', 'set-secret', 'enable', 'disable'])
    p.add_argument('--file')
    args = p.parse_args()
    if args.command == 'render':
        print(json.dumps(render(), indent=2)); return
    if args.dry_run and args.command not in {'update', 'rollback', 'set-secret'}:
        p.error('--dry-run csak update/set-secret mellett ervenyes')
    if args.command == 'set-secret' and not args.file:
        p.error('--file szukseges (helyi credentials.json vagy credentials.conf)')
    kube = Kube(args.target, args.context)
    kube.verify(require_ready=args.command != 'diagnose')
    if args.command == 'diagnose':
        diagnose(kube); return
    with operation_lock(args.target):
        if args.command == 'rollback':
            if not args.file: p.error('--file szukseges')
            restore_snapshot(kube, args.file, ALLOWED | {('Secret', SECRET)}, args.dry_run)
            return
        if args.command == 'update':
            try:
                items = render(kube)
            except (ClientError, ValueError):
                raise Failure('Ervenytelen No-IP konfiguracio; nincs modositas.') from None
            if not args.dry_run: snapshot(kube, items)
            kube.apply(items, ALLOWED, dry_run=args.dry_run)
            if not args.dry_run: rollout(kube, items)
        elif args.command == 'set-secret':
            try:
                user, password = credentials(Path(args.file).read_text())
            except (ClientError, ValueError, TypeError):
                raise Failure('Ervenytelen credential fajl; tartalmat nem naplozunk.') from None
            data = canonical({'username': user, 'password': password})
            secret = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': SECRET, 'namespace': 'default'},
                      'type': 'Opaque', 'data': {'credentials.json': base64.b64encode(data).decode()}}
            if not args.dry_run: snapshot(kube, [secret])
            kube.apply([secret], {('Secret', SECRET)}, dry_run=args.dry_run, sensitive=True)
        elif args.command in {'enable', 'disable'}:
            if args.command == 'enable': check_secret(kube)
            kube.call('scale', 'deployment/noip-ddns-updater', '-n', 'default',
                      '--replicas=' + ('1' if args.command == 'enable' else '0'))
            print('DDNS replika beallitva. Ugyanahhoz a hostname-hez csak egy aktiv frissito legyen.')


if __name__ == '__main__':
    entrypoint(main)
