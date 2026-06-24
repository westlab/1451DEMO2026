#!/bin/bash

# M5Core2-TIM コンパイル・書き込みスクリプト

set -e

FQBN="esp32:esp32:m5stack_core2"
SKETCH="M5Core2-TIM.ino"
PORT="/dev/cu.usbserial-57150184751"

echo "=== M5Core2-TIM ビルド開始 ==="

# クリーンコンパイル
echo "コンパイル中..."
arduino-cli compile --fqbn ${FQBN} ${SKETCH} --clean

# 書き込み
echo "書き込み中..."
arduino-cli upload --fqbn ${FQBN} -p ${PORT} ${SKETCH}

echo "=== 完了 ==="
