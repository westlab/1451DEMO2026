import struct

# チャネル定義
channel_profiles = {
    "temperature": {
        "name": b"Temperature",
        "unit": b"degC",
        "datatype": 0x04,  # float32
        "range_min": -20.0,
        "range_max": 60.0,
        "resolution": 0.1,
    },
    "humidity": {
        "name": b"Humidity",
        "unit": b"%RH",
        "datatype": 0x04,
        "range_min": 5.0,
        "range_max": 95.0,
        "resolution": 0.1,
    },
    "pressure": {
        "name": b"Pressure",
        "unit": b"kPa",
        "datatype": 0x04,
        "range_min": 30.0,
        "range_max": 110.0,
        "resolution": 0.01,
    },
    "actuator_pwm": {
        "name": b"PWMOutput",
        "unit": b"percent",
        "datatype": 0x04,
        "range_min": 0.0,
        "range_max": 100.0,
        "resolution": 1.0,
    },
}

def encode_tlv(t, v):
    length = len(v)
    return struct.pack('B', t) + struct.pack('B', length) + v

def float32_to_bytes(value):
    return struct.pack('>f', value)

def create_chan_teds(profile):
    teds = b''
    teds += encode_tlv(0x01, profile["name"])                 # 名前
    teds += encode_tlv(0x02, profile["unit"])                 # 単位
    teds += encode_tlv(0x03, struct.pack('B', profile["datatype"]))  # データ型
    teds += encode_tlv(0x04, float32_to_bytes(profile["range_min"])) # 最小値
    teds += encode_tlv(0x05, float32_to_bytes(profile["range_max"])) # 最大値
    teds += encode_tlv(0x06, float32_to_bytes(profile["resolution"])) # 分解能
    return teds

def main():
    print("Supported Channel Type:", ", ".join(channel_profiles.keys()))
    choice = input("Input Channel Type name to generate:").strip().lower()

    if choice not in channel_profiles:
        print("Invalid type")
        return

    profile = channel_profiles[choice]
    teds_data = create_chan_teds(profile)

    filename = f"chan_teds_{choice}.bin"
    with open(filename, 'wb') as f:
        f.write(teds_data)

    print(f"\ngenerated: {filename}")
    print("HEX code: ")
    hex_output = ' '.join(f"{b:02X}" for b in teds_data)
    print(hex_output)

if __name__ == "__main__":
    main()

