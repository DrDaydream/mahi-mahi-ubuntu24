"""Build provenance checks shared by the controller and remote validators."""
import hashlib
import inspect
import json
import os
from pathlib import Path

from .settings import COMMIT, RUSTFLAGS, RUST_TOOLCHAIN


def verify_build(source):
    source = Path(source)
    try:
        record = json.loads((source / '.mahi-build.json').read_text())
        expected = {'commit': COMMIT, 'rust_toolchain': RUST_TOOLCHAIN, 'rustflags': RUSTFLAGS}
        if any(record.get(key) != value for key, value in expected.items()):
            raise ValueError('build version or flags do not match')
        binary = source / 'target/release/mysticeti'
        if not binary.is_file() or (os.name != 'nt' and not os.access(binary, os.X_OK)):
            raise ValueError('executable missing')
        digest = hashlib.sha256()
        with binary.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != record.get('sha256'):
            raise ValueError('binary checksum mismatch')
        return record
    except (OSError, ValueError, AttributeError, TypeError) as error:
        raise RuntimeError(f'Invalid build at {source}: {error}. Run fab install first.') from error


def verification_script(source):
    # The same hash/version check executes on the remote machine without Git or Cargo.
    code = '\n'.join([
        'import hashlib, json, os', 'from pathlib import Path',
        f'COMMIT={COMMIT!r}', f'RUST_TOOLCHAIN={RUST_TOOLCHAIN!r}', f'RUSTFLAGS={RUSTFLAGS!r}',
        inspect.getsource(verify_build), f'print(json.dumps(verify_build({source!r})))',
    ])
    return "python3 - <<'MAHI_VERIFY_PY'\n" + code + '\nMAHI_VERIFY_PY'
