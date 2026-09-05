# Resin Pin

Resin 旁边的 sidecar：每天把节点池里 **TW / JP / HK / SG / KR** 的可用节点钉成单节点 Platform，并提供可复制、可订阅的 HTTP 正向代理链接。

不修改 Resin 源码。对账走官方 Admin API。

链接形态：

```text
http://hk-1:PROXY_TOKEN@PUBLIC_HOST:2260
```

## 做什么

1. 筛选启用、有出站、未熔断、已有出口 IP 的 `tw/jp/hk/sg/kr` 节点。
2. 为每个节点创建一个 Platform（`hk-1`、`jp-2`…），用标签正则钉死这一条。
3. 节点消失后删除对应自动 Platform，不碰你手动建的 Platform。
4. 页面展示状态，支持单条复制和一键复制全部可用链接。
5. 提供订阅地址，让 hysj-helper / xyzw-helper / 盘搜自己按间隔拉取。

## 启动

同机 Python：

```bash
copy .env.example .env
# 填 token 和公网地址
python -m resin_pin
```

Docker 默认通过 `host.docker.internal:2260` 访问已经在跑的 Resin。若和 Resin 放在同一 compose 网络，把 `RESIN_URL` 改成 `http://resin:2260`。

```bash
docker compose -f docker-compose.yml.example up -d --build
```

打开 `https://pin.example.com/pin/`，用 **Resin Admin Token** 登录。

## 环境变量

| 变量 | 含义 |
| --- | --- |
| `RESIN_URL` | Resin API 地址，容器互访用服务名 |
| `RESIN_ADMIN_TOKEN` | 管理 API Bearer |
| `RESIN_PROXY_TOKEN` | 写进代理链接密码 |
| `RESIN_PUBLIC_HOST` / `RESIN_PUBLIC_PORT` | 客户端实际要连的地址 |
| `PIN_LISTEN` | sidecar 监听，默认 `0.0.0.0:2270` |
| `PIN_STATE_PATH` | 节点 hash 到平台名的映射 |
| `PIN_SYNC_INTERVAL_SECONDS` | 默认 86400，启动时也会先对账一次 |
| `PIN_PULL_TOKEN` | 下游订阅密钥；不填则用 Admin Token |

## 下游订阅

三个项目填同一个目录地址即可，不必再给它们管理员账号：

```text
https://pin.example.com/pin/api/export?token=PIN_PULL_TOKEN
https://pin.example.com/pin/api/export?format=json&token=PIN_PULL_TOKEN
```

纯文本是每行一条可用代理。JSON 带 `items[].proxyUrl` / `region` / `name`。
