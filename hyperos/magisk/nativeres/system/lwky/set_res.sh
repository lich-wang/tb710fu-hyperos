#!/system/bin/sh
# Port original: sleep 3; cmd window size 2000x3198 (reason unknown).
# Keep the panel's native size instead; reset also clears a stored override.
sleep 3
/system/bin/cmd window size reset
