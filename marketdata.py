"""
Enhanced Market Data Processing Module
Provides comprehensive market data analysis and processing functions
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Union, Tuple
from datetime import datetime, timezone
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class MarketDepth:
    """Market depth data structure"""
    best_bid_price: float
    best_bid_qty: int
    best_ask_price: float
    best_ask_qty: int
    total_bid_qty: int
    total_ask_qty: int
    spread: float
    spread_percent: float

@dataclass
class TickData:
    """Standardized tick data structure"""
    symbol: str
    token: str
    timestamp: datetime
    ltp: float
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int
    open_interest: int
    oi_change: float
    total_buy_qty: int
    total_sell_qty: int
    market_depth: Optional[MarketDepth] = None

class MarketDataProcessor:
    """Enhanced market data processing class"""
    
    def __init__(self):
        self.tick_buffer = []
        self.last_processed_time = None
        
    def process_tick(self, raw_tick: dict) -> TickData:
        """
        Process raw tick data into standardized format
        
        Args:
            raw_tick: Raw tick data from WebSocket
            
        Returns:
            TickData: Processed tick data
        """
        try:
            # Extract timestamp
            timestamp = self._extract_timestamp(raw_tick)
            
            # Extract price data
            ltp = float(raw_tick.get('last_traded_price', 0)) / 100
            open_price = float(raw_tick.get('open_price_of_the_day', 0)) / 100
            high_price = float(raw_tick.get('high_price_of_the_day', 0)) / 100
            low_price = float(raw_tick.get('low_price_of_the_day', 0)) / 100
            
            # Extract volume and OI data
            volume = int(raw_tick.get('volume_trade_for_the_day', 0))
            open_interest = int(raw_tick.get('open_interest', 0))
            oi_change = float(raw_tick.get('open_interest_change_percentage', 0))
            
            # Extract order flow data
            total_buy_qty = int(raw_tick.get('total_buy_quantity', 0))
            total_sell_qty = int(raw_tick.get('total_sell_quantity', 0))
            
            # Process market depth
            market_depth = self._process_market_depth(raw_tick)
            
            return TickData(
                symbol=raw_tick.get('symbol', ''),
                token=str(raw_tick.get('token', '')),
                timestamp=timestamp,
                ltp=ltp,
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                close_price=ltp,  # Using LTP as close for real-time data
                volume=volume,
                open_interest=open_interest,
                oi_change=oi_change,
                total_buy_qty=total_buy_qty,
                total_sell_qty=total_sell_qty,
                market_depth=market_depth
            )
            
        except Exception as e:
            logger.error(f"Error processing tick data: {e}")
            raise
    
    def _extract_timestamp(self, tick: dict) -> datetime:
        """Extract and convert timestamp to UTC datetime"""
        timestamp_ms = tick.get('exchange_timestamp', int(datetime.now().timestamp() * 1000))
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    
    def _process_market_depth(self, tick: dict) -> Optional[MarketDepth]:
        """Process market depth data"""
        try:
            best_5_buy = tick.get('best_5_buy_data', [])
            best_5_sell = tick.get('best_5_sell_data', [])
            
            if not best_5_buy or not best_5_sell:
                return None
            
            # Extract best bid/ask
            best_bid = best_5_buy[0] if best_5_buy else {}
            best_ask = best_5_sell[0] if best_5_sell else {}
            
            best_bid_price = float(best_bid.get('price', 0)) / 100
            best_bid_qty = int(best_bid.get('quantity', 0))
            best_ask_price = float(best_ask.get('price', 0)) / 100
            best_ask_qty = int(best_ask.get('quantity', 0))
            
            # Calculate total quantities
            total_bid_qty = sum(int(order.get('quantity', 0)) for order in best_5_buy)
            total_ask_qty = sum(int(order.get('quantity', 0)) for order in best_5_sell)
            
            # Calculate spread
            spread = best_ask_price - best_bid_price
            spread_percent = (spread / best_bid_price * 100) if best_bid_price > 0 else 0
            
            return MarketDepth(
                best_bid_price=best_bid_price,
                best_bid_qty=best_bid_qty,
                best_ask_price=best_ask_price,
                best_ask_qty=best_ask_qty,
                total_bid_qty=total_bid_qty,
                total_ask_qty=total_ask_qty,
                spread=spread,
                spread_percent=spread_percent
            )
            
        except Exception as e:
            logger.error(f"Error processing market depth: {e}")
            return None

    def get_open_interest(tick: Union[dict, TickData]) -> Optional[int]:
        """
        Extract open interest from tick data
        
        Args:
            tick: Either raw tick dict or TickData object
            
        Returns:
            Open interest value or None
        """
        if isinstance(tick, TickData):
            return tick.open_interest
        return tick.get('open_interest', None)

    def get_market_depth(tick: Union[dict, TickData]) -> Dict:
        """
        Extract market depth from tick data
        
        Args:
            tick: Either raw tick dict or TickData object
            
        Returns:
            Dictionary containing market depth information
        """
        if isinstance(tick, TickData):
            if tick.market_depth:
                return {
                    'best_bid_price': tick.market_depth.best_bid_price,
                    'best_bid_qty': tick.market_depth.best_bid_qty,
                    'best_ask_price': tick.market_depth.best_ask_price,
                    'best_ask_qty': tick.market_depth.best_ask_qty,
                    'total_bid_qty': tick.market_depth.total_bid_qty,
                    'total_ask_qty': tick.market_depth.total_ask_qty,
                    'spread': tick.market_depth.spread,
                    'spread_percent': tick.market_depth.spread_percent
                }
            return {}
        
        # Legacy support for raw tick dict
        return {
            'best_bid': tick.get('best_5_buy_data', []),
            'best_ask': tick.get('best_5_sell_data', [])
        }

    def calculate_order_flow_imbalance(tick: Union[dict, TickData]) -> float:
        """
        Calculate order flow imbalance ratio
        
        Args:
            tick: Either raw tick dict or TickData object
            
        Returns:
            Order flow imbalance ratio (-1 to 1, where positive indicates buying pressure)
        """
        if isinstance(tick, TickData):
            total_buy = tick.total_buy_qty
            total_sell = tick.total_sell_qty
        else:
            total_buy = tick.get('total_buy_quantity', 0)
            total_sell = tick.get('total_sell_quantity', 0)
        
        total_volume = total_buy + total_sell
        if total_volume == 0:
            return 0
        
        return (total_buy - total_sell) / total_volume

    def calculate_price_change_percent(tick: Union[dict, TickData]) -> float:
        """
        Calculate price change percentage from open
        
        Args:
            tick: Either raw tick dict or TickData object
            
        Returns:
            Price change percentage
        """
        if isinstance(tick, TickData):
            current_price = tick.ltp
            open_price = tick.open_price
        else:
            current_price = float(tick.get('last_traded_price', 0)) / 100
            open_price = float(tick.get('open_price_of_the_day', 0)) / 100
        
        if open_price == 0:
            return 0
        
        return ((current_price - open_price) / open_price) * 100

    def get_volatility_indicator(tick: Union[dict, TickData]) -> float:
        """
        Calculate intraday volatility indicator
        
        Args:
            tick: Either raw tick dict or TickData object
            
        Returns:
            Volatility indicator (high-low range as percentage of open)
        """
        if isinstance(tick, TickData):
            high = tick.high_price
            low = tick.low_price
            open_price = tick.open_price
        else:
            high = float(tick.get('high_price_of_the_day', 0)) / 100
            low = float(tick.get('low_price_of_the_day', 0)) / 100
            open_price = float(tick.get('open_price_of_the_day', 0)) / 100
        
        if open_price == 0:
            return 0
        
        return ((high - low) / open_price) * 100

    # Legacy function names for backward compatibility
    def get_tick_ltp(tick: Union[dict, TickData]) -> float:
        """Get last traded price from tick"""
        if isinstance(tick, TickData):
            return tick.ltp
        return float(tick.get('last_traded_price', 0)) / 100

    def get_tick_volume(tick: Union[dict, TickData]) -> int:
        """Get volume from tick"""
        if isinstance(tick, TickData):
            return tick.volume
        return tick.get('volume_trade_for_the_day', 0)

    def get_ohlc_data(self, interval: str = '5s') -> Dict[str, List]:
        """
        Get OHLC data for different time intervals
        
        Args:
            interval: Time interval (1s, 5s, 10s, 30s, 1min, 5min, 15min, 1h)
            
        Returns:
            Dictionary with OHLC data for FUT, CE, PE
        """
        try:
            # Convert tick buffer to OHLC data
            ohlc_data = {}
            
            # Group ticks by symbol type
            symbol_groups = self._group_ticks_by_symbol()
            
            for symbol_type, ticks in symbol_groups.items():
                if ticks:
                    ohlc_data[symbol_type] = self._convert_ticks_to_ohlc(ticks, interval)
                else:
                    ohlc_data[symbol_type] = []
            
            return ohlc_data
            
        except Exception as e:
            logger.error(f"Error getting OHLC data: {str(e)}", e)
            return {'FUT': [], 'CE': [], 'PE': []}
    
    def get_last_update_time(self) -> Optional[str]:
        """Get the timestamp of the last data update"""
        if self.last_processed_time:
            return self.last_processed_time.isoformat()
        return None
    
    def _group_ticks_by_symbol(self) -> Dict[str, List[TickData]]:
        """Group ticks by symbol type (FUT, CE, PE)"""
        groups = {'FUT': [], 'CE': [], 'PE': []}
        
        for tick in self.tick_buffer[-100:]:  # Last 100 ticks
            if isinstance(tick, TickData):
                symbol = tick.symbol.upper()
                if 'FUT' in symbol:
                    groups['FUT'].append(tick)
                elif 'CE' in symbol:
                    groups['CE'].append(tick)
                elif 'PE' in symbol:
                    groups['PE'].append(tick)
        
        return groups
    
    def _convert_ticks_to_ohlc(self, ticks: List[TickData], interval: str) -> List[Dict]:
        """Convert tick data to OHLC format"""
        if not ticks:
            return []
        
        # Simple aggregation for demonstration
        # In production, implement proper time-based aggregation
        ohlc = []
        
        # Group ticks into time intervals
        for tick in ticks[-50:]:  # Last 50 ticks as example
            ohlc_record = {
                'timestamp': tick.timestamp.isoformat(),
                'open': tick.open_price,
                'high': tick.high_price,
                'low': tick.low_price,
                'close': tick.ltp,
                'volume': tick.volume,
                'oi': tick.open_interest
            }
            ohlc.append(ohlc_record)
        
        return ohlc