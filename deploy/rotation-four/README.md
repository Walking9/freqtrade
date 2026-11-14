# RotationConsensus 四币模拟服务

交易池：BTC、ETH、SOL、BNB；默认 1x、逐仓永续、最多三仓。
策略源文件：user_data/strategies/rotation_four/RotationConsensus.py。

config.dryrun.example.json 是无密钥的模拟配置样例，不包含账户凭据。
部署时复制为 /opt/freqtrade/user_data/config-rotation-four.json，权限 600。
实际配置、交易数据库和日志不纳入 Git。
服务模板安装为 /etc/systemd/system/freqtrade-rotation-four.service。
新服务独立使用数据库 tradesv3.rotation-consensus-four.dryrun.sqlite 和日志 rotation-four.log。
不加载研究用 offline/sitecustomize.py，不修改或重启旧 freqtrade.service。

查看：systemctl status freqtrade-rotation-four.service
日志：journalctl -u freqtrade-rotation-four.service -f
停止：systemctl stop freqtrade-rotation-four.service

只同步策略和部署模板；本次模拟不打开 API 或 Telegram。
