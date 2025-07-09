"""Simple Angel One broker wrapper with DRY-RUN support.

This isolates SmartAPI calls so the rest of the codebase remains testable.
"""
from __future__ import annotations

import logging
import time
import random
from typing import Any, Dict

try:
    from SmartApi import SmartConnect  # type: ignore
except ImportError:  # pragma: no cover – keeps mypy happy if library absent
    SmartConnect = Any  # pylint: disable=invalid-name

logger = logging.getLogger(__name__)

class Broker:
    """Angel One SmartAPI thin wrapper.

    Parameters
    ----------
    smart_api : SmartConnect
        Authenticated SmartConnect instance.
    dry_run : bool, default True
        When True, orders are simulated and **NOT** sent to the broker.
    """

    def __init__(self, smart_api: SmartConnect | None, dry_run: bool = True):
        self.smart_api = smart_api
        self.dry_run = dry_run
        if self.dry_run:
            logger.warning("Broker running in DRY-RUN mode – no real orders will be placed.")
        # Cache login params for future refresh
        self._login_params = {}
        if self.smart_api and hasattr(self.smart_api, "api_key"):
            # SmartConnect stores creds on the instance; capture them
            self._login_params["api_key"] = getattr(self.smart_api, "api_key", None)
            self._login_params["client_code"] = getattr(self.smart_api, "client_code", None)
        

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _refresh_session(self) -> bool:
        """Attempt to refresh/renew SmartAPI session token."""
        if not self.smart_api or not self._login_params:
            return False
        try:
            totp_secret = os.getenv("ANGEL_TOTP_SECRET")
            if not totp_secret:
                logger.error("Cannot refresh session – ANGEL_TOTP_SECRET env var missing")
                return False
            import pyotp
            totp = pyotp.TOTP(totp_secret)
            data = self.smart_api.generateSession(
                self._login_params["client_code"],
                os.getenv("ANGEL_PASSWORD"),
                totp.now(),
            )
            if data and data.get("status"):
                logger.info("Session refreshed successfully")
                return True
            logger.error("Session refresh failed: %s", data)
            return False
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Session refresh exception: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def place_market_order(
        self,
        symbol_token: str,
        trading_symbol: str,
        transaction_type: str,
        quantity: int,
        exchange: str = "MCX",
        product_type: str = "INTRADAY",
        variety: str = "NORMAL",
        duration: str = "DAY",
    ) -> Dict[str, Any]:
        """Place a market order or simulate one in dry-run mode."""
        if self.dry_run or self.smart_api is None:
            order_id = f"SIM-{int(time.time())}-{random.randint(100, 999)}"
            logger.info("[DRY-RUN] %s %s x%d → %s", transaction_type, trading_symbol, quantity, order_id)
            return {"order_id": order_id, "simulated": True}

        # Resolve non-numeric symbol token if caller passed "FUT"/"CE"/"PE" etc.
        if not symbol_token.isdigit():
            from mcx import resolve_token  # late import to avoid circular at module load
            numeric = resolve_token("CRUDEOIL", None, "", option_type=symbol_token if symbol_token in {"CE", "PE"} else None)
            if numeric:
                logger.warning("Converted symboltoken %s -> %s", symbol_token, numeric)
                symbol_token = numeric
            else:
                # Fallback: try lookup by trading_symbol directly
                from pathlib import Path
                import pandas as pd
                instruments_path = Path("instruments")
                if instruments_path.exists():
                    # choose most recent csv
                    csv_files = sorted(instruments_path.glob("*_instrument_file.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if csv_files:
                        df = pd.read_csv(csv_files[0], low_memory=False)
                        col = 'tradingsymbol' if 'tradingsymbol' in df.columns else 'symbol'
                        match = df[df[col] == trading_symbol]
                        if not match.empty:
                            numeric = str(match.iloc[0]['token'])
                            logger.warning("Trading symbol lookup: %s -> token %s", trading_symbol, numeric)
                            symbol_token = numeric
                        else:
                            return {"error": f"Invalid symbol_token {symbol_token}"}
                else:
                    return {"error": f"Invalid symbol_token {symbol_token}"}

        def _do_place():
            orderparams = {
                "variety": variety,
                "tradingsymbol": trading_symbol,
                "symboltoken": symbol_token,
                "transactiontype": transaction_type,
                "exchange": exchange,
                "ordertype": "MARKET",
                "producttype": product_type,
                "duration": duration,
                "quantity": quantity,
            }
            return self.smart_api.placeOrder(orderparams)

        try:
            response = _do_place()
            # Retry once on invalid/expired token
            if isinstance(response, dict) and (response.get("errorcode") == "AG8001" or response.get("message") == "Invalid Token"):
                logger.warning("Session token expired – refreshing and retrying once")
                if self._refresh_session():
                    response = _do_place()
            if response and isinstance(response, dict) and response.get("status") and response.get("data") and response["data"].get("orderid"):
                oid = response["data"]["orderid"]
                logger.info("Order placed – ID: %s", oid)
                return {"order_id": oid, "simulated": False}
            err = None
            if isinstance(response, dict):
                err = response.get("message") or response.get("errorcode")
            err = err or "order rejected"
            logger.error("Broker error: %s", err)
            return {"error": err}
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Failed to place order: %s", exc, exc_info=True)
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Unified order helper used by REST endpoint
    # ------------------------------------------------------------------
    def place_order(
        self,
        symbol_token: str,
        trading_symbol: str,
        side: str,
        qty: int,
        order_type: str = "MARKET",
        price: float | None = None,
        exchange: str = "MCX",
        product_type: str = "INTRADAY",
        variety: str = "NORMAL",
        stoploss: float | None = None,
        squareoff: float | None = None,
        trailing_sl: float | None = None,
    ) -> Dict[str, Any]:
        """Route-friendly wrapper supporting MARKET and LIMIT orders."""
        order_type = order_type.upper()
        side = side.upper()
        if order_type == "MARKET":
            return self.place_market_order(
                symbol_token=symbol_token,
                trading_symbol=trading_symbol,
                transaction_type=side,
                quantity=qty,
                exchange=exchange,
                product_type=product_type,
                 variety=variety,
            )

        # LIMIT order logic
        if price is None:
            return {"error": "price required for LIMIT order"}

        if self.dry_run or self.smart_api is None:
            order_id = f"SIM-{int(time.time())}-{random.randint(100, 999)}"
            logger.info("[DRY-RUN] LIMIT %s %s x%d @%s → %s", side, trading_symbol, qty, price, order_id)
            return {"order_id": order_id, "simulated": True}

        try:
            orderparams = {
                "variety": variety,
                "tradingsymbol": trading_symbol,
                "symboltoken": symbol_token,
                "transactiontype": side,
                "exchange": exchange,
                "ordertype": "LIMIT",
                "producttype": product_type,
                "price": price,
                "quantity": qty,
                "duration": "DAY",
            }
            # Add bracket-order optional params
            if stoploss is not None:
                orderparams["stoploss"] = str(stoploss)
            if squareoff is not None:
                orderparams["squareoff"] = str(squareoff)
            if trailing_sl is not None:
                orderparams["trailingStopLoss"] = str(trailing_sl)

            resp = self.smart_api.placeOrder(orderparams)
            if resp and isinstance(resp, dict) and resp.get("status") and resp.get("data") and resp["data"].get("orderid"):
                return {"order_id": resp["data"]["orderid"], "simulated": False}
            return {"error": resp.get("message") or "order rejected"}
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Failed to place LIMIT order: %s", exc, exc_info=True)
            return {"error": str(exc)}
