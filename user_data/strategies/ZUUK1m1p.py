# Import necessary libraries for the strategy
from datetime import datetime

from pandas import DataFrame
import talib.abstract as ta  # Import TA-Lib for technical indicators

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy


class ZUUK1m1p(IStrategy):
    # Define the timeframe to 1 minute
    timeframe = '1m'

    # Initial stop loss will be dynamically adjusted
    stoploss = -0.15  # Default value, will be replaced by intelligent stoploss
    minimal_roi = {
        "0": 0.07  # 7%
    }

    # Trailing Stop configuration
    trailing_stop = True
    trailing_stop_positive = 0.05
    trailing_stop_positive_offset = 0.07
    trailing_only_offset_is_reached = True

    # Define minimum number of candles for analysis
    startup_candle_count: int = 200  # Increased for longer indicators

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Calculate the percentage change from one candle to the next
        dataframe['price_change'] = dataframe['close'].pct_change()

        # Calculate ATR
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)

        # Long-term ATR for volatility comparison
        dataframe['long_atr'] = dataframe['atr'].rolling(window=100).mean()
        dataframe['atr_ratio'] = dataframe['atr'] / dataframe['long_atr']

        # EMA for trend detection
        dataframe['ema_short'] = ta.EMA(dataframe, timeperiod=50)
        dataframe['ema_long'] = ta.EMA(dataframe, timeperiod=200)
        dataframe['uptrend'] = dataframe['ema_short'] > dataframe['ema_long']

        # Swing low calculation
        dataframe['swing_low'] = dataframe['low'].rolling(window=5).min()

        return dataframe

    def populate_buy_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Buy condition: price change greater than or equal to 1% in a 1-minute candle
        dataframe.loc[
            (dataframe['price_change'] >= 0.01),
            'buy'
        ] = 1
        return dataframe

    def populate_sell_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # No specific sell conditions beyond ROI and Stop Loss
        dataframe.loc[
            dataframe['close'] > 0,
            'sell'
        ] = 0
        return dataframe

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs
    ) -> float:
        """
        Enhanced custom stoploss.
        """
        # Get the analyzed dataframe
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)

        # Ensure we have enough data
        if len(dataframe) < self.startup_candle_count:
            return 1  # Do not alter the stoploss if not enough data

        # Get the last candle
        last_candle = dataframe.iloc[-1]

        # Get ATR and other indicators
        atr = last_candle['atr']
        atr_ratio = last_candle['atr_ratio']
        uptrend = last_candle['uptrend']
        swing_low = last_candle['swing_low']

        # Dynamic adjustment of atr_multiplier based on market conditions
        if uptrend:
            atr_multiplier = 1.5 * atr_ratio  # Allow wider stoploss in uptrend
        else:
            atr_multiplier = 1.0 * atr_ratio  # Tighter stoploss in downtrend

        # Adjustment based on current profit
        if current_profit > 0.05:
            atr_multiplier *= 0.8  # Tighten stoploss if profit exceeds 5%
        elif current_profit > 0.02:
            atr_multiplier *= 0.9  # Slightly tighten if profit exceeds 2%

        # Adjustment based on trade duration
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 60
        if trade_duration > 120:
            atr_multiplier *= 0.8  # Tighten stoploss after 2 hours

        # Calculate stoploss based on ATR
        atr_stoploss_price = current_rate - (atr * atr_multiplier)
        atr_stoploss_pct = (atr_stoploss_price - trade.open_rate) / trade.open_rate

        # Calculate stoploss based on swing low
        swing_stoploss_price = swing_low * 0.99  # 1% below swing low
        swing_stoploss_pct = (swing_stoploss_price - trade.open_rate) / trade.open_rate

        # Combine stoplosses and ensure it does not exceed maximum stoploss
        stoploss_pct = max(atr_stoploss_pct, swing_stoploss_pct, self.stoploss)

        return stoploss_pct
