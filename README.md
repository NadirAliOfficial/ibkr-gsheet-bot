# IBKR Trailing Stop Automation

## Setup
1. Enable IBKR API in TWS:
   - Configure > API > Enable Socket Clients
   - Set port to `7497` (paper) or `7496` (live)

2. Google Sheets API:
   - Create service account in Google Cloud Console
   - Download JSON credentials to `config/google_service_account.json`
   - Share your sheet with the service account email

3. Install dependencies:
   ```bash
   chmod +x scripts/install_deps.sh
   ./scripts/install_deps.sh