# Vouch — start the server with the daily Greenbook + warnings refresh on.
#
# Run from this folder:
#   .\start-vouch.ps1
#
# Optional overrides before starting:
#   $env:VOUCH_PORT = "8091"   # serve on another port
#   $env:VOUCH_HOST = "0.0.0.0" # allow phones on the same Wi-Fi

$env:VOUCH_AUTO_SYNC = "1"
python server.py
