[Unit]
Description=sshauto git auto-update check
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory={app_root}
ExecStart=/usr/bin/python3 {app_root}/scripts/autoupdate_check.py
StandardOutput=append:/var/log/sshauto/autoupdate.log
StandardError=append:/var/log/sshauto/autoupdate.log
