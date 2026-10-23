"""Windows account-bound encrypted rehearsal archives, no key/plaintext files."""
import ctypes
from ctypes import wintypes
import os


class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]


def transform(value,*,decrypt=False):
    if os.name!='nt':raise RuntimeError('Windows DPAPI required; no insecure fallback')
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    function=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    function.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    buffer=(ctypes.c_ubyte*len(value)).from_buffer_copy(value);source=Blob(len(value),buffer);output=Blob()
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output)):
        raise RuntimeError('Backup protection/integrity check failed; no plaintext written')
    try:return ctypes.string_at(output.data,output.size)
    finally:kernel.LocalFree(output.data)
