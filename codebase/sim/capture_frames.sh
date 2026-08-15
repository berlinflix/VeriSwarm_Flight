#!/usr/bin/env bash
# Capture frames from the PX4 typhoon_h480 camera (RTP/H.264 on UDP 5600) to
# JPEGs on disk, so the YOLO -> action path can read them like any image.
# Usage: bash sim/capture_frames.sh [out_dir] [seconds]
set -u
OUT="${1:-/tmp/vsframes}"
SECS="${2:-10}"
mkdir -p "$OUT"
rm -f "$OUT"/*.jpg
timeout "$SECS" gst-launch-1.0 -e \
  udpsrc port=5600 caps="application/x-rtp,media=video,clock-rate=90000,encoding-name=H264,payload=96" \
  ! rtpjitterbuffer ! rtph264depay ! avdec_h264 ! videorate ! video/x-raw,framerate=1/1 \
  ! videoconvert ! jpegenc ! multifilesink location="$OUT/f_%03d.jpg" >/dev/null 2>&1
echo "captured $(ls "$OUT"/*.jpg 2>/dev/null | wc -l) frame(s) in $OUT"
