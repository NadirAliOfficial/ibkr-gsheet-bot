#!/bin/bash

# ==============================================
# IBKR Trailing Stop Automation - Setup Script
# ==============================================

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if Python 3.10+ is installed
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Python 3 is not installed. Please install Python 3.10+ first.${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(sys.version_info.major, sys.version_info.minor)')
read -r major minor <<< "$PYTHON_VERSION"

if [[ "$major" -lt 3 ]] || [[ "$minor" -lt 10 ]]; then
    echo -e "${RED}Python 3.10+ is required. Found Python $major.$minor.${NC}"
    exit 1
fi

# Project directory setup
PROJECT_DIR="ibkr-trailing-stops"
echo -e "${YELLOW}Setting up project in: $PWD/$PROJECT_DIR${NC}"

mkdir -p $PROJECT_DIR/{config,logs,scripts,src}
cd $PROJECT_DIR || exit

# Create virtual environment
echo -e "${GREEN}Creating Python virtual environment...${NC}"
python3 -m venv venv
source venv/bin/activate

# Install required libraries
echo -e "${GREEN}Installing Python dependencies...${NC}"
pip install --upgrade pip

cat > requirements.txt << 'EOL'
ib-insync==0.9.85
gspread==6.0.2
google-auth==2.22.0
google-auth-oauthlib==1.0.0
google-api-python-client==2.104.0
python-dotenv==1.0.0
configparser==5.3.0
pandas==2.1.1
python-telegram-bot==20.5
pywin32==306; sys_platform == 'win32'
EOL

pip install -r requirements.txt

# Create default config files
echo -e "${GREEN}Creating default config files...${NC}"

cat > config/config.ini << 'EOL'
[IBKR]
host = 127.0.0.1
port = 7497
client_id = 1

[GOOGLE]
sheet_id = your_google_sheet_id_here

[TELEGRAM]
bot_token = your_bot_token_here
chat_id = your_chat_id_here
EOL

cat > config/telegram_creds.ini << 'EOL'
[TELEGRAM]
bot_token = your_bot_token
chat_id = your_chat_id
EOL

# Create sample Python script
cat > src/trailing_orders.py << 'EOL'
# Your Python script content here
# (Paste the full script from previous examples)
EOL

# Make scripts executable
chmod +x scripts/*.sh 2> /dev/null

# Final instructions
echo -e "${GREEN}Setup completed successfully!${NC}"
echo -e "\nNext steps:"
echo -e "1. Edit ${YELLOW}config/config.ini${NC} with your IBKR and Google Sheets credentials"
echo -e "2. Place your Google Service Account JSON in ${YELLOW}config/google_service_account.json${NC}"
echo -e "3. Run the script: ${YELLOW}source venv/bin/activate && python src/trailing_orders.py${NC}"

# For Windows users
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    echo -e "\n${YELLOW}Windows detected:${NC}"
    echo -e "Use ${YELLOW}scripts\\run_win.bat${NC} to run the script"
fi