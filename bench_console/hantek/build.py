"""Compile the small libsigrok helper for the host architecture (including Pi)."""
import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import threading

_build_lock = threading.Lock()

def helper_path():
    override = os.environ.get('HANTEK_HELPER')
    if override:
        return Path(override).expanduser().resolve()
    native = Path(__file__).parent / 'native'
    sources = sorted([*native.glob('*.c'), *native.glob('*.h')])
    identity = hashlib.sha256(b''.join(p.name.encode() + p.read_bytes() for p in sources)).hexdigest()[:16]
    base = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache')))
    return base / 'owon-bench-console' / f'hantek-capture-{identity}'

def build_helper():
    with _build_lock:
        target = helper_path()
        if target.is_file() and os.access(target, os.X_OK):
            return target
        if os.environ.get('HANTEK_HELPER'):
            raise RuntimeError('HANTEK_HELPER does not point to an executable helper')
        source = Path(__file__).parent / 'native' / 'capture.c'
        try:
            flags = subprocess.run(['pkg-config', '--cflags', '--libs', 'libsigrok', 'libusb-1.0'],
                                   capture_output=True, text=True, check=True, timeout=15)
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=target.parent) as temp:
                output = Path(temp) / 'capture'
                subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', str(source),
                                str(source.parent / 'calibration.c'),
                                '-o', str(output), *shlex.split(flags.stdout)],
                               capture_output=True, text=True, check=True, timeout=60)
                output.chmod(0o755)
                os.replace(output, target)
        except (OSError, subprocess.SubprocessError) as exc:
            detail = getattr(exc, 'stderr', '') or str(exc)
            raise RuntimeError('Install build-essential pkg-config libsigrok-dev libusb-1.0-0-dev '
                               'sigrok-firmware-fx2lafw, then run '
                               'python -m bench_console.hantek.build. ' + detail[-1200:]) from exc
        return target

if __name__ == '__main__':
    print(build_helper())
