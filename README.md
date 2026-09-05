# Resin Pin

Resin 旁边的 sidecar：每天把节点池里 **TW / JP / HK / SG / KR** 的可用节点钉成单节点 Platform，并提供可复制、可订阅的 HTTP 正向代理链接。

不修改 Resin 源码。对账走官方 Admin API。

链接形态：

```text
http://<platform>:<proxy-token>@<public-host>:<port>
```

## 做什么

1. 筛选启用、有出站、未熔断、已有出口 IP 的 `tw/jp/hk/sg/kr` 节点。
2. 为每个节点创建一个 Platform（`hk-1`、`jp-2`…），用标签正则钉死这一条。
3. 节点消失后删除对应自动 Platform，不碰你手动建的 Platform。
4. 页面展示状态，支持单条复制和一键复制全部可用链接。
5. 提供订阅地址，让下游工具按间隔拉取。

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
