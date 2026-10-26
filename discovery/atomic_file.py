"""Atomic byte replacement with bounded retries for Windows sharing contention."""
import os,tempfile,time
from pathlib import Path

def write_bytes(path, payload):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix=path.name+'.',suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name);stream.write(payload);stream.flush();os.fsync(stream.fileno())
        for delay in (.02,.04,.08,.16,.25,.25,None):
            try:os.replace(temporary,path);break
            except PermissionError:
                if delay is None:raise
                time.sleep(delay)
    finally:
        if temporary is not None:
            try:temporary.unlink(missing_ok=True)
            except OSError:pass
