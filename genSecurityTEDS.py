import struct

def encode_tlv(t, value_bytes):
    length = len(value_bytes)
    return struct.pack('B', t) + struct.pack('B', length) + value_bytes

def uint8_input(prompt, default):
    try:
        val = input(f"{prompt} [{default}]: ").strip()
        return int(val) if val else default
    except:
        return default

def hex32_input(prompt, default):
    try:
        val = input(f"{prompt} [0x{default:08X}]: ").strip()
        return int(val, 16) if val else default
    except:
        return default

def create_security_teds_interactive():
    teds = b''

    print("=== SecurityTEDS Interactive Builder ===")

    # Type 0x01: Access Control
    access = uint8_input("Access Control (1=Read-Only, 2=Write-Only, 3=Read/Write)", 3)
    teds += encode_tlv(0x01, struct.pack('B', access))

    # Type 0x02: Encryption Algorithm
    print("Encryption Algorithm: 0=None, 1=AES-128, 2=AES-256")
    enc_algo = uint8_input("Select Encryption Algorithm", 1)
    teds += encode_tlv(0x02, struct.pack('B', enc_algo))

    # Type 0x03: Key ID (4 bytes)
    key_id = hex32_input("Key ID (hex)", 0xAABBCCDD)
    teds += encode_tlv(0x03, struct.pack('>I', key_id))

    # Type 0x04: Key Lifetime (seconds, uint32)
    key_lifetime = uint8_input("Key Lifetime (seconds)", 3600)
    teds += encode_tlv(0x04, struct.pack('>I', key_lifetime))

    # Type 0x05: Role (1=Publisher, 2=Subscriber, 3=Both)
    role = uint8_input("Role (1=Publisher, 2=Subscriber, 3=Both)", 3)
    teds += encode_tlv(0x05, struct.pack('B', role))

    # Optional: Type 0x06: Hash Algorithm
    print("Hash Algorithm: 0=None, 1=SHA-256, 2=SHA-384, 3=SHA-512")
    hash_algo = uint8_input("Select Hash Algorithm (optional)", 1)
    teds += encode_tlv(0x06, struct.pack('B', hash_algo))

    return teds

def main():
    teds = create_security_teds_interactive()

    # Save to binary
    with open("security_teds.bin", "wb") as f:
        f.write(teds)

    print("\nSecurityTEDS written to security_teds.bin")
    print("Hex Output:")
    print(" ".join(f"{b:02X}" for b in teds))

if __name__ == "__main__":
    main()

