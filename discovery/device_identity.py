"""Per-install Discovery proof key; caller persists it only in protected OAuth storage."""
import base64,hashlib

def b64(value):return base64.urlsafe_b64encode(value).decode().rstrip('=')

def generate():
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    key=ec.generate_private_key(ec.SECP256R1());numbers=key.public_key().public_numbers()
    return {'private':b64(key.private_bytes(serialization.Encoding.DER,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())),
        'public':{'kty':'EC','crv':'P-256','x':b64(numbers.x.to_bytes(32,'big')),'y':b64(numbers.y.to_bytes(32,'big'))}}

def sign(device,message):
    from cryptography.hazmat.primitives import serialization,hashes
    from cryptography.hazmat.primitives.asymmetric import ec,utils
    value=device['private'];key=serialization.load_der_private_key(base64.urlsafe_b64decode(value+'='*((4-len(value)%4)%4)),password=None)
    if not isinstance(key,ec.EllipticCurvePrivateKey) or not isinstance(key.curve,ec.SECP256R1):raise ValueError('Invalid Discovery device key')
    r,s=utils.decode_dss_signature(key.sign(message.encode(),ec.ECDSA(hashes.SHA256())))
    return b64(r.to_bytes(32,'big')+s.to_bytes(32,'big'))

def digest(value):return hashlib.sha256(value.encode()).hexdigest()
