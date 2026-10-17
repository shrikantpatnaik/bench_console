#!/usr/bin/env python3
"""Install Bench Console as a Raspberry Pi boot service on HTTP port 80.

Run without sudo, from the permanent project directory containing main.py:
  python3 install_boot_service.py --python "$HOME/.virtualenvs/bench_console/bin/python"
Use --project /absolute/project/path if running from another directory.
Use --dry-run to print the service without installing or starting it.
"""

import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tempfile


SERVICE = 'bench-console.service'


def quote(value, argument=False):
    """Escape systemd specifiers, argument expansion and quoted strings."""
    value = str(value).replace('%', '%%')
    if argument:
        value = value.replace('$', '$$')
    return json.dumps(value, ensure_ascii=False)


def service_text(project, interpreter, account):
    return f'''[Unit]
Description=Bench Console - OWON and Hantek instruments
After=network.target

[Service]
Type=simple
User={account.pw_name}
WorkingDirectory={str(project).replace('%', '%%')}
ExecStart={quote(interpreter)} {quote(project / 'main.py', True)}
Environment="HOST=0.0.0.0" "PORT=80" "PYTHONUNBUFFERED=1"
Environment={quote('HOME=' + account.pw_dir)}
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
Restart=on-failure
RestartSec=5
KillSignal=SIGINT
KillMode=mixed
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal
SyslogIdentifier=bench-console

[Install]
WantedBy=multi-user.target
'''


def choose_python(project, home):
    candidates = []
    if os.environ.get('VIRTUAL_ENV'):
        candidates.append(Path(os.environ['VIRTUAL_ENV']) / 'bin/python')
    candidates.extend([project / '.venv/bin/python',
                       home / '.virtualenvs/bench_console/bin/python'])
    return next((p for p in candidates if p.is_file()), None)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--project', default='.', help='Project folder containing main.py (default: current folder)')
    parser.add_argument('--python', dest='interpreter', help='Python executable in the working virtual environment')
    parser.add_argument('--dry-run', action='store_true', help='Print service configuration without making changes')
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('Run as your normal Pi user, without sudo. The installer invokes sudo only for service setup.')
    account = pwd.getpwuid(os.getuid())
    project = Path(args.project).expanduser().resolve()
    for temporary in ('/tmp', '/var/tmp', '/run', '/dev/shm'):
        if project.is_relative_to(Path(temporary)):
            parser.error('Keep the project in a permanent folder, e.g. ~/bench_console, not a PyCharm /tmp folder.')
    if not (project / 'main.py').is_file() or not (project / 'bench_console/__init__.py').is_file():
        parser.error('--project must contain main.py and the bench_console package.')
    selected = Path(args.interpreter).expanduser() if args.interpreter else choose_python(project, Path(account.pw_dir))
    if selected is None:
        parser.error('No virtual environment found. Pass --python /absolute/path/to/venv/bin/python.')
    # Do not resolve this symlink: resolving venv/bin/python selects system
    # Python and loses the virtual environment's installed dependencies.
    interpreter = Path(os.path.abspath(selected))
    for path in (project, interpreter, Path(account.pw_dir)):
        if any(ord(ch) < 32 for ch in str(path)) or str(path) != str(path).strip():
            parser.error('Project, home and Python paths must not contain control characters or leading/trailing whitespace.')
    if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
        parser.error(f'Python executable not found: {interpreter}')
    unit = service_text(project, interpreter, account)
    if args.dry_run:
        print(unit, end='')
        return
    if not Path('/run/systemd/system').is_dir():
        parser.error('Install this on the Raspberry Pi running Raspberry Pi OS/systemd.')
    for command in ('sudo', 'systemctl', 'install'):
        if not shutil.which(command):
            parser.error(f'Required command not found: {command}')
    # This import check runs as the service user, before privileged changes.
    subprocess.run([str(interpreter), '-c',
                    'import flask, flask_socketio, numpy, pyvisa, serial; '
                    'from bench_console import create_app'], cwd=project, check=True)
    with tempfile.TemporaryDirectory(prefix='bench-console-install-') as folder:
        source = Path(folder) / SERVICE
        source.write_text(unit, encoding='utf-8')
        if shutil.which('systemd-analyze'):
            subprocess.run(['systemd-analyze', 'verify', '--man=no', str(source)], check=True)
        destination = Path('/etc/systemd/system') / SERVICE
        subprocess.run(['sudo', '-v'], check=True)
        if destination.exists():
            subprocess.run(['sudo', 'cp', '-a', str(destination), str(destination) + '.bak'], check=True)
        subprocess.run(['sudo', 'install', '-o', 'root', '-g', 'root', '-m', '644',
                        str(source), str(destination)], check=True)
    subprocess.run(['sudo', 'systemctl', 'daemon-reload'], check=True)
    subprocess.run(['sudo', 'systemctl', 'enable', SERVICE], check=True)
    subprocess.run(['sudo', 'systemctl', 'restart', SERVICE], check=True)
    print('\nInstalled and enabled for boot. Open http://<pi-ip>/ or http://<pi-ip>/hantek')
    print('Status:  sudo systemctl status bench-console --no-pager')
    print('Logs:    sudo journalctl -u bench-console -f')
    print('Restart: sudo systemctl restart bench-console')
    print('Disable: sudo systemctl disable --now bench-console')


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f'Setup command failed ({exc.returncode}). Check its output above; '
                         'for startup failures use sudo journalctl -u bench-console -n 50 --no-pager.')
