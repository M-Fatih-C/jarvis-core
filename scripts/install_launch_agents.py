#!/usr/bin/env python3
"""Install per-user launchd jobs for the existing native agent and Core runtime.

The Gmail scheduler and provisioning renewal remain governed by their existing
explicit opt-in settings. Run this script again after moving the checkout.
"""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_job(domain: str, label: str, path: Path, job: dict) -> None:
    subprocess.run(['launchctl', 'bootout', f'{domain}/{label}'], capture_output=True)
    path.write_bytes(plistlib.dumps(job))
    path.chmod(0o600)
    # bootout may return before the previous process has released the service.
    for attempt in range(10):
        result = subprocess.run(['launchctl', 'bootstrap', domain, str(path)], capture_output=True)
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError(f'Unable to load {label}: {result.stderr.decode().strip()}')


def definitions() -> dict[str, dict]:
    logs = Path.home() / 'Library/Logs/Jarvis'
    logs.mkdir(parents=True, exist_ok=True)
    common = {'WorkingDirectory': str(ROOT), 'RunAtLoad': True, 'ThrottleInterval': 15,
              'KeepAlive': True, 'ProcessType': 'Background', 'Umask': 0o077}
    mac = ROOT / 'macos/JarvisMacAgent/JarvisMacAgent.app/Contents/MacOS/JarvisMacAgent'
    if not mac.exists():
        raise SystemExit('Build JarvisMacAgent.app before installing LaunchAgents')
    commands = {
        'com.jarvis.mac-agent': [str(mac)],
        'com.jarvis.core': [str(ROOT / '.venv/bin/python'), '-m', 'uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', '8765'],
    }
    jobs = {}
    for label, command in commands.items():
        job = dict(common, Label=label, ProgramArguments=command,
                   StandardOutPath=str(logs / f'{label}.log'), StandardErrorPath=str(logs / f'{label}.error.log'))
        if label == 'com.jarvis.core':
            job['EnvironmentVariables'] = {'ENVIRONMENT': 'production', 'APPLE_INTEGRATION_PROVIDER': 'native_macos',
                'EMBEDDING_PROVIDER': 'sentence_transformers', 'PYTHONUNBUFFERED': '1',
                'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'}
        jobs[label] = job
    return jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='Write and load per-user LaunchAgents')
    args = parser.parse_args()
    jobs = definitions()
    if not args.install:
        print('\n'.join(jobs)); return
    dest = Path.home() / 'Library/LaunchAgents'
    dest.mkdir(parents=True, exist_ok=True)
    domain = f'gui/{os.getuid()}'
    for label, job in jobs.items():
        path = dest / f'{label}.plist'
        load_job(domain, label, path, job)
        print(f'Installed {label}')
    # Install the renewal timer only after the user explicitly enabled it.
    from core.build_manager.manager import BuildManager
    if BuildManager().state.auto_renew_enabled:
        label = 'com.jarvis.ios-build-manager'
        job = dict(Label=label, WorkingDirectory=str(ROOT), StartInterval=900, RunAtLoad=True,
                   ProgramArguments=[str(ROOT / '.venv/bin/python'), str(ROOT / 'scripts/ios_build_manager_daemon.py')],
                   StandardOutPath=str(Path.home() / 'Library/Logs/Jarvis/renewal.log'),
                   StandardErrorPath=str(Path.home() / 'Library/Logs/Jarvis/renewal.error.log'),
                   Umask=0o077,
                   EnvironmentVariables={'PATH':'/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'})
        path = dest / f'{label}.plist'
        load_job(domain, label, path, job)
        print(f'Installed {label}')

if __name__ == '__main__':
    main()
