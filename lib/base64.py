# lib/base64.py – Minimal-Wrapper für MicroPython
import ubinascii

def b64decode(data):
    if isinstance(data, str):
        data = data.encode()
    return ubinascii.a2b_base64(data)

