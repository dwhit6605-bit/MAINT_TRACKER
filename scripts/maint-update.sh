#!/bin/bash
set -e
cd /opt/maint-super
git pull
source venv/bin/activate
pip install -q -r requirements.txt
sudo systemctl restart maint-super
echo "Update complete."
