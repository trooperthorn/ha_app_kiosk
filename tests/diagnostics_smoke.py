"""Device-listing failures must not abort bashio's strict startup shell."""
from pathlib import Path
import subprocess
import tempfile

assert Path('/dev/dri').is_dir() and Path('/dev/input').is_dir()
startup = Path('/run.sh').read_text()
block = startup[startup.index('# 1. Check GPU'):startup.index('export XDG_RUNTIME_DIR=')]
with tempfile.TemporaryDirectory() as directory:
    script = Path(directory) / 'diagnostics.sh'
    script.write_text('ls() { return 1; }\n' + block + '\necho DIAGNOSTICS_COMPLETED\n')
    result = subprocess.run(['bashio', str(script)], capture_output=True, text=True, timeout=15)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert 'Could not list DRM devices' in output, output
    assert 'Could not list input devices' in output, output
    assert 'DIAGNOSTICS_COMPLETED' in output, output
print('PASS: failed DRM/input diagnostic listings do not abort startup')
