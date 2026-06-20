import binascii

# Prompt the user to input a hexadecimal string (without CRC)
hex_input = input("Enter the TEDS hex string without CRC (space-separated):\n")

# Remove spaces and line breaks, then convert to byte array
teds_body = bytes.fromhex(hex_input.strip())

# Calculate the length of the TEDS body and encode it in 4-byte little-endian format
teds_length = len(teds_body)
length_bytes = teds_length.to_bytes(4, byteorder='little')

# Combine length and TEDS body (without CRC)
teds_full = length_bytes + teds_body

# Compute CRC-16-CCITT (poly=0x1021, init=0xFFFF, no inversion, no final XOR)
def compute_crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = (crc << 1) ^ 0x1021 if (crc & 0x8000) else crc << 1
            crc &= 0xFFFF
    return crc

# Calculate CRC
crc = compute_crc16_ccitt(teds_full)

# Append CRC to the end
teds_complete = teds_full + crc.to_bytes(2, 'big')

# Display the final hex string (space-separated)
hex_output = ' '.join(f'{b:02X}' for b in teds_complete)
print("\nTEDS Query Response (with Length and CRC):")
print(hex_output)
