# Predict.fun 订单簿分析

一个以简单、模块化为目标的 Python 系统：WebSocket 采集指定市场，Redis 增量维护订单簿，ClickHouse 定时保存快照，Streamlit 独立展示实时及历史原始数据和分析指标。仅采集和分析，不下单。

```text
Predict.fun WebSocket → collector → Redis
                                      ↓
                              snapshot 独立进程 → ClickHouse
                                      ↓               ↓
                                 Streamlit 只读可视化
```

## 快速启动

需要 Python 3.11+、Redis 及 ClickHouse HTTP 接口。以下命令在仓库根目录执行：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp config/example.conf config/local.conf
export PREDICT_API_KEY='你的 API key'
export CLICKHOUSE_PASSWORD='你的 ClickHouse 密码'
```

修改 `config/local.conf` 中的市场 ID、Redis 地址和 ClickHouse 连接信息。`market_ids` 支持英文逗号分隔多个市场。初始化 ClickHouse 表：

```bash
orderbook init-db --config config/local.conf
```

然后在三个终端分别运行（每个终端激活虚拟环境，采集终端需要 API key 环境变量）：

```bash
orderbook collect --config config/local.conf
orderbook snapshot --config config/local.conf
streamlit run src/orderbookanly/dashboard.py -- --config config/local.conf
```

打开 Streamlit 输出的本地地址。也可通过旧入口 `python src/orderBookL2.py --config config/local.conf` 或 `python src/orderBookL2Delt.py --config config/local.conf` 启动相同的采集服务；旧入口不再清屏打印订单簿。Ctrl+C 停止各进程。

## 数据语义与一致性

[Predict.fun 官方文档](https://dev.predict.fun/websocket/topics/order-book)规定每次 `predictOrderbook/{marketId}` 推送**完整 Yes 订单簿**，不是上游增量。系统对完整快照做差分，以绝对数量更新 Redis Hash；消失或数量为零的档位会删除。价格和数量规范化为十进制字符串，避免浮点价格键分裂。No 订单簿从 Yes 推导：价格变成 `1 - price`，买卖方向互换，不重复存储。

每个市场的 Redis 键（默认前缀 `predict`）：

| 键后缀 | 类型 | 内容 |
| --- | --- | --- |
| `:bids` / `:asks` | Hash | 价格 → 数量，增量更新 |
| `:latest` | String/JSON | 完整规范化订单簿、原始消息、分析指标 |
| `:deltas` | Stream | 本地差分，数量 0 表示删除 |

完整键形如 `predict:orderbook:3862211:latest`。使用 WATCH/MULTI/EXEC 将档位变化、最新状态、分析和增量记录一起提交。差分基于 Redis 中的上一状态，支持进程重启。拒绝比当前源时间戳更旧的消息，允许同毫秒内的更新。上游没有严格序列号，因此无法判断同毫秒消息的先后。

Redis Stream 按 `stream_maxlen` 近似裁剪，不是永久审计日志。最新原始消息保持完整；并不永久保留每一条上游消息。为保持实现简单，完整 `:latest` 每次都会写入，Hash 才是逐档增量更新。只运行一个采集进程和一个快照进程；使用 Redis 持久化/AOF 与否由部署环境决定。

采集器回复应用层 heartbeat，异常后重连并重新订阅；订阅后 10 秒没有收到首份订单簿时通过官方 REST 接口补取。收到的数据携带源时间和本地接收时间。断线期间保留最后状态，可视化提示数据年龄；长时间无变化也会触发陈旧提示，不能据此断言连接已断开。

ClickHouse 的 `orderbook.snapshots` 表采用 MergeTree，按月分区、按市场和快照时间排序；包含完整档位 JSON、原始消息 JSON、分析 JSON 和可查询的指标列。所有时间使用 UTC。快照进程以配置周期保存当时最新状态，即使订单簿未变化也会保存，便于观察采样时间。服务错误记录日志并在下一周期重试；启动建表失败会退出。没有历史补录、永久重试队列或恰好一次保证，ClickHouse 故障期间可能缺快照，写入结果不确定时可能产生重复。默认无历史 TTL，应按容量需要单独设置。

## 分析及展示

展示页面参考 Modern Trading Dashboard 原型，采用深色终端布局。侧栏选择市场、Yes/No 和可见档位；实时终端提供报价指标、累计深度图、深度不平衡及带累计深度条的买卖盘。历史快照页签保留 Yes 趋势、快照选择与原始数据。图表统计全部档位，可见档位仅限制表格行数；前 N 档指标仍由配置决定。设计约定见 `DESIGN.md`。从仓库根目录启动时自动加载 `.streamlit/config.toml` 主题。

- 最优买价、最优卖价、价差、中间价；缺少一侧时价差和中间价为 null。
- 前 N 档买卖深度：份额之和（不是金额），N 由 `analysis.depth_levels` 指定。
- 深度不平衡：`(买深度 - 卖深度) / (买深度 + 卖深度)`；总深度为零时为 null。
- Redis：实时深度曲线、档位表、原始消息、指标、最近 20 条增量。
- ClickHouse：Yes 最优报价和不平衡历史曲线、历史快照表、单个快照原始数据及 Yes/No 深度。

指标仅描述订单簿，不代表成交量或成交订单流。快照之间的中间变化不会被 ClickHouse 完整保留。

## 配置与模块

配置采用 INI 风格 `.conf`，关闭插值以支持 `%(asctime)s` 等日志格式。`logging` 支持等级、格式、日期格式和可选轮转文件；默认输出到 stderr。日志文件最大 10 MB、保留 3 份。相对路径基于启动目录。密钥通过配置中指定的环境变量名读取，不写入仓库；`config/local.conf` 已忽略。之前脚本里的硬编码 key 已移除，若曾共享过旧文件，建议在平台轮换该 key。

| 模块 | 职责 |
| --- | --- |
| `config.py` | 配置读取、基础校验、日志初始化 |
| `book.py` | 规范化、差分、Yes/No 转换、纯函数分析 |
| `collector.py` | 订阅、心跳、首份数据补取、重连 |
| `storage.py` | Redis 原子更新、ClickHouse 建表/读写 |
| `snapshot.py` | 独立周期快照任务 |
| `dashboard.py` | 只读可视化，采集停止时仍可查看历史 |
| `cli.py` | 启动命令与资源清理 |

新增分析指标主要修改 `book.analyze`；JSON 分析字段自动保留新增指标，如需 SQL 查询列则另做 schema 迁移。新增平台可实现独立采集适配器，输出相同的规范化订单簿。当前没有自动 schema 迁移。

## 验证

```bash
pytest -q
python -m compileall -q src
```

测试覆盖完整替换、档位删除、十进制键、No 转换、空订单簿、非法数据、配置格式、快照字段映射，采集器订阅/心跳/重连、Streamlit 实时与历史页面及 Yes/No 切换，以及真实本地 Redis 的重启基线/旧消息丢弃/原始数据一致性。Redis 测试使用临时 Unix socket，不连接现有数据库；没有 `redis-server` 时跳过。真实 Predict.fun 和 ClickHouse 联调需要有效凭证和运行中的服务。
