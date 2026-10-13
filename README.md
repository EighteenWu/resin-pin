# Resin Pin

Resin 旁边的 sidecar：每天把节点池里选定地区（默认 **TW / JP / HK / KR**，页面可开关 SG 等）的可用节点钉成单节点 Platform，并提供可复制、可订阅的 HTTP 正向代理链接。

不修改 Resin 源码。对账走官方 Admin API。

链接形态：

```text
http://<platform>:<proxy-token>@<public-host>:<port>
```

## 做什么

1. 筛选启用、有出站、未熔断、已有出口 IP 的地区节点。默认 `tw/jp/hk/kr`，页面「启用地区」可开关（含 SG），写入 `state.json`。
2. 为每个节点创建一个 Platform（`hk-1`、`jp-2`…），用标签正则钉死这一条。关掉的地区在同步后删除对应自动 Platform。
3. 节点消失后删除对应自动 Platform，不碰你手动建的 Platform。
4. 页面展示状态，支持按最大延迟（毫秒）过滤可用节点，单条复制和一键复制全部可用链接。
5. 提供订阅地址，让下游工具按间隔拉取。超限节点不进入订阅。

## 启动

复制 `.env.example` 为 `.env`，按本地环境填写后启动：

```bash
copy .env.example .env
python -m resin_pin
```

Docker 默认通过 `host.docker.internal` 访问已经在跑的 Resin。若和 Resin 放在同一 compose 网络，把 API 地址改成 compose 服务名。

```bash
docker compose -f docker-compose.yml.example up -d --build
```

浏览器打开服务页面，使用 Resin 管理令牌登录。

## 下游订阅

导出接口（需在查询参数中带上订阅密钥）：

- 纯文本：`/pin/api/export`
- JSON：`/pin/api/export?format=json`

纯文本是每行一条可用代理。JSON 带 `items[].proxyUrl` / `region` / `name`。

## pan-so 网盘自动优选池

通过已登录的 `/api/settings` 设置 `{"auto_pool_enabled": true, "sync_interval_seconds": 300}`，
再调用 `/api/sync` 完成首次验证。开关写入 `state.json`，重启后保留；默认关闭。

每轮同步自动读取页面中可用的 HK/JP 单节点代理，通过 Resin 内网地址分别验证夸克接口和百度 HTTPS，
每个目标连续验证两次，每次超时 4 秒，最多 4 个节点并发。夸克探测使用无分享凭据的只读请求，
预期 HTTP 400 和业务码 14001，验证接口连通性，不验证某个真实分享链接。
只有两个目标均通过的节点才能进入池；按两站点实测最大耗时排序，最多保留 8 个不同出口 IP。

自动维护独立 Platform `panso-auto-hkjp` 的节点规则，不固定节点名单，
不会修改现有手工 Platform。新增、失效和恢复节点在下一轮同步重新验证并增删。
全部验证失败时该池保持零个可路由节点，明确返回不可用，不放开地区池或转为直连。
`/api/catalog` 中的 `auto_pool` 提供最近验证时间、选中节点、实测耗时和失败原因，且不含代理密码。

pan-so 容器使用 `http://panso-auto-hkjp:<代理密码>@resin:2260`，不带 `.Account` 后缀。
关闭 pan-so 原有的全地区目录复制，只保留该入口；自动维护由本服务和 Resin 负责。
页面有效性检查走直连的现有行为不受此配置影响。

本机测试：`python -m unittest discover -s tests -v`。
部署遵循仓库流程：本地提交并推送后，在服务器拉取对应提交，执行
`docker compose up -d --build resin-pin`，确认 `/healthz` 正常，再启用并验证自动池。
