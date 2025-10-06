# capital_quickstart.py
import os, time, json, threading
import requests
from websocket import create_connection
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("CAPITAL_BASE_URL", "https://demo-api-capital.backend-capital.com").rstrip("/")
API_KEY = os.getenv("CAPITAL_API_KEY")
IDENTIFIER = os.getenv("CAPITAL_IDENTIFIER")
API_PASS = os.getenv("CAPITAL_API_PASSWORD")

WS_URL = "wss://api-streaming-capital.backend-capital.com/connect"  # official stream endpoint

class CapitalClient:
    def __init__(self, base_url, api_key, identifier, api_pass):
        self.base_url = base_url
        self.api_key = api_key
        self.identifier = identifier
        self.api_pass = api_pass
        self.cst = None
        self.sec = None

    # --- Auth: POST /api/v1/session
    def login(self):
        url = f"{self.base_url}/api/v1/session"
        hdrs = {"X-CAP-API-KEY": self.api_key, "Content-Type": "application/json"}
        body = {"identifier": self.identifier, "password": self.api_pass, "encryptedPassword": False}
        r = requests.post(url, headers=hdrs, json=body, timeout=20)
        r.raise_for_status()
        # Tokens arrive in headers
        self.cst = r.headers.get("CST")
        self.sec = r.headers.get("X-SECURITY-TOKEN")
        if not self.cst or not self.sec:
            raise RuntimeError("Login ok but CST / X-SECURITY-TOKEN missing in headers.")
        return True

    def _auth_headers(self):
        if not (self.cst and self.sec):
            raise RuntimeError("Not authenticated; call login() first.")
        return {"CST": self.cst, "X-SECURITY-TOKEN": self.sec}

    # Keep REST session warm (GET /api/v1/ping)
    def ping(self):
        url = f"{self.base_url}/api/v1/ping"
        r = requests.get(url, headers=self._auth_headers(), timeout=10)
        r.raise_for_status()
        return r.json()

    # Find instruments (GET /api/v1/markets?searchTerm=...)
    def search_markets(self, search_term, epics=None):
        url = f"{self.base_url}/api/v1/markets"
        params = {"searchTerm": search_term}
        if epics:
            params["epics"] = ",".join(epics)
        r = requests.get(url, headers=self._auth_headers(), params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    # Place MARKET position (POST /api/v1/positions)
    # Use profit/stop *level* or *distance*/*amount* per docs.
    def place_market_order(self, epic, direction, size, **risk):
        url = f"{self.base_url}/api/v1/positions"
        payload = {"epic": epic, "direction": direction.upper(), "size": float(size)}
        payload.update({k: v for k, v in risk.items() if v is not None})
        r = requests.post(url, headers={**self._auth_headers(), "Content-Type": "application/json"}, json=payload, timeout=20)
        r.raise_for_status()
        deal_ref = r.json().get("dealReference")
        # Confirm status (GET /api/v1/confirms/{dealReference})
        conf = requests.get(f"{self.base_url}/api/v1/confirms/{deal_ref}", headers=self._auth_headers(), timeout=20)
        conf.raise_for_status()
        return {"dealReference": deal_ref, "confirm": conf.json()}

    # --- WebSocket price stream (marketData.subscribe)
    def stream_quotes(self, epics, on_quote, run_seconds=30):
        ws = create_connection(WS_URL, timeout=20)
        try:
            # subscribe
            msg = {
                "destination": "marketData.subscribe",
                "correlationId": "1",
                "cst": self.cst,
                "securityToken": self.sec,
                "payload": {"epics": list(epics)[:40]}  # API limit
            }
            ws.send(json.dumps(msg))

            # ping every 9 minutes (keepalive)
            def pinger():
                while True:
                    time.sleep(540)
                    ws.send(json.dumps({"destination": "ping", "correlationId": "keepalive", "cst": self.cst, "securityToken": self.sec}))
            threading.Thread(target=pinger, daemon=True).start()

            t_end = time.time() + run_seconds
            while time.time() < t_end:
                raw = ws.recv()
                if not raw:
                    continue
                data = json.loads(raw)
                if data.get("destination") == "quote" and "payload" in data:
                    on_quote(data["payload"])
        finally:
            try:
                ws.close()
            except:  # noqa: E722
                pass

if __name__ == "__main__":
    cap = CapitalClient(BASE_URL, API_KEY, IDENTIFIER, API_PASS)
    cap.login()
    print("Ping:", cap.ping())

    # Search BTC and get its epic
    markets = cap.search_markets("BTC")
    # pick first result (inspect structure in your account/region)
    first_epic = markets.get("markets", [{}])[0].get("epic")
    print("Using epic:", first_epic)

    # Stream quotes for ~20s
    def on_quote(q):
        # q = {epic, bid, ofr, timestamp, ...}
        print(q)
    cap.stream_quotes([first_epic], on_quote, run_seconds=20)

    # Place a tiny demo BUY with a take-profit/stop by distance (example only!)
    if first_epic:
        trade = cap.place_market_order(
            epic=first_epic, direction="BUY", size=1,
            profitDistance=10,  # example: distance units depend on instrument
            stopDistance=10,
            guaranteedStop=False
        )
        print("Order result:", trade)