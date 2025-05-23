#!/bin/bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install ib-insync gspread python-dotnetconfigparser
echo "Done! Activate virtualenv with 'source venv/bin/activate'"