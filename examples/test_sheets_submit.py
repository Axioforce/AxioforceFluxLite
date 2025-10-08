from __future__ import annotations

import datetime


def main() -> None:
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except Exception as e:
        print("Missing dependencies. Install with: pip install gspread google-auth", e)
        return

    # Service account credentials (for demo/testing only)
    service_account_info = {
        "type": "service_account",
        "project_id": "axioforcelivetesting",
        "private_key_id": "4cc5076dea648823e429722a33be61ff72f0fa6f",
        "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQDCVuVmiLG7kjzH\nyUXQeg5GCSCl2lsFfBMJTHIC+448Pcwm/b7TM/n0Y3twZjerdWrrmp3J4z78QbQ6\njf9J4jya9tFD4PpKkInJvqG3V8ruHJ5pjh6oQsSJ5SrYduwbpn5tPBAOwdOmtKUW\ncSDH+S31tt/t4KcuTUyb+fEUs4FygerqFxoLtv0sQOb5XK6R+nKi3wOkLE4l0v1U\nxxBY4o19V8+0x53T5cvEURFUiMAacqT89cLBFwNoCFk9rz8FlD106m/ppQxoxcHw\nbg0IJfmcOhxDmNl51zypbp7M27+22PWIUhUPOIIPcrNLp84ftjEOO+/EGW4VISrz\nwfdtsVi5AgMBAAECggEABpUCMLEr2999wYKBTn35InQdWqCvo9rqRiQEWdzX72sP\nEWRXZI3bxw7g174OiqHFHPUKp74o8aBEEMD4cYcrvaU4Zzp6dQYPiflelCLGvmkn\nuwl+OP1cl2hfRXTvAEITrB10VHD00IOecnPNxBgeGgwMlODz/e8joMYcB93LN5aR\nwF+ZPXR2SuKVlFJAdybAZjylIw4lldh5koLWWJvLU/JCo/H1Uko3BK7+1chTQrX1\nTTNVUy+XoRZNNNOxpdvB1qXkMTWUPeArliyVJtZubYNhRdgCyUm4kWVyGNJ6lll9\n6aAt5SnKXyCyVBXy5nI+YBgl3sWgGau0miyvyOXpkQKBgQDn966sqqYoh6ud72Ao\n4tBA83XrdFnFeWs1VFjRxozpBiAlTIGgySmz2kqmjXJxZi9wCkOXYTempMtUecR+\nr+oMVnqhMCKi5zew8iVlZCQs+G//h6sKMXBzkFOq+qAYIz/DHccYiuVj5lVSKI/z\nF14Acp4jAeXvLDNg4/wlK3VANQKBgQDWeTrWq/NzL2tdscbDaua85Jy4axvtSGex\n0ixn7LNgcwk7+MsDW0pXA0gFHkxukfvGq/ZY6NIBTWox9fucq+zjm2leCHY2NR5o\n1khFcbF1dGjzMERQOoqh1T/q1TQmMXD0PQOt+Wxt50CA1xhvEvoZWT1ixvMT9l5I\nte8JyBgO9QKBgHoiNLwA1Z99X2S2hoDAeznXdfzUs/d/aG0ZzfIVglemvAIneBD6\nGZTymF99FgaS8OMi5Fet/ikll1ERE95ILQj193cq6vGun+nwdLQft9RdskpuWiXx\nxe1yzjq13tkWphnLceqAJyskOUQay0AIy5ucvZpdA32cXijjoPzJFuEJAoGAaTTG\nnA91OIeGT0upiKqjzP0Hs58278qYsy26ArClvSYw3W5Jh7f8W3qMlZYrQAH0U5x/\nF1X9zg2/jgpwBoZ/iZbutOXJtwWPiTWz9fyzZD5aTRDcMc7FumT1GajEEAgotGZJ\nq8myWqcZiRn6LmJMtKqF5jJZgu1Tiq9UNqQkyRECgYBFNt26nnuYSkiyovZBKonx\nCYWoJ6CRexQmO3lAJZazrDSsFhI7/qaoYjX6DxLqcMvUjACSNFraN6zo7ZjEnbGw\nQ3cPaC+kWs9eZ8xIE7IIGHcVPZKagivx6fZJLin3N98iVg0yK1oavFyMis4A3fa1\nLQ/rIuLwmskckbL26HQ28A==\n-----END PRIVATE KEY-----\n",
        "client_email": "axioforcelivetesting@axioforcelivetesting.iam.gserviceaccount.com",
        "client_id": "116599978791633967517",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/axioforcelivetesting%40axioforcelivetesting.iam.gserviceaccount.com",
        "universe_domain": "googleapis.com",
    }

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
    gc = gspread.authorize(creds)

    spreadsheet_id = "19C2NSiFtHGEnQruVpMQ8m5-mkRvAnsLHOGT36U_WvvY"
    sheet_name = "Sheet1"

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Columns: Plate ID, Pass/Fail, Date, Tester, Model ID
    dummy_row = ["TEST-PLATE-123", "Pass", now, "Tester Demo", "06"]

    try:
        sh = gc.open_by_key(spreadsheet_id)
        ws = sh.worksheet(sheet_name)
        ws.append_row(dummy_row, value_input_option="USER_ENTERED")
        print("Appended row:", dummy_row)
    except Exception as e:
        print("Append failed:", e)


if __name__ == "__main__":
    main()


