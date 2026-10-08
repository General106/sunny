#!/usr/bin/env bash
# Start Sunny (formerly Jarvis). Pass --text to chat by keyboard instead of voice.
cd "$(dirname "$0")"
# Let faster-whisper find the pip-installed CUDA libraries.
SITE=.venv/lib/python3.12/site-packages/nvidia
export LD_LIBRARY_PATH="$PWD/$SITE/cublas/lib:$PWD/$SITE/cudnn/lib:${LD_LIBRARY_PATH}"
# Mic levels (they reset after reboots): the built-in and headset mics overload at max boost.
amixer -q -c0 cset name='Internal Mic Boost Volume' 0,0 2>/dev/null
amixer -q -c0 cset name='Mic Boost Volume' 1,1 2>/dev/null
wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 0.3 2>/dev/null
# Keep idle CPU low: stop math-library threads from busy-waiting between transcriptions.
export OMP_WAIT_POLICY=PASSIVE OMP_NUM_THREADS=2
exec .venv/bin/python jarvis.py "$@"
