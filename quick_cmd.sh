source .venv/bin/activate

# 拉取数据
freqtrade download-data --config config.json --pairs BTC/USDT:USDT ETH/USDT:USDT --timeframe 5m --days 300
freqtrade download-data \
  --config user_data/config-demo.json \
  --userdir user_data \
  --datadir user_data/data/binance \
  --timerange 20250501-20260701 \
  --timeframes 5m 15m 1h 4h 1d

freqtrade download-data --config config.json --timeframes 12h --days 300

# 运行回测
freqtrade backtesting --config config.json --strategy UniversalMACD
freqtrade backtesting --config config.json --userdir user_data \
    --strategy UniversalMACD --timerange 20250501-20260701

  freqtrade backtesting --config user_data/config-backtest.json --userdir user_data \
    --strategy NostalgiaForInfinityX7 \
    --timerange 20250501-20260701 \
    --breakdown month \
    --fee 0.00075 \
    --cache none
# 禁用缓存
  --cache none


# 控制台
freqtrade webserver --config config.json

# 列出数据（简略）
freqtrade list-data

# 详细查看某个交易对的数据范围
freqtrade list-data --show-timerange --userdir user_data --datadir user_data/data/binance

# 模拟盘启动
freqtrade trade \
  --config user_data/config-sim-tel.json \
  --strategy NostalgiaForInfinityX7 \
  --log-file user_data/logs/sim-tel-NostalgiaForInfinityX7.log


# 超参数搜索
freqtrade hyperopt --strategy MeanReversionBB --spaces buy sell \
      --hyperopt-loss SharpeHyperOptLoss -e 200 --timeframe 15m




# 启动 Freqtrade Webserver
docker run -p 8080:8080 -v /Users/luka/Project/Freqtrade/user_data:/freqtrade/user_data freqtradeorg/freqtrade:stable webserver --config /freqtrade/user_data/configs/config.json

# 运行回测
docker run -it \
  -v /Users/luka/Project/Freqtrade/user_data:/freqtrade/user_data \
  freqtradeorg/freqtrade:stable \
  backtesting \
  --config /freqtrade/user_data/configs/config.json \
  --strategy SampleStrategy \
  --timeframe 5m

# 停止 Freqtrade Webserver
docker stop great_rosalind


export HTTP_PROXY="http://127.0.0.1:7897"
export HTTPS_PROXY="http://127.0.0.1:7897"
export ALL_PROXY="socks5://127.0.0.1:7897"
