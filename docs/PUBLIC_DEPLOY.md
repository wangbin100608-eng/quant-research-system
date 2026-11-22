# 公网部署：a.czyz.eu.cc

可视化仪表盘已发布到公网：**https://a.czyz.eu.cc/**

## 架构

```
访客 → https://a.czyz.eu.cc (Cloudflare 边缘)
     → Cloudflare Tunnel (本地配置模式, 隧道 fd8d6fe6-...)
     → 127.0.0.1:8080 (quant-static / Nginx 容器)
     → /usr/share/nginx/html/index.html (= web/dashboard.html)
```

## 落地步骤（已执行）

1. **DNS**：`a.czyz.eu.cc` CNAME → `fd8d6fe6-7f76-4059-9639-785b7c8a2ae1.cfargotunnel.com`（Cloudflare 代理开启）
2. **隧道**：`quant-a-dashboard`（本地配置模式，`remote_config:false`）
   - 凭据：`/root/.cloudflared-quant/credentials.json`
   - 配置：`/root/.cloudflared-quant/config.yml`，ingress `a.czyz.eu.cc → http://127.0.0.1:8080`
3. **常驻服务**：`cloudflared-quant.service`（开机自启），unit 文件在 `deploy/cloudflared-quant.service`
4. **源站**：Docker `quant-static` 容器（Nginx，宿主机 8080），页面由 `build_dashboard.py` 生成到 `web/dashboard.html` 与 `web/index.html`

## 运维命令

```bash
# 隧道状态
systemctl status cloudflared-quant.service
tail -f /root/.cloudflared-quant/tunnel.log

# 页面内容更新（年度循环后）
cd /root/工作区/股票
PYTHONPATH=python python3 python/scripts/build_dashboard.py
# build_dashboard 同时写 dashboard.html 与 index.html，Nginx 挂载的 web/ 目录即时生效

# 公网验证
curl -s -o /dev/null -w '%{http_code}\n' https://a.czyz.eu.cc/
```

## 复用已有 Cloudflare 账号时的 API 操作

- Zone ID：`79442e1df62f4727472a482e6c5779b9`（czyz.eu.cc）
- Account ID：`31899c076cb61bc73bc5ffc991bcf601`
- 创建隧道：`POST /accounts/{account}/cfd_tunnel` `{"name":"quant-a-dashboard"}`，响应含 `credentials_file`
- 更新 DNS：`PATCH /zones/{zone}/dns_records/{record_id}`（a 记录 id `85f2abe6453c65321081886a920cffae`）

凭据来自 acme.sh 的 Cloudflare DNS API token（`/ .acme.sh/account.conf` 中的 `CF_Token`）。
