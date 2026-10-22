"""Windows instance lock; explicit test/state profiles never wake another app."""
import ctypes
import hashlib
import os
from pathlib import Path

DEFAULT_STATE=Path(os.environ.get('LOCALAPPDATA',str(Path.cwd())))/'AIExperience/Network'

def instance_name(state_dir=None):
    path=os.path.normcase(str(Path(state_dir or DEFAULT_STATE).resolve()))
    if path==os.path.normcase(str(DEFAULT_STATE.resolve())):return 'Local\\AI-Link-Native-Desktop-0.2'
    return 'Local\\AI-Link-State-'+hashlib.sha256(path.encode('utf-8')).hexdigest()[:24]

def acquire_instance(state_dir=None):
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_wchar_p]
    kernel.CreateMutexW.restype=ctypes.c_void_p
    handle=kernel.CreateMutexW(None,False,instance_name(state_dir))
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error()==183:
        kernel.CloseHandle.argtypes=[ctypes.c_void_p];kernel.CloseHandle(handle);return None
    return handle
