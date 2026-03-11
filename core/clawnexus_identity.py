import json
import uuid
from typing import Dict, Tuple

# We will use the cryptography standard library for Ed25519 Keys and Signatures
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

# --- CONSTANTS ---
DID_PREFIX = "did:clawnexus:"

def generate_keypair() -> Tuple[str, str, str]:
    """
    Generates a new Ed25519 keypair for an OpenClaw instance.
    Returns:
        private_key_hex (str): Keep this secret!
        public_key_hex (str): Share this.
        did (str): The ClawID (Decentralized Identifier)
    """
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Convert to Raw Hex
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption()
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw
    )

    private_hex = private_bytes.hex()
    public_hex = public_bytes.hex()
    did = f"{DID_PREFIX}{public_hex}"

    return private_hex, public_hex, did

def _serialize_for_signing(data: Dict) -> bytes:
    """
    Deterministically serializes the payload for signing.
    Sorting keys and removing whitespace is required so the signature matches identically across systems.
    """
    return json.dumps(data, sort_keys=True, separators=(',', ':')).encode('utf-8')

def sign_payload(data: dict, private_key_hex: str) -> str:
    """
    Generates a cryptographic signature of the data dictionary using the provided Ed25519 private key.
    Returns the signature as a hex string.
    """
    # Load the Private Key
    private_bytes = bytes.fromhex(private_key_hex)
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(private_bytes)

    # Generate Signature of the Deterministic Serialization
    data_to_sign = _serialize_for_signing(data)
    signature_bytes = private_key.sign(data_to_sign)
    signature_hex = signature_bytes.hex()

    return signature_hex

def verify_payload(data: dict, signature: str, public_key: str) -> bool:
    """
    Validates an incoming payload's signature against the provided public key hex.
    """
    try:
        public_bytes = bytes.fromhex(public_key)
        pub_key_obj = ed25519.Ed25519PublicKey.from_public_bytes(public_bytes)

        data_to_verify = _serialize_for_signing(data)
        signature_bytes = bytes.fromhex(signature)

        # cryptography throws InvalidSignature exception if it fails
        pub_key_obj.verify(signature_bytes, data_to_verify)
        return True

    except Exception:
        # We catch the InvalidSignature exception or missing keys
        return False
